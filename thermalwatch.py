#!/usr/bin/env python3
"""ThermalWatch — Unified Temperature & Performance Monitor for Linux
   v2: adds MSI Afterburner-style GPU Tuning tab
"""

import sys, os, math, subprocess, time, json
from datetime import datetime
from pathlib import Path
from collections import deque

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QFrame, QSizePolicy, QSlider, QTabWidget,
        QScrollArea, QMessageBox,
    )
    from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF, QRectF, pyqtSlot
    from PyQt6.QtGui import (
        QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
        QLinearGradient, QPainterPath, QPalette,
    )
    _SP = QSizePolicy.Policy
    _AL = Qt.AlignmentFlag
    _PS = Qt.PenStyle
    _CS = Qt.CursorShape
    _BS = Qt.BrushStyle
    _PC = Qt.PenCapStyle
    _RH = QPainter.RenderHint
    _FW = QFont.Weight
    _MB = Qt.MouseButton
except ImportError:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QFrame, QSizePolicy, QSlider, QTabWidget,
        QScrollArea, QMessageBox,
    )
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF, QRectF, pyqtSlot
    from PyQt5.QtGui import (
        QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
        QLinearGradient, QPainterPath, QPalette,
    )
    _SP = QSizePolicy
    _AL = Qt
    _PS = Qt
    _CS = Qt
    _BS = Qt
    _PC = Qt
    _RH = QPainter
    _FW = QFont
    _MB = Qt

import psutil

# ── NVIDIA ───────────────────────────────────────────────────────────────────
NVIDIA = False
_nvh   = None
try:
    import pynvml
    pynvml.nvmlInit()
    _nvh   = pynvml.nvmlDeviceGetHandleByIndex(0)
    NVIDIA = True
except Exception:
    pass

HISTORY = 90

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
C_OC     = QColor("#a78bfa")   # purple accent for OC features

# ── Tiny utilities ────────────────────────────────────────────────────────────
def _clamp(v, lo, hi):  return max(lo, min(hi, v))
def _bold():            return _FW.Bold if hasattr(_FW, "Bold") else QFont.Bold

def _exy(e):
    """Mouse event position → (x, y) floats, works on PyQt5 and PyQt6."""
    try:    return e.position().x(), e.position().y()
    except: return float(e.pos().x()), float(e.pos().y())

def _temp_color(t):
    if t < 60: return QColor(C_CPU)
    if t < 76: return QColor(C_WARN)
    return QColor(C_CRIT)

def _gpu_temp_color(t):
    if t < 65: return QColor(C_GPU)
    if t < 80: return QColor(C_WARN)
    return QColor(C_CRIT)

# ── Sensor reads ──────────────────────────────────────────────────────────────
def _cpu_temp() -> float:
    try:
        temps = psutil.sensors_temperatures()
        for name in ("coretemp","k10temp","zenpower","cpu_thermal","acpitz","nct6775","it8"):
            if name not in temps: continue
            entries = temps[name]
            for e in entries:
                if any(k in e.label for k in ("Package","Tctl","CPU Temp","Core 0")):
                    return e.current
            if entries: return entries[0].current
        for entries in temps.values():
            if entries: return entries[0].current
    except: pass
    for p in Path("/sys/class/hwmon").glob("hwmon*/temp1_input"):
        try: return int(p.read_text()) / 1000.0
        except: pass
    return 0.0

def _gpu_temp() -> float:
    if NVIDIA and _nvh:
        try: return float(pynvml.nvmlDeviceGetTemperature(_nvh, pynvml.NVML_TEMPERATURE_GPU))
        except: pass
    try:
        out = subprocess.check_output(
            ["nvidia-smi","--query-gpu=temperature.gpu","--format=csv,noheader,nounits"],
            timeout=2).decode().strip()
        return float(out.split("\n")[0])
    except: pass
    return 0.0

def _gpu_info() -> dict:
    d = dict(name="", util=0, mem_used=0, mem_total=0, power=0.0, fan=0, clock=0, mem_clock=0)
    if not NVIDIA or not _nvh: return d
    try:
        raw = pynvml.nvmlDeviceGetName(_nvh)
        d["name"] = raw.decode() if isinstance(raw, bytes) else raw
        ur = pynvml.nvmlDeviceGetUtilizationRates(_nvh)
        d["util"] = ur.gpu
        mem = pynvml.nvmlDeviceGetMemoryInfo(_nvh)
        d["mem_used"]  = mem.used  // 1024**2
        d["mem_total"] = mem.total // 1024**2
    except: pass
    try: d["power"] = pynvml.nvmlDeviceGetPowerUsage(_nvh) / 1000.0
    except: pass
    try: d["fan"] = pynvml.nvmlDeviceGetFanSpeed(_nvh)
    except: pass
    try:
        d["clock"]     = pynvml.nvmlDeviceGetClockInfo(_nvh, pynvml.NVML_CLOCK_GRAPHICS)
        d["mem_clock"] = pynvml.nvmlDeviceGetClockInfo(_nvh, pynvml.NVML_CLOCK_MEM)
    except: pass
    return d

def _cpu_governor() -> str:
    try: return Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text().strip()
    except: return "unknown"

def _available_governors() -> list:
    try: return Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors").read_text().split()
    except: return []

def _set_governor(gov: str):
    for p in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor"):
        try: subprocess.run(["sudo","tee",str(p)], input=gov.encode(), capture_output=True, timeout=3)
        except: pass

# ── GPU control ───────────────────────────────────────────────────────────────
_ENV = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}

def _nvset(attr: str, val) -> bool:
    r = subprocess.run(
        ["nvidia-settings","--no-load-config","-a",f"{attr}={val}"],
        capture_output=True, timeout=5, env=_ENV)
    return r.returncode == 0

def _check_coolbits() -> bool:
    r = subprocess.run(
        ["nvidia-settings","--no-load-config","-q","[fan:0]/GPUTargetFanSpeed"],
        capture_output=True, text=True, timeout=3, env=_ENV)
    return "Attribute 'GPUTargetFanSpeed'" in r.stdout

def _power_range() -> tuple:
    if NVIDIA and _nvh:
        try:
            lo, hi = pynvml.nvmlDeviceGetPowerManagementLimitConstraints(_nvh)
            return lo // 1000, hi // 1000
        except: pass
    return 50, 350

def _current_power_limit() -> int:
    if NVIDIA and _nvh:
        try: return pynvml.nvmlDeviceGetPowerManagementLimit(_nvh) // 1000
        except: pass
    return 200

def _set_power_limit(w: int):
    subprocess.run(["sudo","nvidia-smi",f"--power-limit={w}"],
                   capture_output=True, timeout=5)

def _set_core_offset(mhz: int):
    for ps in range(4):
        _nvset(f"[gpu:0]/GPUGraphicsClockOffset[{ps}]", mhz)

def _set_mem_offset(mhz: int):
    for ps in range(4):
        _nvset(f"[gpu:0]/GPUMemoryTransferRateOffset[{ps}]", mhz)

def _set_fan_manual(pct: int):
    _nvset("[gpu:0]/GPUFanControlState", 1)
    _nvset("[fan:0]/GPUTargetFanSpeed", pct)

