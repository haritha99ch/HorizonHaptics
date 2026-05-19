"""PySide6 desktop GUI - R2 / L2 settings + status bar + log tab.

Works on Windows and Linux. Settings take effect immediately (Parser reads the
same objects) and are persisted to disk on every change.
"""

import dataclasses
import logging
import queue
import socket
import sys
from functools import partial

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import preferences
from Config import BrakeSettings, GearSettings, SurfaceSettings, ThrottleSettings, TriggerMode
from worker import State, Worker

log = logging.getLogger("hh")

# Setting definitions (attr, label, lo, hi)

THROTTLE_SECTIONS = [
    ("General", [
        ("intensity", "Intensity", 0.0, 2.0),
        ("grip_loss_value", "Grip loss threshold", 0.0, 5.0),
    ]),
    ("Normal Driving - Resistance", [
        ("min_resistance", "Min strength  (0-8)", 0, 8),
        ("max_resistance", "Max strength  (0-8)", 0, 8),
        ("resistance_smoothing", "Smoothing  (0-1)", 0.0, 1.0),
        ("turn_accel_scale", "Lateral G scale", 0.0, 2.0),
        ("fwd_accel_scale", "Forward G scale", 0.0, 2.0),
        ("accel_limit", "G-force ceiling", 1.0, 50.0),
    ]),
    ("Grip Loss - Vibration", [
        ("min_vibration", "Min freq  (fallback)", 0, 255),
        ("max_vibration", "Max freq", 0, 255),
        ("vib_smoothing", "Freq smoothing  (0-1)", 0.0, 1.0),
        ("min_stiffness", "Amp at 0 G  (inverted)", 0, 255),
        ("max_stiffness", "Amp at max G  (inverted)", 0, 255),
        ("vib_mode_start", "Min throttle to enter vib", 0, 255),
    ]),
    ("Boost", [
        ("boost_resistance", "Extra resistance while boosting  (0-8)", 0, 8),
    ]),
]

BRAKE_SECTIONS = [
    ("General", [
        ("intensity", "Intensity", 0.0, 2.0),
        ("grip_loss_value", "Grip loss threshold", 0.0, 5.0),
    ]),
    ("Normal Braking - Resistance", [
        ("min_resistance", "Min strength  (0-8)", 0, 8),
        ("max_resistance", "Max strength  (0-8)", 0, 8),
        ("resistance_smoothing", "Smoothing  (0-1)", 0.0, 1.0),
    ]),
    ("Handbrake", [
        ("handbrake_strength", "Resistance level  (0-8)", 0, 8),
    ]),
    ("ABS / Grip Loss - Vibration", [
        ("abs_wall_zones", "Wall zones during ABS  (1-9)", 1, 9),
        ("min_vibration", "Min pulse freq  (light lock-up)", 0, 255),
        ("max_vibration", "Max pulse freq  (heavy lock-up)", 0, 255),
        ("vib_smoothing", "Freq smoothing  (0-1)", 0.0, 1.0),
    ]),
]

GEAR_SECTIONS = [
    ("Gear Shift - Trigger Burst", [
        ("freq", "Frequency", 0, 255),
        ("amp", "Amplitude", 0, 255),
        ("duration_ms", "Duration  (ms)", 10.0, 500.0),
    ]),
]

SURFACE_SECTIONS = [
    ("Road Surface Rumble", [
        ("freq", "Frequency", 0, 255),
        ("amp", "Amplitude", 0, 255),
    ]),
    ("Rumble Strip", [
        ("strip_freq", "Frequency", 0, 255),
        ("strip_amp", "Amplitude", 0, 255),
    ]),
    ("Collision Jolt", [
        ("collision_threshold", "Vel diff threshold  (m/s)", 0.0, 50.0),
        ("collision_freq", "Frequency", 0, 255),
        ("collision_amp", "Amplitude", 0, 255),
        ("collision_duration_ms", "Duration  (ms)", 10.0, 1000.0),
    ]),
]

THROTTLE_RANGES = {a: (lo, hi) for _, fs in THROTTLE_SECTIONS for a, _, lo, hi in fs}
BRAKE_RANGES = {a: (lo, hi) for _, fs in BRAKE_SECTIONS for a, _, lo, hi in fs}
GEAR_RANGES = {a: (lo, hi) for _, fs in GEAR_SECTIONS for a, _, lo, hi in fs}
SURFACE_RANGES = {a: (lo, hi) for _, fs in SURFACE_SECTIONS for a, _, lo, hi in fs}

