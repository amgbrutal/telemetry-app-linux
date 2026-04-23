from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from telemetry.core.models import Alert, Sample


def default_state_dir() -> Path:
    # Follow XDG base dir spec (state is best suited for time-series logs).
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return base / "telemetry"


class JsonlHistorySink:
    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        backups: int = 3,
    ) -> None:
        self._path = path
        self._max_bytes = int(max_bytes)
        self._backups = int(backups)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append_sample(self, sample: Sample) -> None:
        self._rotate_if_needed()
        self._append_json(asdict(sample))

    def append_alert(self, alert: Alert) -> None:
        self._rotate_if_needed()
        payload = {"type": "alert", **asdict(alert)}
        self._append_json(payload)

    def _append_json(self, obj: object) -> None:
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")

    def _rotate_if_needed(self) -> None:
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        if size < self._max_bytes:
            return

        for i in range(self._backups, 0, -1):
            src = self._path if i == 1 else self._path.with_name(f"{self._path.name}.{i-1}")
            dst = self._path.with_name(f"{self._path.name}.{i}")
            if src.exists():
                try:
                    src.replace(dst)
                except Exception:
                    pass

