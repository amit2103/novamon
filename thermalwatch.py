#!/usr/bin/env python3
"""ThermalWatch — Unified Temperature & Performance Monitor for Linux"""

import sys
import os
import math
import subprocess
import time
from pathlib import Path
from collections import deque

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QFrame, QSizePolicy, QButtonGroup,
        QScrollArea, QAbstractButton,
    )
    from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF, QRectF, QSize, pyqtSlot
    from PyQt6.QtGui import (
        QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
        QLinearGradient, QConicalGradient, QPainterPath, QPalette,
    )
    _SP = QSizePolicy.Policy
    _AL = Qt.AlignmentFlag
    _PS = Qt.PenStyle
    _CS = Qt.CursorShape
    _BS = Qt.BrushStyle
    _PC = Qt.PenCapStyle
    _RH = QPainter.RenderHint
    _FW = QFont.Weight
except ImportError:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QFrame, QSizePolicy, QButtonGroup,
        QScrollArea, QAbstractButton,
    )
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF, QRectF, QSize, pyqtSlot
    from PyQt5.QtGui import (
        QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
        QLinearGradient, QConicalGradient, QPainterPath, QPalette,
    )
    _SP = QSizePolicy
    _AL = Qt
    _PS = Qt
    _CS = Qt
    _BS = Qt
    _PC = Qt
    _RH = QPainter
    _FW = QFont

import psutil

# ── NVIDIA ──────────────────────────────────────────────────────────────────
NVIDIA = False
_nvh = None
try:
    import pynvml
    pynvml.nvmlInit()
    _nvh = pynvml.nvmlDeviceGetHandleByIndex(0)
    NVIDIA = True
except Exception:
    pass

HISTORY = 90   # seconds of history kept

# ── Palette ──────────────────────────────────────────────────────────────────
C_BG     = QColor("#0b0b18")
C_PANEL  = QColor("#111124")
C_CARD   = QColor("#181830")
C_BORDER = QColor("#252545")
C_TEXT   = QColor("#dde1f5")
C_MUTED  = QColor("#7880a0")
C_CPU    = QColor("#4e9af1")
C_GPU    = QColor("#00e5a0")
C_WARN   = QColor("#ffb347")
C_CRIT   = QColor("#ff5252")
C_ACT    = QColor("#a78bfa")   # profile active accent


def _temp_color(t: float) -> QColor:
    if t < 60:
        return QColor(C_CPU)
    if t < 76:
        return QColor(C_WARN)
    return QColor(C_CRIT)


def _gpu_temp_color(t: float) -> QColor:
    if t < 65:
        return QColor(C_GPU)
    if t < 80:
        return QColor(C_WARN)
    return QColor(C_CRIT)


# ── Sensor reads ─────────────────────────────────────────────────────────────

def _cpu_temp() -> float:
    try:
        temps = psutil.sensors_temperatures()
        for name in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz", "nct6775", "it8"):
            if name not in temps:
                continue
            entries = temps[name]
            for e in entries:
                if any(k in e.label for k in ("Package", "Tctl", "CPU Temp", "Core 0")):
                    return e.current
            if entries:
                return entries[0].current
        for entries in temps.values():
            if entries:
                return entries[0].current
    except Exception:
        pass
    for p in Path("/sys/class/hwmon").glob("hwmon*/temp1_input"):
        try:
            return int(p.read_text()) / 1000.0
        except Exception:
            pass
    return 0.0


def _gpu_temp() -> float:
    if NVIDIA and _nvh:
        try:
            return float(pynvml.nvmlDeviceGetTemperature(_nvh, pynvml.NVML_TEMPERATURE_GPU))
        except Exception:
            pass
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            timeout=2,
        ).decode().strip()
        return float(out.split("\n")[0])
    except Exception:
        pass
    return 0.0


