from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from resmon.app.service import MonitorService, ProcessesTickResult, SystemTickResult
from resmon.core.models import Sample
from resmon.infra.processes import ProcessRow
from resmon.ui.formatting import fmt_bytes, fmt_ts_ms
from typing import Callable


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        *,
        service: MonitorService,
        interval_ms: int,
        history_path: Path | None,
    ) -> None:
        super().__init__()
        self._service = service
        self._interval_ms = int(interval_ms)
        self._history_path = history_path

        self.setWindowTitle("resmon")
        self.resize(820, 520)

        self._status = QtWidgets.QStatusBar(self)
        self.setStatusBar(self._status)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        self._tabs = QtWidgets.QTabWidget(self)
        layout.addWidget(self._tabs, 1)

        overview = QtWidgets.QWidget(self)
        o_layout = QtWidgets.QVBoxLayout(overview)

        self._top = QtWidgets.QLabel(self)
        self._top.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._top.setFont(_mono_font(self._top))
        o_layout.addWidget(self._top)

        self._table = QtWidgets.QTableWidget(self)
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Метрика", "Значение", "Дополнительно"])
        self._table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        o_layout.addWidget(self._table, 1)

        self._tabs.addTab(overview, "Обзор")

        procs = QtWidgets.QWidget(self)
        p_layout = QtWidgets.QVBoxLayout(procs)
        self._procs_hint = QtWidgets.QLabel("Топ процессов (обновляется автоматически).", self)
        self._procs_hint.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        p_layout.addWidget(self._procs_hint)

        self._procs_mode = QtWidgets.QComboBox(self)
        self._procs_mode.addItem("Топ по CPU", "cpu")
        self._procs_mode.addItem("Топ по Disk I/O (R/s+W/s)", "io")
        self._procs_mode.currentIndexChanged.connect(lambda _: self._refresh_procs_view())
        p_layout.addWidget(self._procs_mode)

        self._procs_filter = QtWidgets.QLineEdit(self)
        self._procs_filter.setPlaceholderText("Фильтр (имя/команда). Например: firefox или steam")
        self._procs_filter.textChanged.connect(lambda _: self._apply_procs_filter())
        p_layout.addWidget(self._procs_filter)

        self._procs_table = QtWidgets.QTableWidget(self)
        self._procs_table.setColumnCount(7)
        self._procs_table.setHorizontalHeaderLabels(["PID", "Имя", "CPU%", "RSS", "R/s", "W/s", "Команда"])
        self._procs_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._procs_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._procs_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._procs_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._procs_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._procs_table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._procs_table.horizontalHeader().setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._procs_table.verticalHeader().setVisible(False)
        self._procs_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._procs_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._procs_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._procs_table.setSortingEnabled(True)
        p_layout.addWidget(self._procs_table, 1)

        self._tabs.addTab(procs, "Процессы")

        self._tray = None
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QtWidgets.QSystemTrayIcon(self)
            self._tray.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon))
            self._tray.setToolTip("resmon")
            self._tray.setVisible(True)

        self._pool = QtCore.QThreadPool.globalInstance()
        self._sys_inflight = False
        self._procs_inflight = False
        self._sys_job: _Job | None = None
        self._procs_job: _Job | None = None

        self._sys_timer = QtCore.QTimer(self)
        self._sys_timer.setInterval(self._interval_ms)
        self._sys_timer.timeout.connect(self._request_system_tick)
        self._sys_timer.start()

        self._procs_timer = QtCore.QTimer(self)
        self._procs_timer.setInterval(max(2000, self._interval_ms))
        self._procs_timer.timeout.connect(self._request_processes_tick)
        self._procs_timer.start()

        # Let the window show first, then populate UI.
        self._top.setText("Загрузка метрик…")
        QtCore.QTimer.singleShot(50, self._request_system_tick)
        QtCore.QTimer.singleShot(250, self._request_processes_tick)
        self._last_alert_shown_ms: dict[str, int] = {}
        self._alert_cooldown_ms = 15_000
        self._last_procs_cpu: tuple[ProcessRow, ...] = ()
        self._last_procs_io: tuple[ProcessRow, ...] = ()

    @QtCore.Slot()
    def _request_system_tick(self) -> None:
        if self._sys_inflight:
            return
        self._sys_inflight = True
        job = _Job(lambda: self._service.tick_system(), parent=self)
        self._sys_job = job  # keep alive until done (prevents PySide GC crashes)
        job.signals.ok.connect(self._on_system_tick)
        job.signals.err.connect(lambda m: self._on_tick_error("Система", m))
        job.signals.done.connect(lambda: self._finish_job("sys"))
        self._pool.start(job)

    @QtCore.Slot()
    def _request_processes_tick(self) -> None:
        if self._procs_inflight:
            return
        self._procs_inflight = True
        job = _Job(lambda: self._service.tick_processes(limit=30), parent=self)
        self._procs_job = job  # keep alive until done
        job.signals.ok.connect(self._on_processes_tick)
        job.signals.err.connect(lambda m: self._on_tick_error("Процессы", m))
        job.signals.done.connect(lambda: self._finish_job("procs"))
        self._pool.start(job)

    @QtCore.Slot(object)
    def _on_system_tick(self, result: SystemTickResult) -> None:
        self._render(result.sample)
        self._handle_alerts(result)

    @QtCore.Slot(object)
    def _on_processes_tick(self, result: ProcessesTickResult) -> None:
        self._last_procs_cpu = result.top_processes_cpu
        self._last_procs_io = result.top_processes_io
        self._refresh_procs_view()

    def _handle_alerts(self, result: SystemTickResult) -> None:
        if not result.alerts:
            return
        now = result.sample.ts_ms
        to_show = []
        for a in result.alerts:
            last = self._last_alert_shown_ms.get(a.key, 0)
            if now - last >= self._alert_cooldown_ms:
                self._last_alert_shown_ms[a.key] = now
                to_show.append(a)
        if to_show:
            msg = " · ".join(a.message for a in to_show[:3])
            self._status.showMessage(msg, 8000)
            if self._tray is not None:
                self._tray.showMessage(
                    "resmon",
                    msg,
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                    8000,
                )

    def _on_tick_error(self, part: str, msg: str) -> None:
        self._status.showMessage(f"{part}: {msg}", 5000)

    def _finish_job(self, which: str) -> None:
        self._set_inflight(which, False)
        if which == "sys":
            self._sys_job = None
        else:
            self._procs_job = None

    def _set_inflight(self, which: str, v: bool) -> None:
        if which == "sys":
            self._sys_inflight = v
        else:
            self._procs_inflight = v

    def _render(self, sample: Sample) -> None:
        header = f"Обновлено: {fmt_ts_ms(sample.ts_ms)}"
        if self._history_path is not None:
            header += f"    История: {self._history_path}"
        self._top.setText(header)

        rows: list[tuple[str, str, str]] = []
        rows.append(("CPU", f"{sample.cpu.percent_total:.1f}%", ""))
        rows.append(
            (
                "RAM",
                f"{sample.mem.percent:.1f}%",
                f"{fmt_bytes(sample.mem.used_bytes)} / {fmt_bytes(sample.mem.total_bytes)}",
            )
        )
        for d in sample.disks:
            rows.append(
                (
                    f"Диск {d.mountpoint}",
                    f"{d.percent:.1f}%",
                    f"{fmt_bytes(d.used_bytes)} / {fmt_bytes(d.total_bytes)} (free {fmt_bytes(d.free_bytes)})",
                )
            )
        net_extra = f"↑ {fmt_bytes(sample.net.bytes_sent)}   ↓ {fmt_bytes(sample.net.bytes_recv)}"
        if sample.net.rate_sent_bps is not None and sample.net.rate_recv_bps is not None:
            net_extra += f"    (скорость ↑ {fmt_bytes(int(sample.net.rate_sent_bps))}/s ↓ {fmt_bytes(int(sample.net.rate_recv_bps))}/s)"
        rows.append(("Сеть", "", net_extra))

        io_extra = f"R {fmt_bytes(sample.disk_io.read_bytes)}   W {fmt_bytes(sample.disk_io.write_bytes)}"
        if sample.disk_io.read_bps is not None and sample.disk_io.write_bps is not None:
            io_extra += f"    (скорость R {fmt_bytes(int(sample.disk_io.read_bps))}/s W {fmt_bytes(int(sample.disk_io.write_bps))}/s)"
        rows.append(("Диск I/O (всего)", "", io_extra))
        if sample.gpus:
            for idx, g in enumerate(sample.gpus):
                label = f"GPU{idx} {g.name}"
                vbits = []
                if g.utilization_gpu_percent is not None:
                    vbits.append(f"{g.utilization_gpu_percent:.0f}%")
                if g.utilization_mem_percent is not None:
                    vbits.append(f"mem {g.utilization_mem_percent:.0f}%")
                if g.temperature_c is not None:
                    vbits.append(f"{g.temperature_c:.0f}°C")
                if g.fan_speed_percent is not None:
                    vbits.append(f"fan {g.fan_speed_percent:.0f}%")
                value = " ".join(vbits) if vbits else "—"

                extra_bits = []
                if g.memory_used_mib is not None and g.memory_total_mib is not None:
                    extra_bits.append(f"VRAM {g.memory_used_mib}/{g.memory_total_mib} MiB")
                if g.power_draw_w is not None and g.power_limit_w is not None:
                    extra_bits.append(f"P {g.power_draw_w:.0f}/{g.power_limit_w:.0f} W")
                if g.clocks_graphics_mhz is not None and g.clocks_mem_mhz is not None:
                    extra_bits.append(f"clk {g.clocks_graphics_mhz}/{g.clocks_mem_mhz} MHz")
                if g.pstate is not None:
                    extra_bits.append(f"{g.pstate}")
                extra = "   ".join(extra_bits)
                rows.append((label, value, extra))
        else:
            rows.append(("GPU (NVIDIA)", "—", "нет данных (проверь драйвер и nvidia-smi)"))
        if sample.temps:
            for t in sample.temps[:12]:
                rows.append((f"Temp {t.label}", f"{t.current_c:.1f}°C", ""))
        else:
            rows.append(("Температуры", "—", "psutil не видит sensors (проверь lm-sensors)"))

        self._table.setRowCount(len(rows))
        for i, (k, v, extra) in enumerate(rows):
            self._table.setItem(i, 0, _item(k))
            self._table.setItem(i, 1, _item(v))
            self._table.setItem(i, 2, _item(extra))

    def _render_procs(self, rows: tuple[ProcessRow, ...]) -> None:
        if not rows:
            self._procs_table.setRowCount(0)
            return

        filt = self._procs_filter.text().strip().lower()
        if filt:
            rows = tuple(
                r
                for r in rows
                if filt in r.name.lower() or filt in (r.cmdline or "").lower()
            )

        # Disable sorting while we rewrite the model.
        sorting = self._procs_table.isSortingEnabled()
        if sorting:
            self._procs_table.setSortingEnabled(False)

        self._procs_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._procs_table.setItem(i, 0, _item(str(r.pid)))
            self._procs_table.setItem(i, 1, _item(r.name))
            self._procs_table.setItem(i, 2, _num_item(r.cpu_percent, f"{r.cpu_percent:.1f}"))
            self._procs_table.setItem(i, 3, _num_item(float(r.rss_bytes), fmt_bytes(r.rss_bytes)))
            rbps = r.read_bps if r.read_bps is not None else 0.0
            wbps = r.write_bps if r.write_bps is not None else 0.0
            self._procs_table.setItem(i, 4, _num_item(rbps, f"{fmt_bytes(int(rbps))}/s" if r.read_bps is not None else "—"))
            self._procs_table.setItem(i, 5, _num_item(wbps, f"{fmt_bytes(int(wbps))}/s" if r.write_bps is not None else "—"))
            self._procs_table.setItem(i, 6, _item(r.cmdline))

        if sorting:
            self._procs_table.setSortingEnabled(True)

    def _refresh_procs_view(self) -> None:
        mode = str(self._procs_mode.currentData() or "cpu")
        if mode == "io":
            self._render_procs(self._last_procs_io)
        else:
            self._render_procs(self._last_procs_cpu)

    def _apply_procs_filter(self) -> None:
        # Re-rendering will re-apply filter on next tick anyway; do quick hide/show now.
        filt = self._procs_filter.text().strip().lower()
        for row in range(self._procs_table.rowCount()):
            name = (self._procs_table.item(row, 1).text() if self._procs_table.item(row, 1) else "").lower()
            cmd = (self._procs_table.item(row, 6).text() if self._procs_table.item(row, 6) else "").lower()
            show = (not filt) or (filt in name) or (filt in cmd)
            self._procs_table.setRowHidden(row, not show)


