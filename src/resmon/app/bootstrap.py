from __future__ import annotations

import argparse
from pathlib import Path

from resmon.app.service import MonitorService
from resmon.core.alerts import ThresholdRule
from resmon.infra.collectors import CollectorConfig, SystemCollector
from resmon.infra.history import JsonlHistorySink, default_state_dir
from resmon.infra.processes import ProcessTopCollector
from resmon.ui.main_window import MainWindow, run_qt


def run(argv: list[str]) -> int:
    args = _parse_args(argv)

    collector = SystemCollector(
        CollectorConfig(disk_mountpoints=tuple(args.disk_mountpoints))
    )
    rules = (
        ThresholdRule(
            key="cpu_high",
            metric="cpu.percent_total",
            op=">=",
            threshold=float(args.alert_cpu),
            severity="warning",
            message="CPU высокая: {value:.1f}% (порог {threshold:.1f}%)",
        ),
        ThresholdRule(
            key="mem_high",
            metric="mem.percent",
            op=">=",
            threshold=float(args.alert_mem),
            severity="warning",
            message="RAM высокая: {value:.1f}% (порог {threshold:.1f}%)",
        ),
    )

    history = None
    if not args.no_history:
        history_path = Path(args.history_path) if args.history_path else default_state_dir() / "history.jsonl"
        history = JsonlHistorySink(history_path)

    service = MonitorService(
        collector=collector,
        rules=rules,
        history=history,
        proc_top=ProcessTopCollector(),
    )
    return run_qt(
        lambda: MainWindow(
            service=service,
            interval_ms=int(args.interval_ms),
            history_path=history.path if history else None,
        )
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="resmon")
    p.add_argument("--interval-ms", type=int, default=1000, help="Период обновления.")
    p.add_argument(
        "--disk-mountpoint",
        dest="disk_mountpoints",
        action="append",
        default=[],
        help="Точка монтирования диска для мониторинга (можно повторять).",
    )
    p.add_argument("--alert-cpu", type=float, default=90.0, help="Порог CPU в %%.")
    p.add_argument("--alert-mem", type=float, default=90.0, help="Порог RAM в %%.")
    p.add_argument("--no-history", action="store_true", help="Не писать историю в файл.")
    p.add_argument("--history-path", type=str, default="", help="Путь к history.jsonl.")
    ns = p.parse_args(argv)
    if not ns.disk_mountpoints:
        ns.disk_mountpoints = ["/"]
    return ns