def _set_fan_auto():
    _nvset("[gpu:0]/GPUFanControlState", 0)

# ── GPU profile persistence ───────────────────────────────────────────────────
_PROF_DIR  = Path.home() / ".config" / "thermalwatch"
_PROF_FILE = _PROF_DIR / "gpu_profiles.json"

def _save_gpu_profile(slot: int, data: dict):
    _PROF_DIR.mkdir(parents=True, exist_ok=True)
    try:    all_p = json.loads(_PROF_FILE.read_text()) if _PROF_FILE.exists() else {}
    except: all_p = {}
    all_p[str(slot)] = data
    _PROF_FILE.write_text(json.dumps(all_p, indent=2))

def _load_gpu_profiles() -> dict:
    try: return json.loads(_PROF_FILE.read_text()) if _PROF_FILE.exists() else {}
    except: return {}

# ── CPU performance profiles ──────────────────────────────────────────────────
PROFILES = {
    "silent":      {"label":"Silent",      "symbol":"◎", "desc":"Quiet & cool — minimal power",  "gov":"powersave",   "color":QColor("#56ccf2"), "pct":0.50},
    "balanced":    {"label":"Balanced",    "symbol":"◈", "desc":"Smooth everyday performance",    "gov":"schedutil",   "color":QColor("#6fcf97"), "pct":0.75},
    "performance": {"label":"Performance", "symbol":"◆", "desc":"Maximum speed — full power",     "gov":"performance", "color":QColor("#f2994a"), "pct":1.00},
}

# ── Data collector ────────────────────────────────────────────────────────────
class Collector(QThread):
    tick = pyqtSignal(dict)
    def run(self):
        psutil.cpu_percent(interval=None)
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
            except: pass
            time.sleep(1)

# ═════════════════════════════════════════════════════════════════════════════
# SHARED WIDGETS
# ═════════════════════════════════════════════════════════════════════════════

class Gauge(QWidget):
    """Animated 270° arc temperature gauge."""
    def __init__(self, label: str, color: QColor, parent=None):
        super().__init__(parent)
        self._label  = label
        self._color  = QColor(color)
        self._target = 0.0
        self._anim   = 0.0
        self.setMinimumSize(180, 180)
        self.setSizePolicy(_SP.Expanding, _SP.Expanding)
        QTimer(self, interval=16, timeout=self._step).start()

    def set_value(self, v: float, color: QColor = None):
        self._target = _clamp(v, 0, 100)
        if color: self._color = color

    def _step(self):
        d = self._target - self._anim
        if abs(d) > 0.15:
            self._anim += d * 0.14
            self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(_RH.Antialiasing)
        w, h   = self.width(), self.height()
        side   = min(w, h)
        cx, cy = w / 2, h / 2
        r      = side * 0.40
        pw     = max(9, int(r * 0.13))

        halo = QColor(self._color); halo.setAlpha(18)
        p.setPen(_PS.NoPen); p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(cx, cy), r + pw + 6, r + pw + 6)
        p.setBrush(QBrush(C_CARD))
        p.drawEllipse(QPointF(cx, cy), r + pw - 2, r + pw - 2)

        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        tp = QPen(C_BORDER, pw); tp.setCapStyle(_PC.RoundCap)
        p.setPen(tp); p.setBrush(_BS.NoBrush)
        p.drawArc(rect, 225 * 16, -270 * 16)

        frac = self._anim / 100.0
        if abs(frac) > 0:
            vp = QPen(self._color, pw); vp.setCapStyle(_PC.RoundCap)
            p.setPen(vp)
            p.drawArc(rect, 225 * 16, int(-270 * frac) * 16)

        ang = math.radians(225 + (-270 * frac))
        p.setPen(_PS.NoPen); p.setBrush(QBrush(self._color))
        p.drawEllipse(QPointF(cx + r * math.cos(ang), cy - r * math.sin(ang)), pw * 0.55, pw * 0.55)

        val_str = f"{self._anim:.0f}"
        fv = QFont("", int(r * 0.44), _bold()); fu = QFont("", int(r * 0.16)); fl = QFont("", int(r * 0.14))
        p.setFont(fv); fm = QFontMetrics(fv)
        vw, vh = fm.horizontalAdvance(val_str), fm.height()
        p.setPen(QPen(C_TEXT)); p.drawText(QPointF(cx - vw / 2, cy + vh * 0.32), val_str)
        p.setFont(fu); p.setPen(QPen(self._color))
        p.drawText(QPointF(cx + vw / 2 + 2, cy - vh * 0.12), "°C")
        p.setFont(fl); p.setPen(QPen(C_MUTED))
        fm3 = QFontMetrics(fl)
        p.drawText(QPointF(cx - fm3.horizontalAdvance(self._label) / 2, cy + vh * 0.90), self._label)
        p.end()


class Sparkline(QWidget):
    def __init__(self, label: str, color: QColor, max_val: float = 100, parent=None):
        super().__init__(parent)
        self._label = label; self._color = color; self._max = max_val
        self._data  = deque([0.0] * HISTORY, maxlen=HISTORY)
        self.setFixedHeight(72); self.setSizePolicy(_SP.Expanding, _SP.Fixed)

    def push(self, v: float): self._data.append(v); self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(_RH.Antialiasing)
        w, h = self.width(), self.height(); pad = 4; ch = h - 20
        p.fillRect(self.rect(), C_PANEL)
        p.setPen(QPen(C_BORDER, 1, _PS.DotLine))
        p.drawLine(pad, int(pad + ch * 0.5), w - pad, int(pad + ch * 0.5))
        pts = list(self._data); n = len(pts)
        xs  = (w - 2 * pad) / max(n - 1, 1)
        def xy(i, v): return pad + i * xs, pad + ch * (1 - v / self._max)
        path = QPainterPath(); path.moveTo(pad, pad + ch); path.lineTo(*xy(0, pts[0]))
        for i in range(1, n): path.lineTo(*xy(i, pts[i]))
        path.lineTo(xy(n - 1, pts[-1])[0], pad + ch); path.closeSubpath()
        grad = QLinearGradient(0, 0, 0, ch)
        c1 = QColor(self._color); c1.setAlpha(70)
        c2 = QColor(self._color); c2.setAlpha(5)
        grad.setColorAt(0, c1); grad.setColorAt(1, c2)
        p.fillPath(path, QBrush(grad))
        lp = QPainterPath(); lp.moveTo(*xy(0, pts[0]))
        for i in range(1, n): lp.lineTo(*xy(i, pts[i]))
        p.setPen(QPen(self._color, 1.5)); p.setBrush(_BS.NoBrush); p.drawPath(lp)
        lx, ly = xy(n - 1, pts[-1])
        p.setPen(_PS.NoPen); p.setBrush(QBrush(self._color)); p.drawEllipse(QPointF(lx, ly), 3, 3)
        p.setFont(QFont("", 8)); p.setPen(QPen(C_MUTED))
        if self._label: p.drawText(int(pad + 2), h - 4, self._label)
        cur_str = f"{pts[-1]:.0f}°C"; p.setPen(QPen(self._color))
        fm = QFontMetrics(p.font())
        p.drawText(int(w - pad - fm.horizontalAdvance(cur_str) - 2), h - 4, cur_str)
        p.setFont(QFont("", 7)); p.setPen(QPen(C_BORDER))
        p.drawText(int(pad + 2), int(pad + 8), f"↑{max(pts):.0f}")
        p.drawText(int(pad + 2), int(pad + ch - 2), f"↓{min(pts):.0f}")
        p.end()


