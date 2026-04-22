from __future__ import annotations

from dataclasses import dataclass

from resmon.core.alerts import ThresholdRule
from resmon.core.models import Alert, Sample
from resmon.infra.collectors import SystemCollector
from resmon.infra.history import JsonlHistorySink
from resmon.infra.processes import ProcessRow, ProcessTopCollector


@dataclass(frozen=True)
class SystemTickResult:
    sample: Sample
    alerts: tuple[Alert, ...]


@dataclass(frozen=True)
class ProcessesTickResult:
    top_processes_cpu: tuple[ProcessRow, ...]
    top_processes_io: tuple[ProcessRow, ...]


class MonitorService:
    def __init__(
        self,
        *,
        collector: SystemCollector,
        rules: tuple[ThresholdRule, ...],
        history: JsonlHistorySink | None = None,
        proc_top: ProcessTopCollector | None = None,
    ) -> None:
        self._collector = collector
        self._rules = rules
        self._history = history
        self._proc_top = proc_top

    def tick_system(self) -> SystemTickResult:
        sample = self._collector.collect()

        alerts: list[Alert] = []
        for r in self._rules:
            a = r.evaluate(sample)
            if a is not None:
                alerts.append(a)

        if self._history is not None:
            self._history.append_sample(sample)
            for a in alerts:
                self._history.append_alert(a)

        return SystemTickResult(sample=sample, alerts=tuple(alerts))

    def tick_processes(self, *, limit: int = 30) -> ProcessesTickResult:
        if self._proc_top is None:
            return ProcessesTickResult(top_processes_cpu=(), top_processes_io=())
        procs_cpu, procs_io = self._proc_top.collect_tops(limit=limit)
        return ProcessesTickResult(top_processes_cpu=procs_cpu, top_processes_io=procs_io)

