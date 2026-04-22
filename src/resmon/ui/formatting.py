from __future__ import annotations

from datetime import datetime, timezone


def fmt_bytes(n: int) -> str:
    n = int(n)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    v = float(n)
    for u in units:
        if abs(v) < 1024.0 or u == units[-1]:
            return f"{v:.1f} {u}" if u != "B" else f"{int(v)} {u}"
        v /= 1024.0
    return f"{v:.1f} TiB"


def fmt_ts_ms(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone()
    return dt.strftime("%H:%M:%S")