_MODE_LABELS = [m.name.capitalize() for m in TriggerMode]  # Off, Resistance, Vibration

# Thread-safe log queue

_log_q: queue.SimpleQueue = queue.SimpleQueue()


class _QtLogHandler(logging.Handler):
    def emit(self, record):
        _log_q.put(self.format(record))


# Settings page

class SettingsPage(QScrollArea):
    """Scrollable settings panel for one trigger."""

    def __init__(
        self,
        prefix: str,
        settings,
        default_cls,
        sections: list,
        state: State,
    ):
        super().__init__()
        self._prefix = prefix
        self._settings = settings
        self._default_cls = default_cls
        self._sections = sections
        self._state = state
        self._spins: dict = {}  # attr -> spinbox

        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        root = QVBoxLayout(container)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # Mode
        mode_box = QGroupBox("Mode")
        mode_row = QHBoxLayout(mode_box)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(_MODE_LABELS)
        self._mode_combo.setCurrentIndex(int(settings.mode))
        self._mode_combo.setFixedWidth(160)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_combo)
        mode_row.addStretch()
        root.addWidget(mode_box)

        # Setting sections
        for section_title, fields in sections:
            group = QGroupBox(section_title)
            form = QFormLayout(group)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(6)
            for attr, label, lo, hi in fields:
                val = getattr(settings, attr)
                spin = self._make_spin(val, lo, hi)
                spin.editingFinished.connect(partial(self._on_changed, attr, spin))
                self._spins[attr] = spin
                form.addRow(label, spin)
            root.addWidget(group)

        # Reset
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setFixedHeight(32)
        reset_btn.clicked.connect(self._on_reset)
        root.addWidget(reset_btn)
        root.addStretch()

        self.setWidget(container)

    # Helpers

    @staticmethod
    def _make_spin(val, lo, hi):
        if isinstance(val, float):
            sp = QDoubleSpinBox()
            sp.setDecimals(3)
            sp.setSingleStep(0.05)
            sp.setRange(float(lo), float(hi))
            sp.setValue(val)
        else:
            sp = QSpinBox()
            sp.setRange(int(lo), int(hi))
            sp.setValue(int(val))
        sp.setFixedWidth(110)
        sp.setAlignment(Qt.AlignmentFlag.AlignRight)
        return sp

    # Slots

    def _on_mode_changed(self, index: int):
        self._settings.mode = TriggerMode(index)
        preferences.save(self._state.throttle, self._state.brake, self._state.gear, self._state.surface)
        log.info("%s mode -> %s", self._prefix.upper(), self._settings.mode.name)

    def _on_changed(self, attr: str, spin):
        new = int(spin.value()) if isinstance(spin, QSpinBox) else spin.value()
        if new == getattr(self._settings, attr):
            return
        setattr(self._settings, attr, new)
        preferences.save(self._state.throttle, self._state.brake, self._state.gear, self._state.surface)
        log.info("%s.%s = %s", self._prefix.upper(), attr, new)

    def _on_reset(self):
        defaults = self._default_cls()
        for f in dataclasses.fields(defaults):
            setattr(self._settings, f.name, getattr(defaults, f.name))
        # Refresh widgets without triggering editingFinished
        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(int(self._settings.mode))
        self._mode_combo.blockSignals(False)
        for attr, spin in self._spins.items():
            spin.blockSignals(True)
            spin.setValue(getattr(self._settings, attr))
            spin.blockSignals(False)
        preferences.save(self._state.throttle, self._state.brake, self._state.gear, self._state.surface)
        log.info("%s settings reset to defaults.", self._prefix.upper())


# Gear settings page