def _gpu_info() -> dict:
    d = dict(name="", util=0, mem_used=0, mem_total=0, power=0.0, fan=0, clock=0, mem_clock=0)
    if not NVIDIA or not _nvh:
        return d
    try:
        raw = pynvml.nvmlDeviceGetName(_nvh)
        d["name"] = raw.decode() if isinstance(raw, bytes) else raw
        ur = pynvml.nvmlDeviceGetUtilizationRates(_nvh)
        d["util"] = ur.gpu
        mem = pynvml.nvmlDeviceGetMemoryInfo(_nvh)
        d["mem_used"]  = mem.used  // 1024 ** 2
        d["mem_total"] = mem.total // 1024 ** 2
    except Exception:
        pass
    try:
        d["power"] = pynvml.nvmlDeviceGetPowerUsage(_nvh) / 1000.0
    except Exception:
        pass
    try:
        d["fan"] = pynvml.nvmlDeviceGetFanSpeed(_nvh)
    except Exception:
        pass
    try:
        d["clock"]     = pynvml.nvmlDeviceGetClockInfo(_nvh, pynvml.NVML_CLOCK_GRAPHICS)
        d["mem_clock"] = pynvml.nvmlDeviceGetClockInfo(_nvh, pynvml.NVML_CLOCK_MEM)
    except Exception:
        pass
    return d


def _cpu_governor() -> str:
    try:
        return Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text().strip()
    except Exception:
        return "unknown"


def _set_governor(gov: str):
    for p in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor"):
        try:
            subprocess.run(["sudo", "tee", str(p)], input=gov.encode(),
                           capture_output=True, timeout=3)
        except Exception:
            pass