class InfoRow(QWidget):
    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22); self.setSizePolicy(_SP.Expanding, _SP.Fixed)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._k = QLabel(key); self._k.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
        self._v = QLabel("—");  self._v.setStyleSheet(f"color:{C_TEXT.name()};font-size:11px;font-weight:600;")
        self._v.setAlignment(_AL.AlignRight)
        lay.addWidget(self._k); lay.addStretch(); lay.addWidget(self._v)

    def set(self, v: str): self._v.setText(v)


class UsageBar(QWidget):
    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = color; self._val = 0.0
        self.setFixedHeight(5); self.setSizePolicy(_SP.Expanding, _SP.Fixed)

    def set(self, v: float): self._val = _clamp(v, 0, 100); self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(_RH.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(_PS.NoPen); p.setBrush(QBrush(C_BORDER)); p.drawRoundedRect(0, 0, w, h, 2, 2)
        fw = int(w * self._val / 100)
        if fw > 0:
            p.setBrush(QBrush(self._color)); p.drawRoundedRect(0, 0, fw, h, 2, 2)
        p.end()


class ProfileCard(QWidget):
    clicked = pyqtSignal(str)
    def __init__(self, key: str, profile: dict, parent=None):
        super().__init__(parent)
        self._key = key; self._profile = profile; self._active = False
        self.setFixedHeight(72); self.setSizePolicy(_SP.Expanding, _SP.Fixed)
        self.setCursor(_CS.PointingHandCursor)
        lay = QHBoxLayout(self); lay.setContentsMargins(14, 0, 14, 0); lay.setSpacing(12)
        self._sym = QLabel(profile["symbol"])
        self._sym.setFont(QFont("", 22, _bold())); self._sym.setFixedSize(32, 32)
        self._sym.setAlignment(_AL.AlignCenter)
        col = QVBoxLayout(); col.setSpacing(2)
        self._ttl = QLabel(profile["label"]); self._ttl.setFont(QFont("", 11, _bold()))
        self._ttl.setStyleSheet(f"color:{C_TEXT.name()};")
        dsc = QLabel(profile["desc"]); dsc.setFont(QFont("", 9))
        dsc.setStyleSheet(f"color:{C_MUTED.name()};")
        col.addWidget(self._ttl); col.addWidget(dsc)
        lay.addWidget(self._sym); lay.addLayout(col); lay.addStretch()

    def set_active(self, v: bool):
        self._active = v; c = self._profile["color"]
        self._sym.setStyleSheet(f"color:{c.name()};") if v else self._sym.setStyleSheet(f"color:{C_MUTED.name()};")
        self._ttl.setStyleSheet(f"color:{c.name()};") if v else self._ttl.setStyleSheet(f"color:{C_TEXT.name()};")
        self.update()

    def mousePressEvent(self, _): self.clicked.emit(self._key)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(_RH.Antialiasing)
        c = self._profile["color"]
        if self._active:
            bg = QColor(c); bg.setAlpha(22)
            p.setBrush(QBrush(bg)); p.setPen(QPen(c, 1.5))
        else:
            p.setBrush(QBrush(C_CARD)); p.setPen(QPen(C_BORDER, 1))
        p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 8, 8)
        if self._active:
            p.setPen(_PS.NoPen); p.setBrush(QBrush(c))
            p.drawRoundedRect(2, 10, 4, self.height() - 20, 2, 2)
        p.end(); super().paintEvent(_)


# ── UI factories ──────────────────────────────────────────────────────────────
def _div() -> QFrame:
    f = QFrame(); f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1); f.setStyleSheet(f"background:{C_BORDER.name()};border:none;")
    return f

def _sec(text: str) -> QLabel:
    l = QLabel(text.upper()); l.setFont(QFont("", 8, _bold()))
    l.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;padding:8px 12px 3px;")
    return l

def _card() -> tuple:
    f = QFrame()
    f.setStyleSheet(f"QFrame{{background:{C_CARD.name()};border:1px solid {C_BORDER.name()};border-radius:14px;}}")
    f.setSizePolicy(_SP.Expanding, _SP.Expanding)
    lay = QVBoxLayout(f); lay.setContentsMargins(18, 16, 18, 16); lay.setSpacing(8)
    return f, lay

def _btn(text: str, accent: QColor = None, checkable: bool = False, height: int = 30) -> QPushButton:
    c = accent or C_CPU
    r, g, b = c.red(), c.green(), c.blue()
    btn = QPushButton(text); btn.setCheckable(checkable); btn.setFixedHeight(height)
    btn.setStyleSheet(f"""
        QPushButton{{background:{C_CARD.name()};border:1px solid {C_BORDER.name()};
            border-radius:6px;color:{C_TEXT.name()};font-size:11px;padding:0 14px;}}
        QPushButton:hover{{border-color:{c.name()};color:{c.name()};}}
        QPushButton:checked{{background:rgba({r},{g},{b},30);border-color:{c.name()};color:{c.name()};}}
        QPushButton:pressed{{background:{C_BORDER.name()};}}
        QPushButton:disabled{{color:{C_BORDER.name()};border-color:{C_BORDER.name()};}}
    """)
    return btn


# ═════════════════════════════════════════════════════════════════════════════
# GPU TUNING WIDGETS
# ═════════════════════════════════════════════════════════════════════════════

class MiniGraph(QWidget):
    """Compact sparkline without labels — used inside MetricRow."""
    def __init__(self, color: QColor, max_val: float = 100, parent=None):
        super().__init__(parent)
        self._color = color; self._max = max_val
        self._data  = deque([0.0] * 60, maxlen=60)
        self.setSizePolicy(_SP.Expanding, _SP.Expanding)

    def push(self, v: float): self._data.append(v); self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(_RH.Antialiasing)
        w, h = self.width(), self.height()
        pts = list(self._data); n = len(pts)
        if n < 2: p.end(); return
        xs = w / (n - 1)
        def xy(i, v): return i * xs, h * (1 - _clamp(v, 0, self._max) / self._max)
        path = QPainterPath(); path.moveTo(0, h); path.lineTo(*xy(0, pts[0]))
        for i in range(1, n): path.lineTo(*xy(i, pts[i]))
        path.lineTo(w, h); path.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        c1 = QColor(self._color); c1.setAlpha(55)
        c2 = QColor(self._color); c2.setAlpha(5)
        grad.setColorAt(0, c1); grad.setColorAt(1, c2)
        p.fillPath(path, QBrush(grad))
        lp = QPainterPath(); lp.moveTo(*xy(0, pts[0]))
        for i in range(1, n): lp.lineTo(*xy(i, pts[i]))
        p.setPen(QPen(self._color, 1.5)); p.setBrush(_BS.NoBrush); p.drawPath(lp)
        p.end()