class GearSettingsPage(QScrollArea):
    """Gear shift burst settings - checkboxes for per-trigger enable, spinboxes for tuning."""

    def __init__(self, settings: GearSettings, state: State):
        super().__init__()
        self._settings = settings
        self._state = state
        self._spins: dict = {}

        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        root = QVBoxLayout(container)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # Enable toggles
        enable_box = QGroupBox("Enable")
        enable_form = QFormLayout(enable_box)
        enable_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        enable_form.setHorizontalSpacing(16)

        self._chk_throttle = QCheckBox()
        self._chk_throttle.setChecked(settings.enable_throttle)
        self._chk_throttle.toggled.connect(lambda v: self._on_bool("enable_throttle", v))
        enable_form.addRow("R2 - Throttle burst", self._chk_throttle)

        self._chk_brake = QCheckBox()
        self._chk_brake.setChecked(settings.enable_brake)
        self._chk_brake.toggled.connect(lambda v: self._on_bool("enable_brake", v))
        enable_form.addRow("L2 - Brake burst", self._chk_brake)

        root.addWidget(enable_box)

        # Tuning sections
        for section_title, fields in GEAR_SECTIONS:
            group = QGroupBox(section_title)
            form = QFormLayout(group)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(6)
            for attr, label, lo, hi in fields:
                val = getattr(settings, attr)
                spin = SettingsPage._make_spin(val, lo, hi)
                spin.editingFinished.connect(partial(self._on_changed, attr, spin))
                self._spins[attr] = spin
                form.addRow(label, spin)
            root.addWidget(group)

        # Reset
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setFixedHeight(32)
        reset_btn.clicked.connect(self._on_reset)
        root.addWidget(reset_btn)
        root.addStretch()

        self.setWidget(container)

    def _on_bool(self, attr: str, value: bool):
        setattr(self._settings, attr, value)
        preferences.save(self._state.throttle, self._state.brake, self._state.gear, self._state.surface)
        log.info("GEAR.%s = %s", attr, value)

    def _on_changed(self, attr: str, spin):
        new = int(spin.value()) if isinstance(spin, QSpinBox) else spin.value()
        if new == getattr(self._settings, attr):
            return
        setattr(self._settings, attr, new)
        preferences.save(self._state.throttle, self._state.brake, self._state.gear, self._state.surface)
        log.info("GEAR.%s = %s", attr, new)

    def _on_reset(self):
        defaults = GearSettings()
        for f in dataclasses.fields(defaults):
            setattr(self._settings, f.name, getattr(defaults, f.name))
        self._chk_throttle.blockSignals(True)
        self._chk_throttle.setChecked(self._settings.enable_throttle)
        self._chk_throttle.blockSignals(False)
        self._chk_brake.blockSignals(True)
        self._chk_brake.setChecked(self._settings.enable_brake)
        self._chk_brake.blockSignals(False)
        for attr, spin in self._spins.items():
            spin.blockSignals(True)
            spin.setValue(getattr(self._settings, attr))
            spin.blockSignals(False)
        preferences.save(self._state.throttle, self._state.brake, self._state.gear, self._state.surface)
        log.info("GEAR settings reset to defaults.")


# Surface & Effects settings page