def _set_nvidia_power(watts: int):
    try:
        subprocess.run(["sudo", "nvidia-smi", f"--power-limit={watts}"],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def _available_governors() -> list[str]:
    try:
        return Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors").read_text().split()
    except Exception:
        return []


# ── Performance profiles ─────────────────────────────────────────────────────

PROFILES = {
    "silent": {
        "label":   "Silent",
        "symbol":  "◎",
        "desc":    "Quiet & cool — minimal power",
        "gov":     "powersave",
        "color":   QColor("#56ccf2"),
        "pct":     0.50,
    },
    "balanced": {
        "label":   "Balanced",
        "symbol":  "◈",
        "desc":    "Smooth everyday performance",
        "gov":     "schedutil",
        "color":   QColor("#6fcf97"),
        "pct":     0.75,
    },
    "performance": {
        "label":   "Performance",
        "symbol":  "◆",
        "desc":    "Maximum speed — full power",
        "gov":     "performance",
        "color":   QColor("#f2994a"),
        "pct":     1.00,
    },
}


# ── Data collection thread ───────────────────────────────────────────────────

class Collector(QThread):
    tick = pyqtSignal(dict)

    def run(self):
        psutil.cpu_percent(interval=None)   # prime
        while not self.isInterruptionRequested():
            try:
                self.tick.emit({
                    "cpu_t":   _cpu_temp(),
                    "gpu_t":   _gpu_temp(),
                    "cpu_pct": psutil.cpu_percent(interval=None),
                    "cpu_mhz": (psutil.cpu_freq().current if psutil.cpu_freq() else 0),
                    "gov":     _cpu_governor(),
                    "gpu":     _gpu_info(),
                })
            except Exception:
                pass
            time.sleep(1)


# ── Circular gauge ───────────────────────────────────────────────────────────

class Gauge(QWidget):
    """270° arc gauge with smooth animation."""

    def __init__(self, label: str, color: QColor, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = QColor(color)
        self._target = 0.0
        self._anim   = 0.0
        self.setMinimumSize(180, 180)
        self.setSizePolicy(_SP.Expanding, _SP.Expanding)
        t = QTimer(self, interval=16)
        t.timeout.connect(self._step)
        t.start()

    def set_value(self, v: float, color: QColor = None):
        self._target = max(0.0, min(100.0, v))
        if color:
            self._color = color

    def _step(self):
        d = self._target - self._anim
        if abs(d) > 0.15:
            self._anim += d * 0.14
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(_RH.Antialiasing)
        w, h  = self.width(), self.height()
        side  = min(w, h)
        cx, cy = w / 2, h / 2
        r     = side * 0.40
        pw    = max(9, int(r * 0.13))

        # Outer glow halo (subtle)
        halo = QColor(self._color)
        halo.setAlpha(18)
        p.setPen(_PS.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(cx, cy), r + pw + 6, r + pw + 6)

        # Card circle background
        p.setBrush(QBrush(C_CARD))
        p.drawEllipse(QPointF(cx, cy), r + pw - 2, r + pw - 2)

        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        START = 225      # Qt CCW degrees from 3 o'clock; maps to bottom-left
        SWEEP = -270     # clockwise

        # Track
        tp = QPen(C_BORDER, pw)
        tp.setCapStyle(_PC.RoundCap)
        p.setPen(tp)
        p.setBrush(_BS.NoBrush)
        p.drawArc(rect, START * 16, SWEEP * 16)

        # Value arc
        frac = self._anim / 100.0
        val_span = int(SWEEP * frac)
        if abs(val_span) > 0:
            vp = QPen(self._color, pw)
            vp.setCapStyle(_PC.RoundCap)
            p.setPen(vp)
            p.drawArc(rect, START * 16, val_span * 16)

        # Tip dot
        ang = math.radians(START + (SWEEP * frac))
        tx  = cx + r * math.cos(ang)
        ty  = cy - r * math.sin(ang)
        p.setPen(_PS.NoPen)
        glc = QColor(self._color)
        glc.setAlpha(255)
        p.setBrush(QBrush(glc))
        p.drawEllipse(QPointF(tx, ty), pw * 0.55, pw * 0.55)

        # Value text
        val_str = f"{self._anim:.0f}"
        f_val   = QFont("", int(r * 0.44), _FW.Bold if hasattr(_FW, "Bold") else QFont.Bold)
        f_unit  = QFont("", int(r * 0.16))
        f_lbl   = QFont("", int(r * 0.14))

        p.setFont(f_val)
        fm = QFontMetrics(f_val)
        vw = fm.horizontalAdvance(val_str)
        vh = fm.height()

        p.setPen(QPen(C_TEXT))
        p.drawText(QPointF(cx - vw / 2, cy + vh * 0.32), val_str)

        p.setFont(f_unit)
        p.setPen(QPen(self._color))
        fm2 = QFontMetrics(f_unit)
        p.drawText(QPointF(cx + vw / 2 + 2, cy - vh * 0.12), "°C")

        p.setFont(f_lbl)
        p.setPen(QPen(C_MUTED))
        fm3 = QFontMetrics(f_lbl)
        lw  = fm3.horizontalAdvance(self._label)
        p.drawText(QPointF(cx - lw / 2, cy + vh * 0.90), self._label)

        p.end()


# ── Sparkline ────────────────────────────────────────────────────────────────

class Sparkline(QWidget):
    def __init__(self, label: str, color: QColor, max_val: float = 100, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._max   = max_val
        self._data  = deque([0.0] * HISTORY, maxlen=HISTORY)
        self.setFixedHeight(72)
        self.setSizePolicy(_SP.Expanding, _SP.Fixed)

    def push(self, v: float):
        self._data.append(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(_RH.Antialiasing)
        w, h = self.width(), self.height()
        pad  = 4
        ch   = h - 20       # chart height, leave room for labels

        p.fillRect(self.rect(), C_PANEL)

        # 50% gridline
        p.setPen(QPen(C_BORDER, 1, _PS.DotLine))
        gy = int(pad + ch * 0.5)
        p.drawLine(pad, gy, w - pad, gy)

        pts  = list(self._data)
        n    = len(pts)
        xstep = (w - 2 * pad) / max(n - 1, 1)

        def xy(i, v):
            return (pad + i * xstep, pad + ch * (1 - v / self._max))

        # Fill
        path = QPainterPath()
        x0, y0 = xy(0, pts[0])
        path.moveTo(x0, pad + ch)
        path.lineTo(x0, y0)
        for i in range(1, n):
            x, y = xy(i, pts[i])
            path.lineTo(x, y)
        path.lineTo(xy(n - 1, pts[-1])[0], pad + ch)
        path.closeSubpath()

        grad = QLinearGradient(0, pad, 0, pad + ch)
        c1 = QColor(self._color); c1.setAlpha(70)
        c2 = QColor(self._color); c2.setAlpha(5)
        grad.setColorAt(0, c1); grad.setColorAt(1, c2)
        p.fillPath(path, QBrush(grad))

        # Line
        lp = QPainterPath()
        lp.moveTo(*xy(0, pts[0]))
        for i in range(1, n):
            lp.lineTo(*xy(i, pts[i]))
        p.setPen(QPen(self._color, 1.5))
        p.setBrush(_BS.NoBrush)
        p.drawPath(lp)

        # Current value dot
        lx, ly = xy(n - 1, pts[-1])
        p.setPen(_PS.NoPen)
        p.setBrush(QBrush(self._color))
        p.drawEllipse(QPointF(lx, ly), 3, 3)

        # Labels
        p.setFont(QFont("", 8))
        p.setPen(QPen(C_MUTED))
        p.drawText(int(pad + 2), h - 4, self._label)
        cur_str = f"{pts[-1]:.0f}°C"
        p.setPen(QPen(self._color))
        fm = QFontMetrics(p.font())
        p.drawText(int(w - pad - fm.horizontalAdvance(cur_str) - 2), h - 4, cur_str)

        # Min/max labels
        hi = max(pts); lo = min(pts)
        p.setFont(QFont("", 7))
        p.setPen(QPen(C_BORDER))
        p.drawText(int(pad + 2), int(pad + 8), f"↑{hi:.0f}")
        p.drawText(int(pad + 2), int(pad + ch - 2), f"↓{lo:.0f}")
        p.end()


# ── InfoRow ──────────────────────────────────────────────────────────────────

class InfoRow(QWidget):
    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setSizePolicy(_SP.Expanding, _SP.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._key = QLabel(key)
        self._key.setStyleSheet(f"color:{C_MUTED.name()}; font-size:11px;")
        self._val = QLabel("—")
        self._val.setStyleSheet(f"color:{C_TEXT.name()}; font-size:11px; font-weight:600;")
        self._val.setAlignment(_AL.AlignRight)
        lay.addWidget(self._key)
        lay.addStretch()
        lay.addWidget(self._val)

    def set(self, v: str):
        self._val.setText(v)


# ── Profile card ─────────────────────────────────────────────────────────────

class ProfileCard(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, key: str, profile: dict, parent=None):
        super().__init__(parent)
        self._key     = key
        self._profile = profile
        self._active  = False
        self.setFixedHeight(72)
        self.setSizePolicy(_SP.Expanding, _SP.Fixed)
        self.setCursor(_CS.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(12)

        sym = QLabel(profile["symbol"])
        sym.setFont(QFont("", 22, _FW.Bold if hasattr(_FW, "Bold") else QFont.Bold))
        sym.setFixedSize(32, 32)
        sym.setAlignment(_AL.AlignCenter)

        col = QVBoxLayout(); col.setSpacing(2)
        ttl = QLabel(profile["label"])
        ttl.setFont(QFont("", 11, _FW.Bold if hasattr(_FW, "Bold") else QFont.Bold))
        ttl.setStyleSheet(f"color:{C_TEXT.name()};")
        dsc = QLabel(profile["desc"])
        dsc.setFont(QFont("", 9))
        dsc.setStyleSheet(f"color:{C_MUTED.name()};")
        col.addWidget(ttl); col.addWidget(dsc)

        lay.addWidget(sym)
        lay.addLayout(col)
        lay.addStretch()

        self._sym = sym
        self._ttl = ttl

    def set_active(self, v: bool):
        self._active = v
        c = self._profile["color"]
        if v:
            self._sym.setStyleSheet(f"color:{c.name()};")
            self._ttl.setStyleSheet(f"color:{c.name()};")
        else:
            self._sym.setStyleSheet(f"color:{C_MUTED.name()};")
            self._ttl.setStyleSheet(f"color:{C_TEXT.name()};")
        self.update()

    def mousePressEvent(self, _):
        self.clicked.emit(self._key)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(_RH.Antialiasing)
        c = self._profile["color"]
        if self._active:
            bg = QColor(c); bg.setAlpha(22)
            p.setBrush(QBrush(bg))
            p.setPen(QPen(c, 1.5))
        else:
            p.setBrush(QBrush(C_CARD))
            p.setPen(QPen(C_BORDER, 1))
        r = self.rect().adjusted(2, 2, -2, -2)
        p.drawRoundedRect(r, 8, 8)
        if self._active:
            p.setPen(_PS.NoPen)
            p.setBrush(QBrush(c))
            p.drawRoundedRect(2, 10, 4, self.height() - 20, 2, 2)
        p.end()
        super().paintEvent(_)


# ── Divider + section header helpers ─────────────────────────────────────────

def _div() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{C_BORDER.name()}; border:none;")
    return f


def _sec(text: str) -> QLabel:
    l = QLabel(text.upper())
    l.setFont(QFont("", 8, _FW.Bold if hasattr(_FW, "Bold") else QFont.Bold))
    l.setStyleSheet(f"color:{C_MUTED.name()}; letter-spacing:2px; padding:8px 12px 3px;")
    return l


# ── Usage bar (thin horizontal bar) ─────────────────────────────────────────

class UsageBar(QWidget):
    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = color
        self._value = 0.0
        self.setFixedHeight(5)
        self.setSizePolicy(_SP.Expanding, _SP.Fixed)

    def set(self, v: float):
        self._value = max(0.0, min(100.0, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(_RH.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(_PS.NoPen)
        p.setBrush(QBrush(C_BORDER))
        p.drawRoundedRect(0, 0, w, h, 2, 2)
        fw = int(w * self._value / 100)
        if fw > 0:
            p.setBrush(QBrush(self._color))
            p.drawRoundedRect(0, 0, fw, h, 2, 2)
        p.end()


# ── Card factory ─────────────────────────────────────────────────────────────

def _card() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setStyleSheet(f"""
        QFrame {{
            background:{C_CARD.name()};
            border:1px solid {C_BORDER.name()};
            border-radius:14px;
        }}
    """)
    f.setSizePolicy(_SP.Expanding, _SP.Expanding)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(8)
    return f, lay


# ── Main window ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ThermalWatch")
        self.setMinimumSize(880, 620)
        self._profile_key = "balanced"
        self._profile_btns: dict[str, ProfileCard] = {}
        self._build_ui()
        self._start()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background:{C_BG.name()};
                color:{C_TEXT.name()};
            }}
            QScrollArea {{ border:none; background:transparent; }}
            QScrollBar:vertical {{
                background:{C_PANEL.name()}; width:5px; border-radius:2px;
            }}
            QScrollBar::handle:vertical {{
                background:{C_BORDER.name()}; border-radius:2px; min-height:20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar())
        root.addWidget(self._content(), 1)

    def _sidebar(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(210)
        w.setStyleSheet(f"background:{C_PANEL.name()}; border-right:1px solid {C_BORDER.name()};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── Logo header
        hdr = QWidget(); hdr.setFixedHeight(60)
        hdr.setStyleSheet(f"background:{C_PANEL.name()};")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(14, 0, 14, 0)
        brand = QLabel("Thermal<b>Watch</b>")
        brand.setFont(QFont("", 13))
        brand.setStyleSheet(f"color:{C_TEXT.name()};")
        self._live_dot = QLabel("●")
        self._live_dot.setStyleSheet(f"color:{C_GPU.name()}; font-size:9px;")
        hl.addWidget(brand)
        hl.addStretch()
        hl.addWidget(self._live_dot)
        lay.addWidget(hdr)
        lay.addWidget(_div())

        # ── Status
        lay.addWidget(_sec("System"))
        self._gov_row = InfoRow("CPU Governor")
        self._gov_row.setContentsMargins(12, 0, 12, 0)
        lay.addWidget(self._gov_row)
        lay.addSpacing(4)
        lay.addWidget(_div())

        # ── Profiles
        lay.addWidget(_sec("Performance Profile"))
        for key, profile in PROFILES.items():
            btn = ProfileCard(key, profile)
            btn.clicked.connect(self._on_profile)
            btn.set_active(key == self._profile_key)
            self._profile_btns[key] = btn
            lay.addWidget(btn)

        lay.addStretch()

        # ── Version footer
        ver = QLabel("v1.0  ·  RTX 5070" if NVIDIA else "v1.0")
        ver.setStyleSheet(f"color:{C_MUTED.name()}; font-size:9px; padding:8px 14px;")
        lay.addWidget(ver)

        return w

    def _content(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        # ── Top bar
        tb = QHBoxLayout()
        self._headline = QLabel("System Overview")
        self._headline.setFont(QFont("", 15, _FW.Bold if hasattr(_FW, "Bold") else QFont.Bold))
        self._headline.setStyleSheet(f"color:{C_TEXT.name()};")
        self._clock = QLabel()
        self._clock.setStyleSheet(f"color:{C_MUTED.name()}; font-size:11px;")
        tb.addWidget(self._headline)
        tb.addStretch()
        tb.addWidget(self._clock)
        lay.addLayout(tb)

        # ── GPU name
        self._gpu_name = QLabel("NVIDIA GPU — detecting…" if NVIDIA else "No NVIDIA GPU detected")
        self._gpu_name.setStyleSheet(f"color:{C_MUTED.name()}; font-size:10px;")
        lay.addWidget(self._gpu_name)

        # ── Gauge row
        row = QHBoxLayout(); row.setSpacing(16)

        # CPU card
        cpu_f, cpu_l = _card()
        cpu_lbl = QLabel("PROCESSOR")
        cpu_lbl.setFont(QFont("", 9, _FW.Bold if hasattr(_FW, "Bold") else QFont.Bold))
        cpu_lbl.setStyleSheet(f"color:{C_MUTED.name()}; letter-spacing:2px;")
        self._cpu_gauge = Gauge("CPU Temp", C_CPU)
        self._cpu_bar   = UsageBar(C_CPU)
        self._cpu_pct   = QLabel("Usage: 0%")
        self._cpu_pct.setStyleSheet(f"color:{C_MUTED.name()}; font-size:10px;")
        self._cpu_spark = Sparkline("CPU temperature history", C_CPU)
        self._cpu_freq  = InfoRow("Core Frequency")
        self._cpu_gov2  = InfoRow("Governor")
        for w2 in (cpu_lbl, self._cpu_gauge, self._cpu_bar, self._cpu_pct,
                   self._cpu_spark, self._cpu_freq, self._cpu_gov2):
            cpu_l.addWidget(w2)

        # GPU card
        gpu_f, gpu_l = _card()
        gpu_lbl = QLabel("GRAPHICS")
        gpu_lbl.setFont(QFont("", 9, _FW.Bold if hasattr(_FW, "Bold") else QFont.Bold))
        gpu_lbl.setStyleSheet(f"color:{C_MUTED.name()}; letter-spacing:2px;")
        self._gpu_gauge  = Gauge("GPU Temp", C_GPU)
        self._gpu_bar    = UsageBar(C_GPU)
        self._gpu_pct    = QLabel("Utilization: 0%")
        self._gpu_pct.setStyleSheet(f"color:{C_MUTED.name()}; font-size:10px;")
        self._gpu_spark  = Sparkline("GPU temperature history", C_GPU)
        self._gpu_util   = InfoRow("GPU Utilization")
        self._gpu_mem    = InfoRow("VRAM Used")
        self._gpu_power  = InfoRow("Power Draw")
        self._gpu_fan    = InfoRow("Fan Speed")
        self._gpu_clock  = InfoRow("Core / Mem Clock")
        for w2 in (gpu_lbl, self._gpu_gauge, self._gpu_bar, self._gpu_pct,
                   self._gpu_spark, self._gpu_util, self._gpu_mem,
                   self._gpu_power, self._gpu_fan, self._gpu_clock):
            gpu_l.addWidget(w2)

        row.addWidget(cpu_f)
        row.addWidget(gpu_f)
        lay.addLayout(row, 1)

        return w

    # ── Data wiring ──────────────────────────────────────────────────────────

    def _start(self):
        self._col = Collector()
        self._col.tick.connect(self._on_tick)
        self._col.start()
        t = QTimer(self, interval=1000)
        t.timeout.connect(self._tick_clock)
        t.start()
        self._tick_clock()
        # blink timer for live dot
        self._blink = True
        bt = QTimer(self, interval=800)
        bt.timeout.connect(self._blink_dot)
        bt.start()

    def _blink_dot(self):
        self._blink = not self._blink
        c = C_GPU.name() if self._blink else C_BORDER.name()
        self._live_dot.setStyleSheet(f"color:{c}; font-size:9px;")

    def _tick_clock(self):
        from datetime import datetime
        self._clock.setText(datetime.now().strftime("%a %d %b  %H:%M:%S"))

    @pyqtSlot(dict)
    def _on_tick(self, d: dict):
        ct = d["cpu_t"]
        gt = d["gpu_t"]
        cu = d["cpu_pct"]
        cf = d["cpu_mhz"]
        gov = d["gov"]
        gi  = d["gpu"]

        self._cpu_gauge.set_value(ct, _temp_color(ct))
        self._gpu_gauge.set_value(gt, _gpu_temp_color(gt))
        self._cpu_spark.push(ct)
        self._gpu_spark.push(gt)

        self._cpu_bar.set(cu)
        self._gpu_bar.set(gi["util"])
        self._cpu_pct.setText(f"Usage: {cu:.0f}%")
        self._gpu_pct.setText(f"Utilization: {gi['util']}%")

        self._cpu_freq.set(f"{cf:.0f} MHz")
        self._cpu_gov2.set(gov)
        self._gov_row.set(gov)

        if gi["name"]:
            self._gpu_name.setText(gi["name"])

        self._gpu_util.set(f"{gi['util']}%")
        mem_pct = (gi['mem_used'] / gi['mem_total'] * 100) if gi['mem_total'] else 0
        self._gpu_mem.set(f"{gi['mem_used']} / {gi['mem_total']} MB  ({mem_pct:.0f}%)")
        self._gpu_power.set(f"{gi['power']:.1f} W")
        self._gpu_fan.set(f"{gi['fan']}%" if gi['fan'] else "N/A")
        self._gpu_clock.set(
            f"{gi['clock']} MHz / {gi['mem_clock']} MHz" if gi['clock'] else "N/A"
        )

    # ── Profile switching ─────────────────────────────────────────────────────

    def _on_profile(self, key: str):
        for k, btn in self._profile_btns.items():
            btn.set_active(k == key)
        self._profile_key = key
        p = PROFILES[key]

        # Governor
        gov = p["gov"]
        avail = _available_governors()
        if gov not in avail and avail:
            # fallback order
            for fallback in ("schedutil", "ondemand", "powersave", "performance"):
                if fallback in avail:
                    gov = fallback
                    break
        _set_governor(gov)

        # NVIDIA power limit
        if NVIDIA and _nvh:
            try:
                lo, hi = pynvml.nvmlDeviceGetPowerManagementLimitConstraints(_nvh)
                lo //= 1000; hi //= 1000
                target = int(lo + (hi - lo) * p["pct"])
                _set_nvidia_power(target)
            except Exception:
                pass

    def closeEvent(self, e):
        if hasattr(self, "_col"):
            self._col.requestInterruption()
            self._col.wait(1000)
        super().closeEvent(e)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
    app = QApplication(sys.argv)
    app.setApplicationName("ThermalWatch")

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,      C_BG)
    pal.setColor(QPalette.ColorRole.WindowText,  C_TEXT)
    pal.setColor(QPalette.ColorRole.Base,        C_PANEL)
    pal.setColor(QPalette.ColorRole.AlternateBase, C_CARD)
    pal.setColor(QPalette.ColorRole.Text,        C_TEXT)
    pal.setColor(QPalette.ColorRole.Button,      C_CARD)
    pal.setColor(QPalette.ColorRole.ButtonText,  C_TEXT)
    pal.setColor(QPalette.ColorRole.Highlight,   C_CPU)
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
