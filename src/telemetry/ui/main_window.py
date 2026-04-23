from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from telemetry import APP_VERSION_LABEL, UPDATE_REPO, __version__
from telemetry.app.service import MonitorService, ProcessesTickResult, SystemTickResult
from telemetry.core.models import Sample
from telemetry.infra.processes import ProcessRow
from telemetry.infra.system_info import SystemInfo, get_system_info
from telemetry.ui.formatting import fmt_bytes, fmt_ts_ms
from typing import Callable
import re


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        *,
        service: MonitorService,
        interval_ms: int,
        history_path: Path | None,
        tray_enabled: bool = False,
        start_in_tray: bool = False,
    ) -> None:
        super().__init__()
        self._service = service
        self._interval_ms = int(interval_ms)
        self._history_path = history_path
        self._tray_enabled = bool(tray_enabled)
        self._start_in_tray = bool(start_in_tray)
        self._paused = False
        self._last_sample: Sample | None = None
        self._sysinfo: SystemInfo = get_system_info()

        self.setWindowTitle("Telemetry")
        self.resize(1120, 720)

        self._status = QtWidgets.QStatusBar(self)
        self.setStatusBar(self._status)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        self._tabs = QtWidgets.QTabWidget(self)
        layout.addWidget(self._tabs, 1)

        overview = QtWidgets.QWidget(self)
        o_layout = QtWidgets.QVBoxLayout(overview)

        topbar = QtWidgets.QWidget(self)
        topbar_layout = QtWidgets.QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(0, 0, 0, 0)

        self._version_label = QtWidgets.QLabel(APP_VERSION_LABEL, self)
        self._version_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._version_label.setFont(_mono_font(self._version_label))

        self._btn_check_updates = QtWidgets.QToolButton(self)
        self._btn_check_updates.setText("⟳")
        self._btn_check_updates.setToolTip("Проверить обновления")
        self._btn_check_updates.clicked.connect(self._check_updates)

        topbar_layout.addWidget(self._version_label, 0)
        topbar_layout.addWidget(self._btn_check_updates, 0)
        topbar_layout.addStretch(1)

        self._btn_settings = QtWidgets.QPushButton("Настройка", self)
        self._btn_settings.clicked.connect(self._open_settings)
        topbar_layout.addWidget(self._btn_settings, 0)

        o_layout.addWidget(topbar)

        sysinfo = QtWidgets.QWidget(self)
        sysinfo_layout = QtWidgets.QHBoxLayout(sysinfo)
        sysinfo_layout.setContentsMargins(0, 0, 0, 0)

        self._sysinfo_left = QtWidgets.QLabel(self)
        self._sysinfo_left.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._sysinfo_left.setFont(_mono_font(self._sysinfo_left))
        self._sysinfo_left.setWordWrap(True)

        self._sysinfo_right = QtWidgets.QLabel(self)
        self._sysinfo_right.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._sysinfo_right.setFont(_mono_font(self._sysinfo_right))
        self._sysinfo_right.setWordWrap(True)

        sysinfo_layout.addWidget(self._sysinfo_left, 1)
        sysinfo_layout.addWidget(self._sysinfo_right, 1)
        o_layout.addWidget(sysinfo)

        # reserved for future (we removed "Обновлено…" per request)
        self._meta = QtWidgets.QLabel(self)
        self._meta.hide()
        o_layout.addWidget(self._meta)

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

        self._tabs.addTab(overview, "Информация о системе")

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
        if self._tray_enabled and QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QtWidgets.QSystemTrayIcon(self)
            self._tray.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon))
            self._tray.setToolTip("Telemetry")
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.setContextMenu(self._build_tray_menu())
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
        self._render_sysinfo()
        self._status.showMessage("Загрузка метрик…", 1500)
        QtCore.QTimer.singleShot(50, self._request_system_tick)
        QtCore.QTimer.singleShot(250, self._request_processes_tick)
        self._last_alert_shown_ms: dict[str, int] = {}
        self._alert_cooldown_ms = 15_000
        self._last_procs_cpu: tuple[ProcessRow, ...] = ()
        self._last_procs_io: tuple[ProcessRow, ...] = ()
        self._update_job: _Job | None = None

    @QtCore.Slot()
    def _request_system_tick(self) -> None:
        if self._paused:
            return
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
        if self._paused:
            return
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
        self._last_sample = result.sample
        self._render(result.sample)
        self._update_tray_tooltip(result.sample)
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
                    "Telemetry",
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
        self._render_sysinfo()

        rows: list[tuple[str, str, str]] = []
        rows.append(("Процессор (CPU)", f"{sample.cpu.percent_total:.1f}%", ""))
        rows.append(
            (
                "Оперативная память (RAM)",
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
                label = (
                    f"Видеокарта (GPU) {idx}: {g.name}"
                    if len(sample.gpus) > 1
                    else f"Видеокарта (GPU): {g.name}"
                )
                vbits = []
                if g.utilization_gpu_percent is not None:
                    vbits.append(f"{g.utilization_gpu_percent:.0f}%")
                if g.utilization_mem_percent is not None:
                    vbits.append(f"память {g.utilization_mem_percent:.0f}%")
                if g.temperature_c is not None:
                    vbits.append(f"{g.temperature_c:.0f}°C")
                if g.fan_speed_percent is not None:
                    vbits.append(f"вент {g.fan_speed_percent:.0f}%")
                value = " ".join(vbits) if vbits else "—"

                extra_bits = []
                if g.memory_used_mib is not None and g.memory_total_mib is not None:
                    extra_bits.append(f"Видеопамять {g.memory_used_mib}/{g.memory_total_mib} MiB")
                if g.power_draw_w is not None and g.power_limit_w is not None:
                    extra_bits.append(f"Питание {g.power_draw_w:.0f}/{g.power_limit_w:.0f} Вт")
                if g.clocks_graphics_mhz is not None and g.clocks_mem_mhz is not None:
                    extra_bits.append(f"Частоты {g.clocks_graphics_mhz}/{g.clocks_mem_mhz} МГц")
                if g.pstate is not None:
                    extra_bits.append(f"Режим {g.pstate}")
                extra = "   ".join(extra_bits)
                rows.append((label, value, extra))
        else:
            rows.append(("Видеокарта (GPU)", "—", "нет данных (проверь драйвер и nvidia-smi)"))
        if sample.temps:
            for t in sample.temps[:12]:
                rows.append((f"Температура: {_pretty_temp_label(t.label)}", f"{t.current_c:.1f}°C", ""))
        else:
            rows.append(("Температуры", "—", "psutil не видит sensors (проверь lm-sensors)"))

        self._table.setRowCount(len(rows))
        for i, (k, v, extra) in enumerate(rows):
            self._table.setItem(i, 0, _item(k))
            self._table.setItem(i, 1, _item(v))
            self._table.setItem(i, 2, _item(extra))

    def _render_sysinfo(self) -> None:
        cpu_line = f"{self._sysinfo.cpu} ({self._sysinfo.cpu_cores} потоков)"
        mem_line = fmt_bytes(self._sysinfo.mem_total_bytes)
        gpu_line = "—"
        if self._last_sample is not None and self._last_sample.gpus:
            gpu_line = self._last_sample.gpus[0].name
        left = (
            f"Хост: {self._sysinfo.hostname}\n"
            f"ОС: {self._sysinfo.os_pretty}\n"
            f"Ядро: {self._sysinfo.kernel}"
        )
        right = (
            f"Процессор (CPU): {cpu_line}\n"
            f"Видеокарта (GPU): {gpu_line}\n"
            f"Оперативная память (RAM, всего): {mem_line}"
        )
        self._sysinfo_left.setText(left)
        self._sysinfo_right.setText(right)

    @QtCore.Slot()
    def _open_settings(self) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Настройки")
        dlg.resize(520, 180)
        layout = QtWidgets.QVBoxLayout(dlg)

        history = str(self._history_path) if self._history_path is not None else "выключено"
        lbl = QtWidgets.QLabel(f"История: {history}", dlg)
        lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(lbl)

        btn = QtWidgets.QPushButton("Закрыть", dlg)
        btn.clicked.connect(dlg.accept)
        layout.addWidget(btn, 0, QtCore.Qt.AlignmentFlag.AlignRight)

        dlg.exec()

    @QtCore.Slot()
    def _check_updates(self) -> None:
        if self._update_job is not None:
            self._status.showMessage("Проверка обновлений уже выполняется…", 2000)
            return

        self._status.showMessage("Проверяю обновления…", 2000)
        self._btn_check_updates.setEnabled(False)

        def _run() -> tuple[str, str] | None:
            if not UPDATE_REPO:
                return None
            url = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
            try:
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # GitHub returns 404 both for "no releases yet" and "not found".
                if int(getattr(e, "code", 0) or 0) == 404:
                    return (__version__, "")
                raise
            tag = str(data.get("tag_name") or "").strip()
            if tag.startswith("v") or tag.startswith("V"):
                tag = tag[1:]
            return (__version__, tag)

        job = _Job(_run, parent=self)
        self._update_job = job
        job.signals.ok.connect(self._on_update_check_done)
        job.signals.err.connect(lambda m: self._on_tick_error("Обновления", m))
        job.signals.done.connect(self._finish_update_job)
        self._pool.start(job)

    @QtCore.Slot(object)
    def _on_update_check_done(self, res: object) -> None:
        if not res:
            self._status.showMessage("Проверка обновлений не настроена.", 4000)
            return
        cur, latest = res
        if not latest:
            self._status.showMessage("Релизов пока нет (или не удалось получить тег).", 4000)
            return
        if str(latest) != str(cur):
            self._status.showMessage(f"Доступна новая версия: {latest} (текущая {cur})", 8000)
        else:
            self._status.showMessage(f"У вас актуальная версия: {cur}", 4000)

    def _finish_update_job(self) -> None:
        self._update_job = None
        self._btn_check_updates.setEnabled(True)

    def should_start_hidden(self) -> bool:
        return bool(self._start_in_tray and self._tray is not None)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        if self._tray is not None:
            self.hide()
            self._status.showMessage("Свернуто в трей.", 2000)
            event.ignore()
            return
        super().closeEvent(event)

    def _build_tray_menu(self) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(self)

        self._tray_action_toggle = QtGui.QAction("Открыть", self)
        self._tray_action_toggle.triggered.connect(self._toggle_window)
        menu.addAction(self._tray_action_toggle)

        self._tray_action_pause = QtGui.QAction("Пауза", self)
        self._tray_action_pause.setCheckable(True)
        self._tray_action_pause.triggered.connect(self._toggle_pause)
        menu.addAction(self._tray_action_pause)

        menu.addSeparator()

        quit_action = QtGui.QAction("Выход", self)
        quit_action.triggered.connect(QtWidgets.QApplication.quit)
        menu.addAction(quit_action)

        return menu

    @QtCore.Slot()
    def _toggle_window(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        self._sync_tray_action_labels()

    @QtCore.Slot()
    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._sys_timer.stop()
            self._procs_timer.stop()
            self._status.showMessage("Пауза.", 2000)
        else:
            self._sys_timer.start()
            self._procs_timer.start()
            self._status.showMessage("Продолжено.", 2000)
            self._request_system_tick()
            self._request_processes_tick()
        self._sync_tray_action_labels()

    def _sync_tray_action_labels(self) -> None:
        if self._tray is None:
            return
        if self.isVisible() and not self.isMinimized():
            self._tray_action_toggle.setText("Скрыть")
        else:
            self._tray_action_toggle.setText("Открыть")
        self._tray_action_pause.blockSignals(True)
        self._tray_action_pause.setChecked(self._paused)
        self._tray_action_pause.blockSignals(False)
        self._tray_action_pause.setText("Продолжить" if self._paused else "Пауза")

    @QtCore.Slot(QtWidgets.QSystemTrayIcon.ActivationReason)
    def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_window()

    def _update_tray_tooltip(self, sample: Sample) -> None:
        if self._tray is None:
            return
        cpu = int(round(sample.cpu.percent_total))
        ram = int(round(sample.mem.percent))

        gpu_part = "GPU —"
        if sample.gpus:
            g = sample.gpus[0]
            util = f"{int(round(g.utilization_gpu_percent))}%" if g.utilization_gpu_percent is not None else "—"
            temp = f"{int(round(g.temperature_c))}°C" if g.temperature_c is not None else "—"
            gpu_part = f"GPU {util} {temp}"

        text = f"CPU {cpu}% | RAM {ram}% | {gpu_part}"
        if self._paused:
            text = f"[Пауза] {text}"
        self._tray.setToolTip(text)
        self._sync_tray_action_labels()

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
    if hasattr(window, "should_start_hidden") and getattr(window, "should_start_hidden")():
        window.hide()
    else:
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


def _fmt_duration(seconds: int) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}д {hours:02d}:{mins:02d}:{secs:02d}"
    return f"{hours:02d}:{mins:02d}:{secs:02d}"


def _pretty_temp_label(label: str) -> str:
    """
    Приводит системные имена датчиков (lm-sensors) к более понятному виду.
    Примеры:
    - Composite -> Композитная (NVMe)
    - Tccd1 -> CCD 1 (AMD)
    - Tctl -> Контроль (Tctl)
    - Package id 0 -> Пакет CPU 0
    """
    s = (label or "").strip()
    if not s:
        return "—"

    low = s.lower()
    mapping = {
        "composite": "Композитная",
        "edge": "Край (Edge)",
        "junction": "Переход (Junction)",
        "hot spot": "Горячая точка (Hotspot)",
        "tctl": "Контроль (Tctl)",
        "tdie": "Кристалл (Tdie)",
        "cpu package": "Пакет CPU",
        "package": "Пакет",
    }
    if low in mapping:
        return mapping[low]

    m = re.fullmatch(r"tccd(\d+)", low)
    if m:
        return f"CCD {m.group(1)}"

    # e.g. "Package id 0"
    m = re.fullmatch(r"package id (\d+)", low)
    if m:
        return f"Пакет CPU {m.group(1)}"

    # e.g. "temp1", "temp2" -> "Датчик 1"
    m = re.fullmatch(r"temp(\d+)", low)
    if m:
        return f"Датчик {m.group(1)}"

    # SSD/NVMe often: "Sensor 1" / "Sensor 2"
    m = re.fullmatch(r"sensor (\d+)", low)
    if m:
        return f"Сенсор {m.group(1)}"

    return s