class SurfaceSettingsPage(QScrollArea):
    """Surface rumble, rumble strip, and collision jolt settings."""

    def __init__(self, settings: SurfaceSettings, state: State):
        super().__init__()
        self._settings = settings
        self._state = state
        self._spins: dict = {}

        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        root = QVBoxLayout(container)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # Enable toggles
        enable_box = QGroupBox("Enable")
        enable_form = QFormLayout(enable_box)
        enable_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        enable_form.setHorizontalSpacing(16)

        self._chk_throttle = QCheckBox()
        self._chk_throttle.setChecked(settings.enable_throttle)
        self._chk_throttle.toggled.connect(lambda v: self._on_bool("enable_throttle", v))
        enable_form.addRow("R2 - Throttle surface rumble", self._chk_throttle)

        self._chk_brake = QCheckBox()
        self._chk_brake.setChecked(settings.enable_brake)
        self._chk_brake.toggled.connect(lambda v: self._on_bool("enable_brake", v))
        enable_form.addRow("L2 - Brake surface rumble", self._chk_brake)

        self._chk_collision = QCheckBox()
        self._chk_collision.setChecked(settings.enable_collision)
        self._chk_collision.toggled.connect(lambda v: self._on_bool("enable_collision", v))
        enable_form.addRow("Collision jolt", self._chk_collision)

        self._chk_steam_rumble = QCheckBox()
        self._chk_steam_rumble.setChecked(settings.allow_steam_rumble)
        self._chk_steam_rumble.toggled.connect(lambda v: self._on_bool("allow_steam_rumble", v))
        enable_form.addRow("Allow Steam rumble (L/R motors)", self._chk_steam_rumble)
        enable_form.addRow("", QLabel("Steam Input also sends rumble through these motors.\n"
                                      "Disable to suppress it."))

        root.addWidget(enable_box)

        # Tuning sections
        for section_title, fields in SURFACE_SECTIONS:
            group = QGroupBox(section_title)
            form = QFormLayout(group)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(6)
            for attr, label, lo, hi in fields:
                val = getattr(settings, attr)
                spin = SettingsPage._make_spin(val, lo, hi)
                spin.editingFinished.connect(partial(self._on_changed, attr, spin))
                self._spins[attr] = spin
                form.addRow(label, spin)
            root.addWidget(group)

        # Reset
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setFixedHeight(32)
        reset_btn.clicked.connect(self._on_reset)
        root.addWidget(reset_btn)
        root.addStretch()

        self.setWidget(container)

    def _on_bool(self, attr: str, value: bool):
        setattr(self._settings, attr, value)
        preferences.save(self._state.throttle, self._state.brake, self._state.gear, self._state.surface)
        log.info("SURFACE.%s = %s", attr, value)

    def _on_changed(self, attr: str, spin):
        new = int(spin.value()) if isinstance(spin, QSpinBox) else spin.value()
        if new == getattr(self._settings, attr):
            return
        setattr(self._settings, attr, new)
        preferences.save(self._state.throttle, self._state.brake, self._state.gear, self._state.surface)
        log.info("SURFACE.%s = %s", attr, new)

    def _on_reset(self):
        defaults = SurfaceSettings()
        for f in dataclasses.fields(defaults):
            setattr(self._settings, f.name, getattr(defaults, f.name))
        self._chk_throttle.blockSignals(True)
        self._chk_throttle.setChecked(self._settings.enable_throttle)
        self._chk_throttle.blockSignals(False)
        self._chk_brake.blockSignals(True)
        self._chk_brake.setChecked(self._settings.enable_brake)
        self._chk_brake.blockSignals(False)
        self._chk_collision.blockSignals(True)
        self._chk_collision.setChecked(self._settings.enable_collision)
        self._chk_collision.blockSignals(False)
        self._chk_steam_rumble.blockSignals(True)
        self._chk_steam_rumble.setChecked(self._settings.allow_steam_rumble)
        self._chk_steam_rumble.blockSignals(False)
        for attr, spin in self._spins.items():
            spin.blockSignals(True)
            spin.setValue(getattr(self._settings, attr))
            spin.blockSignals(False)
        preferences.save(self._state.throttle, self._state.brake, self._state.gear, self._state.surface)
        log.info("SURFACE settings reset to defaults.")


# Log page

class LogPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(9)
        self._text.setFont(mono)
        self._text.setMaximumBlockCount(2000)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._text.clear)
        btn_row.addStretch()
        btn_row.addWidget(clear_btn)

        layout.addWidget(self._text)
        layout.addLayout(btn_row)

    def append(self, msg: str):
        self._text.appendPlainText(msg)


