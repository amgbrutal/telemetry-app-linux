from __future__ import annotations

import os
import platform
import socket
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class SystemInfo:
    hostname: str
    os_pretty: str
    kernel: str
    cpu: str
    cpu_cores: int
    mem_total_bytes: int
    boot_time_ts: float


def get_system_info() -> SystemInfo:
    hostname = socket.gethostname()

    os_pretty = _linux_pretty_name() or platform.platform()
    kernel = platform.release()

    cpu = platform.processor() or ""
    if not cpu:
        cpu = _read_first_line("/proc/cpuinfo", "model name") or "CPU"

    cores = psutil.cpu_count(logical=True) or 0
    mem_total = int(psutil.virtual_memory().total)
    boot_time = float(psutil.boot_time())

    return SystemInfo(
        hostname=hostname,
        os_pretty=os_pretty,
        kernel=kernel,
        cpu=cpu,
        cpu_cores=int(cores),
        mem_total_bytes=mem_total,
        boot_time_ts=boot_time,
    )


def uptime_seconds(boot_time_ts: float) -> int:
    import time

    return max(0, int(time.time() - boot_time_ts))


def _linux_pretty_name() -> str:
    path = "/etc/os-release"
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    v = line.split("=", 1)[1].strip().strip("\n").strip('"')
                    return v
    except Exception:
        return ""
    return ""


def _read_first_line(path: str, key: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                if k.strip().lower() == key.lower():
                    return v.strip()
    except Exception:
        return ""
    return ""

