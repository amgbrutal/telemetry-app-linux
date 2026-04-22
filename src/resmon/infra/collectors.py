from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass

import psutil

from resmon.core.models import (
    CpuSample,
    DiskIoSample,
    DiskSample,
    GpuSample,
    MemSample,
    NetSample,
    Sample,
    TempReading,
)


@dataclass(frozen=True)
class CollectorConfig:
    disk_mountpoints: tuple[str, ...] = ("/",)
    enable_nvidia: bool = True
    nvidia_min_interval_ms: int = 2000


class SystemCollector:
    def __init__(self, config: CollectorConfig | None = None) -> None:
        self._config = config or CollectorConfig()
        # Warm up CPU percent measurement (psutil returns 0.0 for the first call).
        psutil.cpu_percent(interval=None)
        self._last_ts_ms: int | None = None
        self._last_net: tuple[int, int] | None = None  # (sent, recv)
        self._last_diskio: tuple[int, int] | None = None  # (read, write)
        self._last_gpus_ts_ms: int | None = None
        self._last_gpus: tuple[GpuSample, ...] = ()

    def collect(self) -> Sample:
        ts_ms = int(time.time() * 1000)

        cpu = CpuSample(percent_total=float(psutil.cpu_percent(interval=None)))

        vm = psutil.virtual_memory()
        mem = MemSample(
            total_bytes=int(vm.total),
            used_bytes=int(vm.used),
            percent=float(vm.percent),
        )

        disks: list[DiskSample] = []
        for mp in self._config.disk_mountpoints:
            try:
                du = psutil.disk_usage(mp)
            except Exception:
                continue
            disks.append(
                DiskSample(
                    mountpoint=mp,
                    total_bytes=int(du.total),
                    used_bytes=int(du.used),
                    free_bytes=int(du.free),
                    percent=float(du.percent),
                )
            )

        nio = psutil.net_io_counters()
        sent = int(nio.bytes_sent)
        recv = int(nio.bytes_recv)

        dio = psutil.disk_io_counters()
        read_b = int(getattr(dio, "read_bytes", 0))
        write_b = int(getattr(dio, "write_bytes", 0))

        dt_s = None
        if self._last_ts_ms is not None:
            dt_ms = ts_ms - self._last_ts_ms
            if dt_ms > 0:
                dt_s = dt_ms / 1000.0

        rate_sent = None
        rate_recv = None
        read_bps = None
        write_bps = None
        if dt_s is not None and dt_s > 0 and self._last_net is not None and self._last_diskio is not None:
            rate_sent = max(0.0, (sent - self._last_net[0]) / dt_s)
            rate_recv = max(0.0, (recv - self._last_net[1]) / dt_s)
            read_bps = max(0.0, (read_b - self._last_diskio[0]) / dt_s)
            write_bps = max(0.0, (write_b - self._last_diskio[1]) / dt_s)

        self._last_ts_ms = ts_ms
        self._last_net = (sent, recv)
        self._last_diskio = (read_b, write_b)

        net = NetSample(
            bytes_sent=sent,
            bytes_recv=recv,
            rate_sent_bps=rate_sent,
            rate_recv_bps=rate_recv,
        )
        disk_io = DiskIoSample(
            read_bytes=read_b,
            write_bytes=write_b,
            read_bps=read_bps,
            write_bps=write_bps,
        )

        temps = _collect_temps()
        gpus = ()
        if self._config.enable_nvidia:
            gpus = self._collect_nvidia_gpus_cached(ts_ms)

        return Sample(
            ts_ms=ts_ms,
            cpu=cpu,
            mem=mem,
            disks=tuple(disks),
            net=net,
            disk_io=disk_io,
            temps=temps,
            gpus=gpus,
        )

    def _collect_nvidia_gpus_cached(self, ts_ms: int) -> tuple[GpuSample, ...]:
        last_ts = self._last_gpus_ts_ms
        if last_ts is not None:
            if ts_ms - last_ts < int(self._config.nvidia_min_interval_ms):
                return self._last_gpus

        gpus = _collect_nvidia_gpus()
        self._last_gpus_ts_ms = ts_ms
        self._last_gpus = gpus
        return gpus


def _collect_temps() -> tuple[TempReading, ...]:
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False) or {}
    except Exception:
        return ()

    out: list[TempReading] = []
    for chip_name, readings in temps.items():
        for r in readings:
            label = r.label or chip_name
            if r.current is None:
                continue
            out.append(TempReading(label=str(label), current_c=float(r.current)))
    out.sort(key=lambda x: x.label)
    return tuple(out)


def _collect_nvidia_gpus() -> tuple[GpuSample, ...]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ()

    # CSV output without headers; one line per GPU.
    # Keep it minimal and fast to avoid UI stalls.
    query = ",".join(
        [
            "name",
            "uuid",
            "utilization.gpu",
            "utilization.memory",
            "temperature.gpu",
            "fan.speed",
            "memory.total",
            "memory.used",
            "power.draw",
            "power.limit",
            "clocks.gr",
            "clocks.mem",
            "pstate",
        ]
    )
    cmd = [
        exe,
        f"--query-gpu={query}",
        "--format=csv,noheader,nounits",
    ]
    try:
        p = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=0.4,
        )
    except Exception:
        return ()
    if p.returncode != 0:
        return ()

    out: list[GpuSample] = []
    for raw_line in (p.stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 13:
            continue
        name, uuid = parts[0], parts[1]
        util = _to_float(parts[2])
        util_mem = _to_float(parts[3])
        temp = _to_float(parts[4])
        fan = _to_float(parts[5])
        mem_total = _to_int(parts[6])
        mem_used = _to_int(parts[7])
        pdraw = _to_float(parts[8])
        plim = _to_float(parts[9])
        clk_gr = _to_int(parts[10])
        clk_mem = _to_int(parts[11])
        pstate = parts[12].strip() if parts[12].strip() and parts[12].strip().lower() != "n/a" else None
        out.append(
            GpuSample(
                vendor="nvidia",
                name=name,
                uuid=uuid,
                utilization_gpu_percent=util,
                utilization_mem_percent=util_mem,
                temperature_c=temp,
                fan_speed_percent=fan,
                memory_total_mib=mem_total,
                memory_used_mib=mem_used,
                power_draw_w=pdraw,
                power_limit_w=plim,
                clocks_graphics_mhz=clk_gr,
                clocks_mem_mhz=clk_mem,
                pstate=pstate,
            )
        )
    return tuple(out)


def _to_float(s: str) -> float | None:
    s = s.strip()
    if not s or s.lower() == "n/a":
        return None
    try:
        return float(s)
    except Exception:
        return None


def _to_int(s: str) -> int | None:
    s = s.strip()
    if not s or s.lower() == "n/a":
        return None
    try:
        return int(float(s))
    except Exception:
        return None