def run_qt(create_window: Callable[[], QtWidgets.QMainWindow]) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_window()
    window.show()
    return app.exec()


def _mono_font(widget: QtWidgets.QWidget) -> QtGui.QFont:
    return QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)


def _item(text: str) -> QtWidgets.QTableWidgetItem:
    it = QtWidgets.QTableWidgetItem(text)
    it.setFlags(it.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
    return it


def _num_item(value: float, text: str) -> QtWidgets.QTableWidgetItem:
    it = _NumericItem(text)
    it.setData(QtCore.Qt.ItemDataRole.UserRole, float(value))
    it.setFlags(it.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
    return it


class _NumericItem(QtWidgets.QTableWidgetItem):
    def __lt__(self, other: QtWidgets.QTableWidgetItem) -> bool:
        a = self.data(QtCore.Qt.ItemDataRole.UserRole)
        b = other.data(QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a) < float(b)
        return super().__lt__(other)


class _JobSignals(QtCore.QObject):
    ok = QtCore.Signal(object)
    err = QtCore.Signal(str)
    done = QtCore.Signal()


class _Job(QtCore.QRunnable):
    def __init__(self, fn, *, parent: QtCore.QObject | None = None) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _JobSignals(parent)
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            res = self.fn()
            self.signals.ok.emit(res)
        except Exception as e:
            self.signals.err.emit(str(e))
        finally:
            self.signals.done.emit()