def _slider_css(c: QColor) -> str:
    return f"""
        QSlider::groove:horizontal{{height:4px;background:{C_BORDER.name()};border-radius:2px;}}
        QSlider::sub-page:horizontal{{background:{c.name()};border-radius:2px;}}
        QSlider::add-page:horizontal{{background:{C_BORDER.name()};border-radius:2px;}}
        QSlider::handle:horizontal{{background:{c.name()};width:14px;height:14px;
            margin:-5px 0;border-radius:7px;border:2px solid {C_BG.name()};}}
        QSlider::handle:horizontal:hover{{background:#ffffff;}}
    """


class SliderControl(QWidget):
    changed = pyqtSignal(int)

    def __init__(self, label: str, unit: str, min_v: int, max_v: int,
                 step: int = 1, default: int = None,
                 color: QColor = None, needs_coolbits: bool = False, parent=None):
        super().__init__(parent)
        self._step = step; self._unit = unit; self._needs_cb = needs_coolbits
        c = color or C_GPU

        lay = QVBoxLayout(self); lay.setContentsMargins(0, 6, 0, 6); lay.setSpacing(4)

        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label); lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
        self._val_lbl = QLabel("—")
        self._val_lbl.setStyleSheet(f"color:{c.name()};font-size:11px;font-weight:700;")
        self._val_lbl.setMinimumWidth(85); self._val_lbl.setAlignment(_AL.AlignRight)
        top.addWidget(lbl); top.addStretch(); top.addWidget(self._val_lbl)
        lay.addLayout(top)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        n_ticks = (max_v - min_v) // step
        self._min_v = min_v   # must be set before connecting valueChanged
        self._slider.setMinimum(0); self._slider.setMaximum(n_ticks)
        self._slider.setStyleSheet(_slider_css(c))
        self._slider.valueChanged.connect(self._on_change)
        lay.addWidget(self._slider)

        if needs_coolbits:
            warn = QLabel("⚠  Requires Coolbits — click Setup Guide")
            warn.setStyleSheet(f"color:{C_WARN.name()};font-size:9px;")
            lay.addWidget(warn)
            self._slider.setEnabled(False)
            self._val_lbl.setStyleSheet(f"color:{C_BORDER.name()};font-size:11px;font-weight:700;")

        dv = default if default is not None else (max_v if unit == "W" else 0)
        self._slider.setValue((dv - min_v) // step)

    def _on_change(self, ticks: int):
        v = self._min_v + ticks * self._step
        sign = "+" if v > 0 else ""
        self._val_lbl.setText(f"{sign}{v} {self._unit}")
        self.changed.emit(v)

    def value(self) -> int:
        return self._min_v + self._slider.value() * self._step

    def set_value(self, v: int):
        self._slider.setValue((v - self._min_v) // self._step)

    def enable_oc(self, on: bool):
        self._slider.setEnabled(on)
        c = C_OC if on else C_BORDER
        self._val_lbl.setStyleSheet(f"color:{c.name()};font-size:11px;font-weight:700;")


class FanCurveEditor(QWidget):
    """Interactive draggable fan curve. Left-click drag = move point.
       Left-click empty area = add point. Right-click = remove point."""
    curve_changed = pyqtSignal(list)
    DEFAULT = [(0, 0), (40, 30), (60, 55), (70, 70), (85, 90), (100, 100)]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pts      = list(self.DEFAULT)
        self._drag_idx = None
        self._hover_idx = None
        self.setMinimumHeight(160); self.setSizePolicy(_SP.Expanding, _SP.Expanding)
        self.setMouseTracking(True); self.setCursor(_CS.CrossCursor)

    # ── coordinate helpers ────────────────────────────────────────────────────
    def _pads(self):  return 46, 28, 10, 12   # left, bottom, top, right

    def _to_px(self, temp: float, fan: float):
        pl, pb, pt, pr = self._pads()
        cw = self.width()  - pl - pr
        ch = self.height() - pt - pb
        return pl + (temp / 100) * cw,  pt + ch * (1 - fan / 100)

    def _from_px(self, px: float, py: float):
        pl, pb, pt, pr = self._pads()
        cw = self.width()  - pl - pr
        ch = self.height() - pt - pb
        return (_clamp((px - pl) / cw * 100, 0, 100),
                _clamp((1 - (py - pt) / ch) * 100, 0, 100))

    def _hit(self, px, py, r=12):
        for i, (t, f) in enumerate(self._pts):
            x, y = self._to_px(t, f)
            if abs(px - x) < r and abs(py - y) < r:
                return i
        return None

    def get_fan_for_temp(self, temp: float) -> float:
        pts = self._pts
        if temp <= pts[0][0]:  return pts[0][1]
        if temp >= pts[-1][0]: return pts[-1][1]
        for i in range(len(pts) - 1):
            t1, f1 = pts[i]; t2, f2 = pts[i + 1]
            if t1 <= temp <= t2:
                return f1 + (f2 - f1) * (temp - t1) / (t2 - t1)
        return 50.0

    def reset(self): self._pts = list(self.DEFAULT); self.update()

    # ── painting ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(_RH.Antialiasing)
        w, h = self.width(), self.height()
        pl, pb, pt, pr = self._pads()
        p.fillRect(self.rect(), C_CARD)

        # Grid + axis labels
        for pct in range(0, 101, 25):
            x0, gy = self._to_px(0, pct);    x1, _  = self._to_px(100, pct)
            vx, vy0 = self._to_px(pct, 100); _,  vy1 = self._to_px(pct, 0)
            p.setPen(QPen(C_BORDER, 1, _PS.DotLine))
            p.drawLine(int(x0), int(gy), int(x1), int(gy))
            p.drawLine(int(vx), int(vy0), int(vx), int(vy1))
            p.setFont(QFont("", 8)); p.setPen(QPen(C_MUTED))
            if pct > 0:
                p.drawText(int(x0) - 38, int(gy) + 4, f"{pct}%")
                p.drawText(int(vx) - 8, int(vy1) + 13, f"{pct}°")

        p.setFont(QFont("", 8)); p.setPen(QPen(C_MUTED))
        p.drawText(2, h // 2, "Fan"); p.drawText(2, h // 2 + 12, "(%)")
        tx, _ = self._to_px(50, 0)
        p.drawText(int(tx) - 32, h - 3, "Temperature (°C)")

        # Filled area under curve
        if len(self._pts) >= 2:
            bx, by = self._to_px(self._pts[0][0], 0)
            path = QPainterPath(); path.moveTo(bx, by)
            for t, f in self._pts:
                x, y = self._to_px(t, f); path.lineTo(x, y)
            ex, _ = self._to_px(self._pts[-1][0], 0)
            path.lineTo(ex, by); path.closeSubpath()
            grad = QLinearGradient(0, 0, 0, h)
            c1 = QColor(C_GPU); c1.setAlpha(45)
            c2 = QColor(C_GPU); c2.setAlpha(8)
            grad.setColorAt(0, c1); grad.setColorAt(1, c2)
            p.fillPath(path, QBrush(grad))
            lp = QPainterPath()
            x, y = self._to_px(*self._pts[0]); lp.moveTo(x, y)
            for t, f in self._pts[1:]:
                x, y = self._to_px(t, f); lp.lineTo(x, y)
            p.setPen(QPen(C_GPU, 2)); p.setBrush(_BS.NoBrush); p.drawPath(lp)

        # Control points
        for i, (t, f) in enumerate(self._pts):
            x, y   = self._to_px(t, f)
            active = (i == self._drag_idx or i == self._hover_idx)
            size   = 7 if active else 5
            if active:
                glow = QColor(C_GPU); glow.setAlpha(35)
                p.setPen(_PS.NoPen); p.setBrush(QBrush(glow))
                p.drawEllipse(QPointF(x, y), size + 5, size + 5)
            fill = C_GPU.lighter(130) if active else C_GPU
            p.setPen(QPen(C_GPU.lighter(160) if active else C_GPU, 1.5))
            p.setBrush(QBrush(fill))
            p.drawEllipse(QPointF(x, y), size, size)
            if active:
                p.setFont(QFont("", 8)); p.setPen(QPen(C_TEXT))
                p.drawText(int(x) + 9, int(y) - 4, f"{t:.0f}°C → {f:.0f}%")
        p.end()

    # ── mouse interaction ─────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        px, py = _exy(e)
        try:    right = e.button() == _MB.RightButton
        except: right = e.button() == Qt.RightButton
        if right:
            idx = self._hit(px, py)
            if idx is not None and len(self._pts) > 2:
                self._pts.pop(idx); self.curve_changed.emit(self._pts)
            self.update(); return
        idx = self._hit(px, py)
        if idx is not None:
            self._drag_idx = idx
        else:
            t, f = self._from_px(px, py)
            pt = (round(t, 1), round(f, 1))
            self._pts.append(pt); self._pts.sort(key=lambda x: x[0])
            self._drag_idx = self._pts.index(pt)
            self.curve_changed.emit(self._pts)
        self.update()

    def mouseMoveEvent(self, e):
        px, py = _exy(e)
        if self._drag_idx is not None:
            t, f = self._from_px(px, py)
            if self._drag_idx > 0:
                t = max(t, self._pts[self._drag_idx - 1][0] + 1)
            if self._drag_idx < len(self._pts) - 1:
                t = min(t, self._pts[self._drag_idx + 1][0] - 1)
            self._pts[self._drag_idx] = (round(t, 1), round(f, 1))
            self.curve_changed.emit(self._pts)
        else:
            self._hover_idx = self._hit(px, py)
        self.update()

    def mouseReleaseEvent(self, _): self._drag_idx = None


class MetricRow(QWidget):
    """One monitoring metric: label + large value + MiniGraph sparkline."""
    def __init__(self, label: str, unit: str, color: QColor, max_val: float, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedHeight(52); self.setSizePolicy(_SP.Expanding, _SP.Fixed)
        lay = QHBoxLayout(self); lay.setContentsMargins(14, 4, 14, 4); lay.setSpacing(12)

        lc = QVBoxLayout(); lc.setSpacing(0); lc.setContentsMargins(0, 0, 0, 0)
        self._lbl = QLabel(label.upper()); self._lbl.setFont(QFont("", 8))
        self._lbl.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:1px;")
        self._val = QLabel("—"); self._val.setFont(QFont("", 15, _bold()))
        self._val.setStyleSheet(f"color:{color.name()};")
        self._sub = QLabel(unit); self._sub.setFont(QFont("", 8))
        self._sub.setStyleSheet(f"color:{C_MUTED.name()};")
        lc.addWidget(self._lbl); lc.addWidget(self._val); lc.addWidget(self._sub)

        lc_w = QWidget(); lc_w.setLayout(lc); lc_w.setFixedWidth(130)
        self._graph = MiniGraph(color, max_val)
        lay.addWidget(lc_w); lay.addWidget(self._graph, 1)

    def push(self, v: float, text: str = None):
        self._graph.push(v); self._val.setText(text or f"{v:.0f}")

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self); p.setPen(QPen(C_BORDER, 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


class ProfileBar(QWidget):
    save_requested = pyqtSignal(int)
    load_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setStyleSheet(f"background:{C_PANEL.name()};border-top:1px solid {C_BORDER.name()};")
        lay = QHBoxLayout(self); lay.setContentsMargins(16, 0, 16, 0); lay.setSpacing(6)

        lbl = QLabel("PROFILES"); lbl.setFont(QFont("", 8, _bold()))
        lbl.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        lay.addWidget(lbl); lay.addSpacing(8)

        self._slots = []
        profs = _load_gpu_profiles()
        for i in range(1, 6):
            b = _btn(str(i), C_OC, checkable=True, height=28); b.setFixedWidth(36)
            b.setProperty("slot", i)
            if str(i) in profs:
                b.setStyleSheet(b.styleSheet() + f"QPushButton{{color:{C_OC.name()};border-color:{C_OC.name()};}}")
            b.clicked.connect(lambda _, idx=i: self._on_slot(idx))
            self._slots.append(b); lay.addWidget(b)

        lay.addSpacing(8)
        sv = _btn("Save", C_OC,  height=28); sv.setFixedWidth(56)
        ld = _btn("Load", C_GPU, height=28); ld.setFixedWidth(56)
        sv.clicked.connect(self._on_save); ld.clicked.connect(self._on_load)
        lay.addWidget(sv); lay.addWidget(ld); lay.addStretch()
        self._status = QLabel(""); self._status.setStyleSheet(f"color:{C_GPU.name()};font-size:10px;")
        lay.addWidget(self._status)
        self._active = None; self._sv = sv; self._ld = ld

    def _on_slot(self, idx):
        self._active = idx
        for b in self._slots: b.setChecked(b.property("slot") == idx)

    def _on_save(self):
        if self._active is None: self.flash("Select a slot first", C_WARN); return
        self.save_requested.emit(self._active)

    def _on_load(self):
        if self._active is None: self.flash("Select a slot first", C_WARN); return
        self.load_requested.emit(self._active)

    def flash(self, msg: str, color: QColor = None):
        c = color or C_GPU
        self._status.setStyleSheet(f"color:{c.name()};font-size:10px;")
        self._status.setText(msg)
        QTimer.singleShot(3000, lambda: self._status.setText(""))

    def refresh(self):
        profs = _load_gpu_profiles()
        for b in self._slots:
            i = b.property("slot")
            base_style = _btn("", C_OC, checkable=True).styleSheet()
            if str(i) in profs:
                b.setStyleSheet(base_style + f"QPushButton{{color:{C_OC.name()};border-color:{C_OC.name()};}}")


# ═════════════════════════════════════════════════════════════════════════════
# TABS
# ═════════════════════════════════════════════════════════════════════════════

class OverviewTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent); self._build()

    def _build(self):
        lay = QVBoxLayout(self); lay.setContentsMargins(24, 20, 24, 20); lay.setSpacing(16)

        tb = QHBoxLayout()
        hl = QLabel("System Overview"); hl.setFont(QFont("", 15, _bold()))
        hl.setStyleSheet(f"color:{C_TEXT.name()};")
        self._clock = QLabel(); self._clock.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
        tb.addWidget(hl); tb.addStretch(); tb.addWidget(self._clock)
        lay.addLayout(tb)

        self._gpu_name = QLabel("NVIDIA GPU — detecting…" if NVIDIA else "No NVIDIA GPU detected")
        self._gpu_name.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;")
        lay.addWidget(self._gpu_name)

        row = QHBoxLayout(); row.setSpacing(16)
        cpu_f, cl = _card()
        lb = QLabel("PROCESSOR"); lb.setFont(QFont("", 9, _bold())); lb.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        self._cpu_gauge = Gauge("CPU Temp",  C_CPU)
        self._cpu_bar   = UsageBar(C_CPU)
        self._cpu_pct   = QLabel("Usage: 0%"); self._cpu_pct.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;")
        self._cpu_spark = Sparkline("CPU temperature history", C_CPU)
        self._cpu_freq  = InfoRow("Core Frequency")
        self._cpu_gov   = InfoRow("Governor")
        for w in (lb, self._cpu_gauge, self._cpu_bar, self._cpu_pct, self._cpu_spark, self._cpu_freq, self._cpu_gov): cl.addWidget(w)

        gpu_f, gl = _card()
        lb2 = QLabel("GRAPHICS"); lb2.setFont(QFont("", 9, _bold())); lb2.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        self._gpu_gauge = Gauge("GPU Temp",  C_GPU)
        self._gpu_bar   = UsageBar(C_GPU)
        self._gpu_pct   = QLabel("Utilization: 0%"); self._gpu_pct.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;")
        self._gpu_spark = Sparkline("GPU temperature history", C_GPU)
        self._gpu_util  = InfoRow("GPU Utilization")
        self._gpu_mem   = InfoRow("VRAM Used")
        self._gpu_power = InfoRow("Power Draw")
        self._gpu_fan   = InfoRow("Fan Speed")
        self._gpu_clock = InfoRow("Core / Mem Clock")
        for w in (lb2, self._gpu_gauge, self._gpu_bar, self._gpu_pct, self._gpu_spark,
                  self._gpu_util, self._gpu_mem, self._gpu_power, self._gpu_fan, self._gpu_clock):
            gl.addWidget(w)

        row.addWidget(cpu_f); row.addWidget(gpu_f)
        lay.addLayout(row, 1)

    def on_tick(self, d: dict):
        self._clock.setText(datetime.now().strftime("%a %d %b  %H:%M:%S"))
        ct, gt, cu = d["cpu_t"], d["gpu_t"], d["cpu_pct"]
        gi, gov    = d["gpu"], d["gov"]
        self._cpu_gauge.set_value(ct, _temp_color(ct))
        self._gpu_gauge.set_value(gt, _gpu_temp_color(gt))
        self._cpu_spark.push(ct); self._gpu_spark.push(gt)
        self._cpu_bar.set(cu); self._gpu_bar.set(gi["util"])
        self._cpu_pct.setText(f"Usage: {cu:.0f}%")
        self._gpu_pct.setText(f"Utilization: {gi['util']}%")
        self._cpu_freq.set(f"{d['cpu_mhz']:.0f} MHz"); self._cpu_gov.set(gov)
        if gi["name"]: self._gpu_name.setText(gi["name"])
        self._gpu_util.set(f"{gi['util']}%")
        mp = (gi['mem_used'] / gi['mem_total'] * 100) if gi['mem_total'] else 0
        self._gpu_mem.set(f"{gi['mem_used']} / {gi['mem_total']} MB  ({mp:.0f}%)")
        self._gpu_power.set(f"{gi['power']:.1f} W")
        self._gpu_fan.set(f"{gi['fan']}%" if gi['fan'] else "N/A")
        self._gpu_clock.set(f"{gi['clock']} / {gi['mem_clock']} MHz" if gi['clock'] else "N/A")


class GPUTuningTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._coolbits = False
        self._fan_mode = "auto"
        self._pwr_lo, self._pwr_hi = _power_range()
        self._fan_timer = QTimer(self, interval=3000)
        self._fan_timer.timeout.connect(self._apply_fan_tick)
        self._build()
        QTimer.singleShot(600, self._detect_oc)

    # ── build ─────────────────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        # ── Header bar
        hdr = QWidget(); hdr.setFixedHeight(50)
        hdr.setStyleSheet(f"background:{C_PANEL.name()};border-bottom:1px solid {C_BORDER.name()};")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(20, 0, 20, 0); hl.setSpacing(10)
        title = QLabel("GPU Tuning"); title.setFont(QFont("", 13, _bold()))
        title.setStyleSheet(f"color:{C_TEXT.name()};")
        self._oc_lbl = QLabel("Checking OC capabilities…")
        self._oc_lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;")
        self._apply_btn = _btn("Apply",       C_GPU,  height=32)
        self._reset_btn = _btn("Reset",       C_WARN, height=32)
        self._guide_btn = _btn("Setup Guide", C_OC,   height=32)
        self._apply_btn.clicked.connect(self._on_apply)
        self._reset_btn.clicked.connect(self._on_reset)
        self._guide_btn.clicked.connect(self._show_guide)
        hl.addWidget(title); hl.addWidget(self._oc_lbl); hl.addStretch()
        hl.addWidget(self._guide_btn); hl.addWidget(self._reset_btn); hl.addWidget(self._apply_btn)
        outer.addWidget(hdr)

        # ── Scrollable body
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        body = QWidget(); body.setStyleSheet(f"background:{C_BG.name()};")
        blay = QVBoxLayout(body); blay.setContentsMargins(16, 16, 16, 16); blay.setSpacing(12)

        mid = QHBoxLayout(); mid.setSpacing(12)
        mid.addWidget(self._build_controls(), 0)
        mid.addWidget(self._build_monitoring(), 1)
        blay.addLayout(mid)
        blay.addWidget(self._build_fan_section())

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ── Profile bar
        self._prof_bar = ProfileBar()
        self._prof_bar.save_requested.connect(self._on_save_profile)
        self._prof_bar.load_requested.connect(self._on_load_profile)
        outer.addWidget(self._prof_bar)

    def _build_controls(self) -> QFrame:
        f, lay = _card()
        f.setFixedWidth(285); f.setSizePolicy(_SP.Fixed, _SP.Expanding)
        title = QLabel("OVERCLOCKING"); title.setFont(QFont("", 9, _bold()))
        title.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        lay.addWidget(title); lay.addWidget(_div())

        cur = _current_power_limit()
        self._power_sl = SliderControl("Power Limit", "W",
                                       self._pwr_lo, self._pwr_hi, step=5,
                                       default=cur, color=C_GPU)
        lay.addWidget(self._power_sl); lay.addWidget(_div())

        self._core_sl = SliderControl("Core Clock Offset", "MHz",
                                      -200, 300, step=5, default=0,
                                      color=C_OC, needs_coolbits=True)
        self._mem_sl  = SliderControl("Memory Clock Offset", "MHz",
                                      -500, 2000, step=25, default=0,
                                      color=C_OC, needs_coolbits=True)
        lay.addWidget(self._core_sl); lay.addWidget(self._mem_sl)
        lay.addStretch()
        return f

    def _build_monitoring(self) -> QFrame:
        f, lay = _card()
        lay.setSpacing(0); lay.setContentsMargins(0, 12, 0, 8)
        title = QLabel("LIVE MONITORING"); title.setFont(QFont("", 9, _bold()))
        title.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        hrow = QHBoxLayout(); hrow.setContentsMargins(14, 0, 14, 8); hrow.addWidget(title)
        lay.addLayout(hrow)

        self._m_clock = MetricRow("Core Clock",  "MHz",  C_OC,   3000)
        self._m_mclk  = MetricRow("Mem Clock",   "MHz",  C_GPU, 20000)
        self._m_usage = MetricRow("GPU Usage",   "%",    C_GPU,   100)
        self._m_power = MetricRow("Power Draw",  "W",    C_WARN,  300)
        self._m_temp  = MetricRow("Temperature", "°C",   C_CPU,   100)
        self._m_fan   = MetricRow("Fan Speed",   "%",    C_MUTED, 100)
        for m in (self._m_clock, self._m_mclk, self._m_usage, self._m_power, self._m_temp, self._m_fan):
            lay.addWidget(m)
        lay.addStretch()
        return f

    def _build_fan_section(self) -> QFrame:
        f, lay = _card()
        title = QLabel("FAN CURVE"); title.setFont(QFont("", 9, _bold()))
        title.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        self._fan_auto_btn   = _btn("Auto",        C_CPU,  checkable=True, height=28)
        self._fan_manual_btn = _btn("Manual Curve",C_WARN, checkable=True, height=28)
        self._fan_apply_btn  = _btn("Apply Curve", C_GPU,  height=28)
        self._fan_reset_btn  = _btn("Reset",       C_MUTED,height=28)
        self._fan_auto_btn.setChecked(True)
        self._fan_auto_btn.clicked.connect(lambda: self._set_fan_mode("auto"))
        self._fan_manual_btn.clicked.connect(lambda: self._set_fan_mode("manual"))
        self._fan_apply_btn.clicked.connect(self._apply_fan_now)
        self._fan_reset_btn.clicked.connect(lambda: (self._fan_curve.reset(),))
        fhdr = QHBoxLayout(); fhdr.setContentsMargins(0, 0, 0, 4)
        fhdr.addWidget(title); fhdr.addStretch()
        fhdr.addWidget(self._fan_auto_btn); fhdr.addWidget(self._fan_manual_btn)
        fhdr.addSpacing(8)
        fhdr.addWidget(self._fan_reset_btn); fhdr.addWidget(self._fan_apply_btn)
        lay.addLayout(fhdr); lay.addWidget(_div())
        self._fan_curve = FanCurveEditor(); self._fan_curve.setMinimumHeight(175)
        lay.addWidget(self._fan_curve, 1)
        self._fan_status = QLabel("Fan control: Auto (driver managed)")
        self._fan_status.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;")
        lay.addWidget(self._fan_status)
        return f

    # ── OC detection ──────────────────────────────────────────────────────────
    def _detect_oc(self):
        self._coolbits = _check_coolbits()
        if self._coolbits:
            self._core_sl.enable_oc(True); self._mem_sl.enable_oc(True)
            self._oc_lbl.setText("OC features available  ·  Power limit always active")
            self._oc_lbl.setStyleSheet(f"color:{C_GPU.name()};font-size:10px;")
        else:
            self._oc_lbl.setText("⚠  Fan/clock OC needs Coolbits  ·  Power limit always active")
            self._oc_lbl.setStyleSheet(f"color:{C_WARN.name()};font-size:10px;")

    # ── Fan control ───────────────────────────────────────────────────────────
    def _set_fan_mode(self, mode: str):
        if mode == "manual" and not self._coolbits:
            self._fan_status.setText("⚠  Manual fan requires Coolbits — see Setup Guide")
            self._fan_status.setStyleSheet(f"color:{C_WARN.name()};font-size:10px;")
            self._fan_manual_btn.setChecked(False); return
        self._fan_mode = mode
        self._fan_auto_btn.setChecked(mode == "auto")
        self._fan_manual_btn.setChecked(mode == "manual")
        if mode == "auto":
            _set_fan_auto(); self._fan_timer.stop()
            self._fan_status.setText("Fan control: Auto (driver managed)")
            self._fan_status.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;")
        else:
            self._fan_timer.start()
            self._fan_status.setText("Fan curve active")
            self._fan_status.setStyleSheet(f"color:{C_GPU.name()};font-size:10px;")

    def _apply_fan_now(self):
        if not self._coolbits:
            self._fan_status.setText("⚠  Requires Coolbits — see Setup Guide")
            self._fan_status.setStyleSheet(f"color:{C_WARN.name()};font-size:10px;"); return
        self._set_fan_mode("manual"); self._apply_fan_tick()

    def _apply_fan_tick(self):
        temp  = _gpu_temp()
        speed = int(self._fan_curve.get_fan_for_temp(temp))
        _set_fan_manual(speed)
        self._fan_status.setText(f"Fan curve active — {temp:.0f}°C → {speed}% target")
        self._fan_status.setStyleSheet(f"color:{C_GPU.name()};font-size:10px;")

    # ── Apply / Reset ─────────────────────────────────────────────────────────
    def _on_apply(self):
        pw = self._power_sl.value(); _set_power_limit(pw)
        msgs = [f"Power {pw}W"]
        if self._coolbits:
            co = self._core_sl.value(); mo = self._mem_sl.value()
            _set_core_offset(co); _set_mem_offset(mo)
            msgs += [f"Core {co:+d}MHz", f"Mem {mo:+d}MHz"]
        self._oc_lbl.setText("  ✓  Applied:  " + "  ·  ".join(msgs))
        self._oc_lbl.setStyleSheet(f"color:{C_GPU.name()};font-size:10px;")
        QTimer.singleShot(5000, self._detect_oc)

    def _on_reset(self):
        self._power_sl.set_value(self._pwr_hi)
        self._core_sl.set_value(0); self._mem_sl.set_value(0)
        _set_power_limit(self._pwr_hi)
        if self._coolbits: _set_core_offset(0); _set_mem_offset(0)
        _set_fan_auto(); self._fan_timer.stop()
        self._fan_mode = "auto"
        self._fan_auto_btn.setChecked(True); self._fan_manual_btn.setChecked(False)
        self._fan_status.setText("Fan control: Auto (driver managed)")
        self._fan_status.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;")
        self._oc_lbl.setText("All settings reset to defaults")
        self._oc_lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;")
        QTimer.singleShot(3000, self._detect_oc)

    # ── Profiles ──────────────────────────────────────────────────────────────
    def _current_state(self) -> dict:
        return {"power": self._power_sl.value(),
                "core_offset": self._core_sl.value(),
                "mem_offset":  self._mem_sl.value(),
                "fan_curve":   self._fan_curve._pts,
                "fan_mode":    self._fan_mode}

    def _apply_state(self, s: dict):
        self._power_sl.set_value(s.get("power", self._pwr_hi))
        self._core_sl.set_value(s.get("core_offset", 0))
        self._mem_sl.set_value(s.get("mem_offset", 0))
        if "fan_curve" in s:
            self._fan_curve._pts = [tuple(pt) for pt in s["fan_curve"]]
            self._fan_curve.update()
        self._set_fan_mode(s.get("fan_mode", "auto"))

    def _on_save_profile(self, slot: int):
        _save_gpu_profile(slot, self._current_state())
        self._prof_bar.refresh()
        self._prof_bar.flash(f"Saved to slot {slot}")

    def _on_load_profile(self, slot: int):
        p = _load_gpu_profiles()
        if str(slot) not in p:
            self._prof_bar.flash(f"Slot {slot} is empty", C_WARN); return
        self._apply_state(p[str(slot)])
        self._on_apply()
        self._prof_bar.flash(f"Loaded slot {slot}")

    # ── Live data ─────────────────────────────────────────────────────────────
    def on_tick(self, d: dict):
        gi = d["gpu"]
        self._m_clock.push(gi["clock"],    f"{gi['clock']}")
        self._m_mclk.push(gi["mem_clock"], f"{gi['mem_clock']}")
        self._m_usage.push(gi["util"],     f"{gi['util']}%")
        self._m_power.push(gi["power"],    f"{gi['power']:.0f} W")
        self._m_temp.push(d["gpu_t"],      f"{d['gpu_t']:.0f}°C")
        self._m_fan.push(gi["fan"],        f"{gi['fan']}%")

    # ── Setup guide dialog ────────────────────────────────────────────────────
    def _show_guide(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Enable Fan & OC Control  (Coolbits)")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText("""
<b>Step 1 — Create the config file:</b><br>
<pre style='background:#0d0d1a;padding:10px;border-radius:6px;font-size:12px;'>
sudo nano /etc/X11/xorg.conf.d/20-nvidia.conf
</pre>

<b>Step 2 — Paste this content:</b><br>
<pre style='background:#0d0d1a;padding:10px;border-radius:6px;font-size:12px;'>
Section "Device"
    Identifier  "GPU0"
    Driver      "nvidia"
    Option      "Coolbits" "28"
EndSection
</pre>

<b>Step 3 — Log out and log back in</b><br><br>

<b>Coolbits 28</b> = Fan control (4) + Clock offsets (8) + Voltage (16)<br><br>
<i>Power limit control works <b>without</b> Coolbits and is always available via Apply.</i>
        """)
        msg.exec()


# ═════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ThermalWatch")
        self.setMinimumSize(920, 640)
        self._profile_key  = "balanced"
        self._profile_btns = {}
        self._build_ui()
        self._start()

    def _build_ui(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{background:{C_BG.name()};color:{C_TEXT.name()};}}
            QScrollArea {{border:none;background:transparent;}}
            QScrollBar:vertical {{background:{C_PANEL.name()};width:5px;border-radius:2px;}}
            QScrollBar::handle:vertical {{background:{C_BORDER.name()};border-radius:2px;min-height:20px;}}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height:0;}}
            QMessageBox {{background:{C_CARD.name()};}}
        """)
        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{border:none;background:{C_BG.name()};}}
            QTabBar::tab {{background:{C_PANEL.name()};color:{C_MUTED.name()};
                padding:9px 24px;border:none;font-size:12px;
                border-bottom:2px solid transparent;}}
            QTabBar::tab:selected {{background:{C_BG.name()};color:{C_TEXT.name()};
                border-bottom:2px solid {C_CPU.name()};}}
            QTabBar::tab:hover:!selected {{color:{C_TEXT.name()};}}
        """)
        self._overview_tab   = OverviewTab()
        self._gpu_tuning_tab = GPUTuningTab()
        tabs.addTab(self._overview_tab,   "Overview")
        tabs.addTab(self._gpu_tuning_tab, "GPU Tuning")
        root.addWidget(tabs, 1)

    def _build_sidebar(self) -> QWidget:
        w = QWidget(); w.setFixedWidth(210)
        w.setStyleSheet(f"background:{C_PANEL.name()};border-right:1px solid {C_BORDER.name()};")
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        hdr = QWidget(); hdr.setFixedHeight(60)
        hdr.setStyleSheet(f"background:{C_PANEL.name()};")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(14, 0, 14, 0)
        brand = QLabel("Thermal<b>Watch</b>"); brand.setFont(QFont("", 13))
        brand.setStyleSheet(f"color:{C_TEXT.name()};")
        self._dot = QLabel("●"); self._dot.setStyleSheet(f"color:{C_GPU.name()};font-size:9px;")
        hl.addWidget(brand); hl.addStretch(); hl.addWidget(self._dot)
        lay.addWidget(hdr); lay.addWidget(_div())

        lay.addWidget(_sec("System"))
        self._gov_row = InfoRow("CPU Governor"); self._gov_row.setContentsMargins(12, 0, 12, 0)
        lay.addWidget(self._gov_row)
        lay.addSpacing(4); lay.addWidget(_div())

        lay.addWidget(_sec("Performance Profile"))
        for key, profile in PROFILES.items():
            btn = ProfileCard(key, profile)
            btn.clicked.connect(self._on_profile)
            btn.set_active(key == self._profile_key)
            self._profile_btns[key] = btn; lay.addWidget(btn)

        lay.addStretch()
        ver = QLabel("v2.0  ·  RTX 5070" if NVIDIA else "v2.0")
        ver.setStyleSheet(f"color:{C_MUTED.name()};font-size:9px;padding:8px 14px;")
        lay.addWidget(ver)
        return w

    def _start(self):
        self._col = Collector(); self._col.tick.connect(self._on_tick); self._col.start()
        self._blink_state = True
        QTimer(self, interval=800, timeout=self._blink).start()

    def _blink(self):
        self._blink_state = not self._blink_state
        c = C_GPU.name() if self._blink_state else C_BORDER.name()
        self._dot.setStyleSheet(f"color:{c};font-size:9px;")

    @pyqtSlot(dict)
    def _on_tick(self, d: dict):
        self._overview_tab.on_tick(d)
        self._gpu_tuning_tab.on_tick(d)
        self._gov_row.set(d["gov"])

    def _on_profile(self, key: str):
        for k, btn in self._profile_btns.items(): btn.set_active(k == key)
        self._profile_key = key; pr = PROFILES[key]
        gov = pr["gov"]; avail = _available_governors()
        if gov not in avail and avail:
            for fb in ("schedutil","ondemand","powersave","performance"):
                if fb in avail: gov = fb; break
        _set_governor(gov)
        if NVIDIA and _nvh:
            try:
                lo, hi = pynvml.nvmlDeviceGetPowerManagementLimitConstraints(_nvh)
                _set_power_limit(int(lo // 1000 + (hi // 1000 - lo // 1000) * pr["pct"]))
            except: pass

    def closeEvent(self, e):
        if hasattr(self, "_col"): self._col.requestInterruption(); self._col.wait(1000)
        super().closeEvent(e)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
    app = QApplication(sys.argv); app.setApplicationName("ThermalWatch")
    pal = QPalette()
    for role, col in (
        (QPalette.ColorRole.Window,        C_BG),
        (QPalette.ColorRole.WindowText,    C_TEXT),
        (QPalette.ColorRole.Base,          C_PANEL),
        (QPalette.ColorRole.AlternateBase, C_CARD),
        (QPalette.ColorRole.Text,          C_TEXT),
        (QPalette.ColorRole.Button,        C_CARD),
        (QPalette.ColorRole.ButtonText,    C_TEXT),
        (QPalette.ColorRole.Highlight,     C_CPU),
    ): pal.setColor(role, col)
    app.setPalette(pal)
    win = MainWindow(); win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
