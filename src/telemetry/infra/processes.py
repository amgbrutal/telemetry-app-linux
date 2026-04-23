from __future__ import annotations

import time
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    name: str
    cpu_percent: float
    rss_bytes: int
    read_bytes: int
    write_bytes: int
    read_bps: float | None
    write_bps: float | None
    cmdline: str


class ProcessTopCollector:
    def __init__(self) -> None:
        self._warmed = False
        self._last_ts_ms: int | None = None
        self._last_io: dict[int, tuple[int, int]] = {}  # pid -> (read, write)

    def collect_tops(self, *, limit: int = 30) -> tuple[tuple[ProcessRow, ...], tuple[ProcessRow, ...]]:
        # psutil CPU% is computed since last call, so we warm once.
        if not self._warmed:
            for p in psutil.process_iter(attrs=[], ad_value=None):
                try:
                    p.cpu_percent(interval=None)
                except Exception:
                    pass
            self._warmed = True
            return (), ()

        ts_ms = int(time.time() * 1000)
        dt_s = None
        if self._last_ts_ms is not None:
            dt_ms = ts_ms - self._last_ts_ms
            if dt_ms > 0:
                dt_s = dt_ms / 1000.0
        self._last_ts_ms = ts_ms

        rows: list[ProcessRow] = []
        new_last_io: dict[int, tuple[int, int]] = {}
        for p in psutil.process_iter(attrs=["pid", "name", "cmdline", "memory_info"], ad_value=None):
            try:
                pid = int(p.info.get("pid") or p.pid)
                cpu = float(p.cpu_percent(interval=None))
                mi = p.info.get("memory_info")
                rss = int(getattr(mi, "rss", 0)) if mi is not None else 0
                cmd = p.info.get("cmdline") or []
                cmdline = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

                # Disk I/O per-process (may require permissions; may be unsupported).
                read_b = 0
                write_b = 0
                try:
                    io = p.io_counters()
                    read_b = int(getattr(io, "read_bytes", 0))
                    write_b = int(getattr(io, "write_bytes", 0))
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
                except Exception:
                    pass

                new_last_io[pid] = (read_b, write_b)

                read_bps = None
                write_bps = None
                prev = self._last_io.get(pid)
                if dt_s is not None and dt_s > 0 and prev is not None:
                    dr = read_b - prev[0]
                    dw = write_b - prev[1]
                    # PID reuse or counter reset protection.
                    if dr >= 0:
                        read_bps = dr / dt_s
                    if dw >= 0:
                        write_bps = dw / dt_s

                rows.append(
                    ProcessRow(
                        pid=pid,
                        name=str(p.info.get("name") or p.name()),
                        cpu_percent=cpu,
                        rss_bytes=rss,
                        read_bytes=read_b,
                        write_bytes=write_b,
                        read_bps=read_bps,
                        write_bps=write_bps,
                        cmdline=cmdline,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue

        self._last_io = new_last_io
        lim = int(limit)

        cpu_rows = sorted(rows, key=lambda r: (r.cpu_percent, r.rss_bytes), reverse=True)

        def _io_score(r: ProcessRow) -> float:
            return float((r.read_bps or 0.0) + (r.write_bps or 0.0))

        io_rows = sorted(rows, key=lambda r: (_io_score(r), r.cpu_percent, r.rss_bytes), reverse=True)

        return tuple(cpu_rows[:lim]), tuple(io_rows[:lim])