def _get_local_ips() -> list[str]:
    """Return all non-loopback IPv4 addresses for this machine."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            if info[0] == socket.AF_INET:
                ips.add(info[4][0])
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips)


class InfoPage(QWidget):
    """Shows UDP listener address/port and live connection status."""

    def __init__(self, state: State, host: str, port: int):
        super().__init__()
        self._state = state

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Listener
        listener_box = QGroupBox("UDP Listener")
        listener_form = QFormLayout(listener_box)
        listener_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        listener_form.setHorizontalSpacing(16)
        listener_form.addRow("Host:", QLabel(host))
        listener_form.addRow("Port:", QLabel(str(port)))
        layout.addWidget(listener_box)

        # Local IPs
        ip_box = QGroupBox("Local Network IPs  (use one of these in Forza Data Out settings)")
        ip_layout = QVBoxLayout(ip_box)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        ips = _get_local_ips()
        for ip in ips:
            lbl = QLabel(f"{ip}  :  {port}")
            lbl.setFont(mono)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            ip_layout.addWidget(lbl)
        if not ips:
            ip_layout.addWidget(QLabel("Could not determine local IP"))
        layout.addWidget(ip_box)

        # Connection status
        status_box = QGroupBox("Connection Status")
        status_form = QFormLayout(status_box)
        status_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        status_form.setHorizontalSpacing(16)
        self._ds_val = QLabel("-")
        self._fh6_val = QLabel("-")
        self._src_val = QLabel("-")
        self._pkt_val = QLabel("0")
        status_form.addRow("DualSense:", self._ds_val)
        status_form.addRow("FH6 data:", self._fh6_val)
        status_form.addRow("Game source:", self._src_val)
        status_form.addRow("Packets received:", self._pkt_val)
        layout.addWidget(status_box)

        layout.addStretch()

    def refresh(self):
        with self._state.lock:
            ds_ok = self._state.ds_connected
            recv = self._state.receiving
            addr = self._state.last_addr
            pkts = self._state.pkt_count
        self._ds_val.setText("Connected" if ds_ok else "Waiting")
        self._fh6_val.setText("Receiving" if recv else "Waiting")
        self._src_val.setText(addr or "-")
        self._pkt_val.setText(str(pkts))


# Main window

class MainWindow(QMainWindow):
    def __init__(self, state: State, worker: Worker, port: int = 5300):
        super().__init__()
        self._state = state
        self._worker = worker

        self.setWindowTitle("HorizonHaptics")
        self.setMinimumSize(QSize(520, 560))
        self.resize(640, 720)

        # Tabs
        tabs = QTabWidget()
        self._log_page = LogPage()
        self._info_page = InfoPage(state, "0.0.0.0", port)
        tabs.addTab(self._info_page, "Info")
        tabs.addTab(
            SettingsPage("r2", state.throttle, ThrottleSettings, THROTTLE_SECTIONS, state),
            "R2 - Throttle",
        )
        tabs.addTab(
            SettingsPage("l2", state.brake, BrakeSettings, BRAKE_SECTIONS, state),
            "L2 - Brake",
        )
        tabs.addTab(GearSettingsPage(state.gear, state), "Gear Shift")
        tabs.addTab(SurfaceSettingsPage(state.surface, state), "Surface & Effects")
        tabs.addTab(self._log_page, "Logs")
        self.setCentralWidget(tabs)

        # Status bar
        self._status_bar = QStatusBar()
        self._status_bar.setSizeGripEnabled(True)
        self.setStatusBar(self._status_bar)
        self._ds_label = QLabel("DualSense: - Waiting")
        self._fh6_label = QLabel("FH6: - Waiting")
        self._status_bar.addWidget(self._ds_label)
        self._status_bar.addWidget(QLabel("|"))
        self._status_bar.addWidget(self._fh6_label)

        # Timers
        t_status = QTimer(self)
        t_status.timeout.connect(self._update_status)
        t_status.start(1000)

        t_log = QTimer(self)
        t_log.timeout.connect(self._poll_logs)
        t_log.start(100)

    def _update_status(self):
        with self._state.lock:
            ds_ok = self._state.ds_connected
            recv = self._state.receiving
            addr = self._state.last_addr

        self._ds_label.setText(
            "DualSense: * Connected" if ds_ok else "DualSense: - Waiting"
        )
        self._fh6_label.setText(
            f"FH6: * Receiving  {addr}" if recv else "FH6: - Waiting for packets"
        )
        self._info_page.refresh()

    def _poll_logs(self):
        try:
            while True:
                self._log_page.append(_log_q.get_nowait())
        except queue.Empty:
            pass

    def closeEvent(self, event):
        self._worker.stop()
        event.accept()


# Dark palette (optional, applied in run_qt)

def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(45,  45,  45))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Base,            QColor(30,  30,  30))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(50,  50,  50))
    p.setColor(QPalette.ColorRole.Text,            QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Button,          QColor(55,  55,  55))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(0,   120, 215))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(45,  45,  45))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(130, 130, 130))
    return p


# Entry point

def run_qt(state: State, worker: Worker, dark: bool = True, port: int = 5300):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    if dark:
        app.setPalette(_dark_palette())

    # Wire logging into the Qt log queue
    root = logging.getLogger()
    handler = _QtLogHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    window = MainWindow(state, worker, port=port)
    window.show()
    return app.exec()
