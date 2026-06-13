#!/usr/bin/env python3
"""NovaMon — Unified Temperature, Performance & Process Monitor for Linux"""

import sys, os, math, subprocess, time, json, signal
from datetime import datetime
from pathlib import Path
from collections import deque

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QFrame, QSizePolicy, QSlider, QTabWidget,
        QScrollArea, QMessageBox, QTableView, QTableWidget, QTableWidgetItem,
        QLineEdit, QHeaderView, QAbstractItemView, QStyledItemDelegate,
        QSystemTrayIcon, QMenu,
    )
    from PyQt6.QtCore import (
        Qt, QTimer, QThread, pyqtSignal, QPointF, QRectF, pyqtSlot,
        QAbstractTableModel, QSortFilterProxyModel, QModelIndex,
    )
    from PyQt6.QtGui import (
        QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
        QLinearGradient, QPainterPath, QPalette,
        QIcon, QPixmap, QRadialGradient, QAction,
    )
    _SP   = QSizePolicy.Policy
    _AL   = Qt.AlignmentFlag
    _PS   = Qt.PenStyle
    _CS   = Qt.CursorShape
    _BS   = Qt.BrushStyle
    _PC   = Qt.PenCapStyle
    _RH   = QPainter.RenderHint
    _FW   = QFont.Weight
    _MB   = Qt.MouseButton
    _DR   = Qt.ItemDataRole
    _OR   = Qt.Orientation
    _SO   = Qt.SortOrder
    _SEL  = QAbstractItemView.SelectionBehavior
    _SELM = QAbstractItemView.SelectionMode
    _ET   = QAbstractItemView.EditTrigger
    _HRM  = QHeaderView.ResizeMode
    _TRAY = QSystemTrayIcon.ActivationReason
except ImportError:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QFrame, QSizePolicy, QSlider, QTabWidget,
        QScrollArea, QMessageBox, QTableView, QTableWidget, QTableWidgetItem,
        QLineEdit, QHeaderView, QAbstractItemView, QStyledItemDelegate,
        QSystemTrayIcon, QMenu, QAction,
    )
    from PyQt5.QtCore import (
        Qt, QTimer, QThread, pyqtSignal, QPointF, QRectF, pyqtSlot,
        QAbstractTableModel, QSortFilterProxyModel, QModelIndex,
    )
    from PyQt5.QtGui import (
        QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
        QLinearGradient, QPainterPath, QPalette,
        QIcon, QPixmap, QRadialGradient,
    )
    _SP   = QSizePolicy
    _AL   = Qt
    _PS   = Qt
    _CS   = Qt
    _BS   = Qt
    _PC   = Qt
    _RH   = QPainter
    _FW   = QFont
    _MB   = Qt
    _DR   = Qt
    _OR   = Qt
    _SO   = Qt
    _SEL  = QAbstractItemView
    _SELM = QAbstractItemView
    _ET   = QAbstractItemView
    _HRM  = QHeaderView
    _TRAY = QSystemTrayIcon

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

# ── App icon ──────────────────────────────────────────────────────────────────
def _draw_icon(size: int) -> "QPixmap":
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm); p.setRenderHint(_RH.Antialiasing)
    s = size

    # Rounded square background
    bg = QLinearGradient(0, 0, 0, s)
    bg.setColorAt(0, QColor("#1a1a3e")); bg.setColorAt(1, QColor("#0b0b18"))
    p.setPen(_PS.NoPen); p.setBrush(QBrush(bg))
    p.drawRoundedRect(0, 0, s, s, s * 0.14, s * 0.14)

    # Monitor frame
    marg = s * 0.06; mon_w = s * 0.88; mon_h = s * 0.66
    mx = (s - mon_w) / 2; my = marg
    p.setBrush(QBrush(QColor("#1c1c3a")))
    p.setPen(QPen(C_CPU, max(1.0, s / 48.0)))
    p.drawRoundedRect(int(mx), int(my), int(mon_w), int(mon_h), s * 0.06, s * 0.06)

    # Screen
    sp = s * 0.055
    sx, sy = mx + sp, my + sp
    sw, sh = mon_w - 2 * sp, mon_h - 2 * sp
    p.setPen(_PS.NoPen); p.setBrush(QBrush(QColor("#060612")))
    p.drawRoundedRect(int(sx), int(sy), int(sw), int(sh), s * 0.03, s * 0.03)

    # Waveforms (only at sizes large enough to be visible)
    if size >= 24:
        n = max(10, size // 3)
        # CPU waveform — upper half, blue
        cy1 = sy + sh * 0.32
        path1 = QPainterPath()
        for i in range(n + 1):
            t = i / n
            x = sx + t * sw
            y = cy1 - math.sin(t * math.pi * 3.2) * sh * 0.13
            if i == 0: path1.moveTo(x, y)
            else:      path1.lineTo(x, y)
        p.setPen(QPen(C_CPU, max(1.0, s / 56.0))); p.setBrush(_BS.NoBrush)
        p.drawPath(path1)

        # GPU waveform — lower half, teal
        cy2 = sy + sh * 0.68
        path2 = QPainterPath()
        for i in range(n + 1):
            t = i / n
            x = sx + t * sw
            y = cy2 - math.sin(t * math.pi * 2.4 + 1.1) * sh * 0.12
            if i == 0: path2.moveTo(x, y)
            else:      path2.lineTo(x, y)
        p.setPen(QPen(C_GPU, max(1.0, s / 56.0)))
        p.drawPath(path2)

    # Live indicator dot (top-right of screen)
    dr = max(1.5, s * 0.042)
    dx = sx + sw - dr * 2.8; dy = sy + dr * 2.2
    glow = QRadialGradient(dx, dy, dr * 3)
    gc = QColor(C_GPU); gc.setAlpha(0)
    glow.setColorAt(0, C_GPU); glow.setColorAt(0.45, C_GPU); glow.setColorAt(1, gc)
    p.setPen(_PS.NoPen); p.setBrush(QBrush(glow))
    p.drawEllipse(QPointF(dx, dy), dr * 3, dr * 3)
    p.setBrush(QBrush(C_GPU))
    p.drawEllipse(QPointF(dx, dy), dr, dr)

    # Stand (neck + base) for larger sizes
    if size >= 32:
        nw = s * 0.09; nh = s * 0.09
        nx = (s - nw) / 2; ny = my + mon_h
        p.setBrush(QBrush(QColor("#1c1c3a"))); p.setPen(_PS.NoPen)
        p.drawRect(int(nx), int(ny), int(nw), int(nh))
        bw = s * 0.42; bh = max(3.0, s * 0.06)
        bx = (s - bw) / 2; by = ny + nh
        p.drawRoundedRect(int(bx), int(by), int(bw), int(bh), s * 0.03, s * 0.03)

    p.end()
    return pm


def _make_app_icon() -> "QIcon":
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw_icon(sz))
    return icon


def _install_icon():
    """Write PNG files into ~/.local/share/icons/hicolor so the DE picks them up."""
    try:
        base = Path.home() / ".local" / "share" / "icons" / "hicolor"
        for sz in (16, 24, 32, 48, 64, 128, 256):
            dest = base / f"{sz}x{sz}" / "apps"
            dest.mkdir(parents=True, exist_ok=True)
            _draw_icon(sz).save(str(dest / "novamon.png"))
    except Exception:
        pass   # non-fatal — window icon still works


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


# ── Memory / DIMM sensors ────────────────────────────────────────────────────
def _dimm_temps() -> list:
    """Return [(label, temp_c)] for spd5118 DIMM sensors."""
    result = []
    slot = 1
    for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            if (hwmon / "name").read_text().strip() != "spd5118":
                continue
            t = int((hwmon / "temp1_input").read_text().strip()) / 1000
            result.append((f"DIMM {slot}", t))
            slot += 1
        except: pass
    return result


# ── Storage sensors ───────────────────────────────────────────────────────────
def _phys_dev(dev_path: str) -> str:
    """'/dev/nvme0n1p2' → 'nvme0n1',  '/dev/sda3' → 'sda'."""
    name = Path(dev_path).name
    if "nvme" in name:
        idx = name.rfind("p")
        if idx > 0 and name[idx + 1:].isdigit():
            return name[:idx]
        return name
    return name.rstrip("0123456789")

def _nvme_info() -> dict:
    """Returns {nvme_name: {model, temp}} for each NVMe hwmon device."""
    result = {}
    for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            if (hwmon / "name").read_text().strip() != "nvme":
                continue
            dev = (hwmon / "device").resolve()
            nvme_name = dev.name   # e.g. "nvme0"
            model = ""
            for mp in (dev / "model", Path(f"/sys/class/nvme/{nvme_name}/model")):
                if mp.exists():
                    model = mp.read_text().strip(); break
            temp = int((hwmon / "temp1_input").read_text().strip()) / 1000
            result[nvme_name] = {"model": model, "temp": temp}
        except: pass
    return result

_io_prev: dict = {}   # dev → (read_bytes, write_bytes, timestamp)

def _disk_rates() -> dict:
    """Returns {dev: (read_MB_s, write_MB_s)} delta since last call."""
    global _io_prev
    now = time.time()
    rates = {}
    try:
        counters = psutil.disk_io_counters(perdisk=True) or {}
        for dev, c in counters.items():
            if dev in _io_prev:
                pr, pw, pt = _io_prev[dev]
                dt = now - pt
                if dt > 0:
                    rates[dev] = ((c.read_bytes - pr) / dt / 1024**2,
                                  (c.write_bytes - pw) / dt / 1024**2)
            _io_prev[dev] = (c.read_bytes, c.write_bytes, now)
    except: pass
    return rates

def _disk_info() -> list:
    """Returns list of {phys_dev, model, temp, mounts:[{mountpoint,used_gb,total_gb,pct,fstype}]}."""
    nvme = _nvme_info()
    groups: dict = {}
    for part in psutil.disk_partitions(all=False):
        try:
            if not part.mountpoint or part.fstype in ("tmpfs","devtmpfs","squashfs",""):
                continue
            usage = psutil.disk_usage(part.mountpoint)
            phys  = _phys_dev(part.device)
            if phys not in groups:
                # Try to get model for non-NVMe drives
                model = ""
                for mp in (Path(f"/sys/block/{phys}/device/model"),):
                    if mp.exists():
                        model = mp.read_text().strip(); break
                # NVMe: map nvme0n1 → nvme0
                import re as _re
                nvme_key = ""
                if "nvme" in phys:
                    m = _re.match(r"(nvme\d+)", phys)
                    if m: nvme_key = m.group(1)
                ni = nvme.get(nvme_key, {})
                groups[phys] = {"phys_dev": phys, "model": ni.get("model", model),
                                "temp": ni.get("temp", 0), "mounts": []}
            groups[phys]["mounts"].append({
                "mountpoint": part.mountpoint,
                "used_gb":  usage.used  / 1024**3,
                "total_gb": usage.total / 1024**3,
                "pct":      usage.percent,
                "fstype":   part.fstype,
            })
        except: pass
    return list(groups.values())


# ── Network sensors ───────────────────────────────────────────────────────────
import socket as _socket

_net_prev: dict = {}   # iface → (snetio, timestamp)

def _net_rates() -> dict:
    """Returns {iface: (upload_MB_s, download_MB_s)} since last call."""
    global _net_prev
    now = time.time()
    try:
        stats = psutil.net_io_counters(pernic=True)
    except Exception:
        return {}
    rates = {}
    for iface, s in stats.items():
        if iface in _net_prev:
            prev_s, prev_t = _net_prev[iface]
            dt = now - prev_t
            if dt > 0:
                up = (s.bytes_sent - prev_s.bytes_sent) / dt / 1_048_576
                dn = (s.bytes_recv - prev_s.bytes_recv) / dt / 1_048_576
                rates[iface] = (max(0.0, up), max(0.0, dn))
        _net_prev[iface] = (s, now)
    return rates

def _net_ifaces() -> list:
    """Return list of active interface dicts (skip loopback + never-used)."""
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_io_counters(pernic=True)
    except Exception:
        return []
    result = []
    for iface, s in stats.items():
        if iface == "lo":
            continue
        if s.bytes_sent == 0 and s.bytes_recv == 0:
            continue
        ip = ""
        for a in addrs.get(iface, []):
            if a.family == _socket.AF_INET:
                ip = a.address; break
        result.append({
            "iface":       iface,
            "ip":          ip,
            "bytes_sent":  s.bytes_sent,
            "bytes_recv":  s.bytes_recv,
        })
    return result


# ── GPU process list ───────────────────────────────────────────────────────────
def _gpu_processes() -> list:
    """Return [{pid, name, vram_mb}] sorted by VRAM descending."""
    if not NVIDIA or not _nvh:
        return []
    procs, seen = [], set()
    try:
        for fn in (pynvml.nvmlDeviceGetComputeRunningProcesses,
                   pynvml.nvmlDeviceGetGraphicsRunningProcesses):
            try:
                for p in fn(_nvh):
                    if p.pid not in seen:
                        seen.add(p.pid)
                        procs.append(p)
            except Exception:
                pass
    except Exception:
        return []
    result = []
    for p in procs:
        try:
            name = psutil.Process(p.pid).name()
        except Exception:
            try:
                name = Path(f"/proc/{p.pid}/comm").read_text().strip()
            except Exception:
                name = f"pid {p.pid}"
        vram = getattr(p, "usedGpuMemory", 0) or 0
        result.append({"pid": p.pid, "name": name, "vram_mb": vram // 1_048_576})
    return sorted(result, key=lambda x: x["vram_mb"], reverse=True)


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

# ── Process kill helper ───────────────────────────────────────────────────────
def _kill_proc(pid: int, force: bool = False) -> tuple:
    sig     = signal.SIGKILL if force else signal.SIGTERM
    sig_str = "-9" if force else "-15"
    try:
        os.kill(pid, sig)
        return True, "Signal sent"
    except ProcessLookupError:
        return False, "Process not found"
    except PermissionError:
        r = subprocess.run(["sudo", "kill", sig_str, str(pid)],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return True, "Killed (elevated)"
        return False, (r.stderr.strip() or "sudo kill failed")


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
        psutil.cpu_percent(percpu=True, interval=None)
        while not self.isInterruptionRequested():
            try:
                core_freqs = []
                try:
                    cf = psutil.cpu_freq(percpu=True)
                    core_freqs = [f.current for f in cf] if cf else []
                except: pass
                self.tick.emit({
                    "cpu_t":        _cpu_temp(),
                    "gpu_t":        _gpu_temp(),
                    "cpu_pct":      psutil.cpu_percent(interval=None),
                    "cpu_mhz":      (psutil.cpu_freq().current if psutil.cpu_freq() else 0),
                    "gov":          _cpu_governor(),
                    "gpu":          _gpu_info(),
                    "cpu_cores":    psutil.cpu_percent(percpu=True, interval=None),
                    "cpu_core_mhz": core_freqs,
                    "ram":          psutil.virtual_memory(),
                    "swap":         psutil.swap_memory(),
                    "dimm_temps":   _dimm_temps(),
                    "gpu_procs":    _gpu_processes(),
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

class CoreGrid(QWidget):
    """Painted heatmap of per-CPU-core load (and frequency if available)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cores: list = []   # [(pct, mhz), ...]
        self.setSizePolicy(_SP.Expanding, _SP.Preferred)
        self.setMinimumHeight(72)

    def update_cores(self, pcts: list, freqs: list):
        n = len(pcts)
        self._cores = [(pcts[i], freqs[i] if i < len(freqs) else 0) for i in range(n)]
        cols = min(8, max(4, math.ceil(math.sqrt(n * 2))))
        rows = math.ceil(n / cols)
        self.setMinimumHeight(max(72, rows * 42 + 6))
        self.update()

    def paintEvent(self, _):
        if not self._cores: return
        p = QPainter(self); p.setRenderHint(_RH.Antialiasing)
        n = len(self._cores)
        cols = min(8, max(4, math.ceil(math.sqrt(n * 2))))
        rows = math.ceil(n / cols)
        GAP = 4
        w, h = self.width(), self.height()
        cw = max(24.0, (w - GAP * (cols + 1)) / cols)
        ch = max(24.0, (h - GAP * (rows + 1)) / rows)

        for i, (pct, mhz) in enumerate(self._cores):
            col = i % cols; row = i // cols
            x = GAP + col * (cw + GAP)
            y = GAP + row * (ch + GAP)
            t = _clamp(pct / 100.0, 0.0, 1.0)
            # Colour: teal(0%) → orange(50%) → red(100%)
            if t < 0.5:
                t2 = t * 2
                r = int(C_GPU.red()   + (C_WARN.red()   - C_GPU.red())   * t2)
                g = int(C_GPU.green() + (C_WARN.green() - C_GPU.green()) * t2)
                b = int(C_GPU.blue()  + (C_WARN.blue()  - C_GPU.blue())  * t2)
            else:
                t2 = (t - 0.5) * 2
                r = int(C_WARN.red()   + (C_CRIT.red()   - C_WARN.red())   * t2)
                g = int(C_WARN.green() + (C_CRIT.green() - C_WARN.green()) * t2)
                b = int(C_WARN.blue()  + (C_CRIT.blue()  - C_WARN.blue())  * t2)
            accent = QColor(r, g, b)
            bg = QColor(r, g, b, max(18, int(t * 110)))
            p.setPen(_PS.NoPen); p.setBrush(QBrush(bg))
            p.drawRoundedRect(int(x), int(y), int(cw), int(ch), 5, 5)
            border_c = QColor(r, g, b, 80)
            p.setPen(QPen(border_c, 1)); p.setBrush(_BS.NoBrush)
            p.drawRoundedRect(int(x), int(y), int(cw), int(ch), 5, 5)
            # Core index (small, top-left)
            p.setFont(QFont("", max(6, int(ch * 0.20)))); p.setPen(QPen(C_MUTED))
            p.drawText(int(x + 3), int(y + ch * 0.30), f"C{i}")
            # Usage % (centre)
            p.setFont(QFont("", max(7, int(ch * 0.32)), _bold()))
            p.setPen(QPen(accent))
            txt = f"{pct:.0f}%"
            fm  = QFontMetrics(p.font())
            p.drawText(int(x + (cw - fm.horizontalAdvance(txt)) / 2),
                       int(y + ch * 0.70), txt)
            # Frequency (bottom, only if cell is tall enough)
            if ch >= 46 and mhz > 0:
                p.setFont(QFont("", max(6, int(ch * 0.19)))); p.setPen(QPen(C_MUTED))
                f_txt = f"{mhz / 1000:.1f}G"
                fm2   = QFontMetrics(p.font())
                p.drawText(int(x + (cw - fm2.horizontalAdvance(f_txt)) / 2),
                           int(y + ch - 4), f_txt)
        p.end()


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
        for w in (lb, self._cpu_gauge, self._cpu_bar, self._cpu_pct,
                  self._cpu_spark, self._cpu_freq, self._cpu_gov): cl.addWidget(w)

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

        # ── RAM + DIMM row ────────────────────────────────────────────────────
        ram_row = QHBoxLayout(); ram_row.setSpacing(16)

        ram_f, rl = _card()
        ram_f.setSizePolicy(_SP.Expanding, _SP.Preferred)
        lb_r = QLabel("MEMORY"); lb_r.setFont(QFont("", 9, _bold()))
        lb_r.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        self._ram_bar  = UsageBar(C_CPU)
        self._ram_info = InfoRow("RAM Used")
        self._ram_avail= InfoRow("Available")
        self._swap_row = InfoRow("Swap")
        for w in (lb_r, self._ram_bar, self._ram_info, self._ram_avail, self._swap_row):
            rl.addWidget(w)
        # DIMM temp rows (added dynamically on first tick)
        self._dimm_rows: list = []
        ram_row.addWidget(ram_f)
        lay.addLayout(ram_row)

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
        # RAM
        vm = d.get("ram"); sm = d.get("swap")
        if vm:
            self._ram_bar.set(vm.percent)
            self._ram_info.set(f"{vm.used/1024**3:.1f} / {vm.total/1024**3:.1f} GB  ({vm.percent:.0f}%)")
            self._ram_avail.set(f"{vm.available/1024**3:.1f} GB free")
        if sm:
            self._swap_row.set(
                f"{sm.used/1024**3:.1f} / {sm.total/1024**3:.1f} GB" if sm.total else "Not configured")
        # DIMM temps — create rows on first appearance
        dimms = d.get("dimm_temps", [])
        if dimms and not self._dimm_rows:
            for label, _ in dimms:
                row = InfoRow(label)
                self._dimm_rows.append(row)
                self._ram_bar.parent().layout().addWidget(row)  # add to ram card layout
        for row, (_, temp) in zip(self._dimm_rows, dimms):
            row.set(f"{temp:.1f} °C")


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
        self._gpu_proc_panel = GpuProcessPanel()
        blay.addWidget(self._gpu_proc_panel)

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
        self._gpu_proc_panel.update_procs(d.get("gpu_procs", []))

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
# TASK MANAGER
# ═════════════════════════════════════════════════════════════════════════════

class ProcCache:
    """Maintains psutil.Process objects across refreshes for accurate cpu_percent()."""
    def __init__(self):
        self._cache: dict = {}

    def snapshot(self) -> list:
        current_pids: set = set()
        rows: list = []
        attrs = ["pid", "name", "username", "status", "memory_info"]
        for proc in psutil.process_iter(attrs):
            try:
                info = proc.info
                pid  = info["pid"]
                current_pids.add(pid)
                if pid not in self._cache:
                    self._cache[pid] = proc
                    proc.cpu_percent(interval=None)  # prime — returns 0 on first call
                    cpu = 0.0
                else:
                    try:   cpu = self._cache[pid].cpu_percent(interval=None)
                    except Exception:
                        self._cache[pid] = proc
                        proc.cpu_percent(interval=None)
                        cpu = 0.0
                try:   mem = info["memory_info"].rss // (1024 * 1024) if info.get("memory_info") else 0
                except Exception: mem = 0
                rows.append({
                    "pid":    pid,
                    "name":   info.get("name") or "",
                    "cpu":    cpu,
                    "mem":    mem,
                    "user":   info.get("username") or "",
                    "status": info.get("status")   or "",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        for pid in list(self._cache):
            if pid not in current_pids:
                del self._cache[pid]
        rows.sort(key=lambda r: r["cpu"], reverse=True)
        return rows


class ProcessModel(QAbstractTableModel):
    COLS = ["PID", "Name", "CPU %", "Memory", "User", "Status"]
    COL_PID, COL_NAME, COL_CPU, COL_MEM, COL_USER, COL_STATUS = range(6)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []

    def update_rows(self, rows: list):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=None):    return len(self._rows)
    def columnCount(self, parent=None): return len(self.COLS)

    def headerData(self, section, orientation, role=None):
        if role == _DR.DisplayRole and orientation == _OR.Horizontal:
            return self.COLS[section]
        return None

    def data(self, index, role=None):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == _DR.DisplayRole:
            if col == self.COL_PID:    return str(row["pid"])
            if col == self.COL_NAME:   return row["name"]
            if col == self.COL_CPU:    return f"{row['cpu']:.1f}"
            if col == self.COL_MEM:    return f"{row['mem']} MB"
            if col == self.COL_USER:   return row["user"]
            if col == self.COL_STATUS: return row["status"]
        if role == _DR.UserRole:
            if col == self.COL_PID:  return row["pid"]
            if col == self.COL_CPU:  return row["cpu"]
            if col == self.COL_MEM:  return row["mem"]
            return self.data(index, _DR.DisplayRole)
        if role == _DR.ForegroundRole:
            if col == self.COL_CPU:
                cpu = row["cpu"]
                if cpu > 50: return QBrush(C_CRIT)
                if cpu > 20: return QBrush(C_WARN)
                return QBrush(C_GPU)
        return None

    def get_row(self, row_idx: int):
        return self._rows[row_idx] if 0 <= row_idx < len(self._rows) else None


class ProcProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = ""

    def set_filter(self, text: str):
        self._filter = text.lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent) -> bool:
        if not self._filter: return True
        src = self.sourceModel()
        if src is None: return True
        name = src.data(src.index(row, ProcessModel.COL_NAME), _DR.DisplayRole) or ""
        pid  = src.data(src.index(row, ProcessModel.COL_PID),  _DR.DisplayRole) or ""
        return self._filter in name.lower() or self._filter in pid

    def lessThan(self, left, right) -> bool:
        lv = left.data(_DR.UserRole)
        rv = right.data(_DR.UserRole)
        try:    return float(lv) < float(rv)
        except: return str(lv or "") < str(rv or "")


class CpuDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        cpu_val = index.data(_DR.UserRole)
        if cpu_val is None: return
        try:   cpu = float(cpu_val)
        except: return
        c = C_CRIT if cpu > 50 else C_WARN if cpu > 20 else C_GPU
        r = option.rect; bar_h = 3
        bar_w = int(r.width() * min(cpu / 100.0, 1.0))
        painter.save()
        if bar_w > 0:
            painter.setPen(_PS.NoPen); painter.setBrush(QBrush(c))
            painter.drawRect(r.x(), r.bottom() - bar_h, bar_w, bar_h)
        txt = f"{cpu:.1f}%"
        fm  = QFontMetrics(painter.font())
        tw  = fm.horizontalAdvance(txt); th = fm.height()
        painter.setPen(QPen(c)); painter.setBrush(_BS.NoBrush)
        painter.drawText(r.right() - tw - 6, r.top() + (r.height() - bar_h - th) // 2 + th, txt)
        painter.restore()


class TaskManagerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache = ProcCache()
        self._build()
        self._timer = QTimer(self, interval=2000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        QTimer.singleShot(100, self._refresh)

    def _build(self):
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        # Header bar
        hdr = QWidget(); hdr.setFixedHeight(50)
        hdr.setStyleSheet(f"background:{C_PANEL.name()};border-bottom:1px solid {C_BORDER.name()};")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(20, 0, 20, 0); hl.setSpacing(10)
        title = QLabel("Task Manager"); title.setFont(QFont("", 13, _bold()))
        title.setStyleSheet(f"color:{C_TEXT.name()};")
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name or PID…")
        self._search.setFixedWidth(220); self._search.setFixedHeight(28)
        self._search.setStyleSheet(f"""
            QLineEdit{{background:{C_CARD.name()};border:1px solid {C_BORDER.name()};
                border-radius:5px;color:{C_TEXT.name()};padding:0 8px;font-size:11px;}}
            QLineEdit:focus{{border-color:{C_CPU.name()};}}
        """)
        self._kill_btn  = _btn("Kill",       C_WARN, height=28)
        self._fkill_btn = _btn("Force Kill", C_CRIT, height=28)
        self._kill_btn.clicked.connect(lambda:  self._on_kill(False))
        self._fkill_btn.clicked.connect(lambda: self._on_kill(True))
        hl.addWidget(title); hl.addStretch()
        hl.addWidget(self._search); hl.addSpacing(8)
        hl.addWidget(self._kill_btn); hl.addWidget(self._fkill_btn)
        lay.addWidget(hdr)

        # Table
        self._model = ProcessModel()
        self._proxy = ProcProxyModel()
        self._proxy.setSourceModel(self._model)
        self._search.textChanged.connect(self._proxy.set_filter)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(ProcessModel.COL_CPU, _SO.DescendingOrder)
        self._table.setStyleSheet(f"""
            QTableView{{background:{C_BG.name()};border:none;
                gridline-color:{C_BORDER.name()};
                alternate-background-color:{C_CARD.name()};
                color:{C_TEXT.name()};font-size:11px;}}
            QTableView::item:selected{{background:rgba(78,154,241,22);}}
            QHeaderView::section{{background:{C_PANEL.name()};color:{C_MUTED.name()};
                border:none;border-bottom:1px solid {C_BORDER.name()};
                border-right:1px solid {C_BORDER.name()};
                padding:4px 8px;font-size:10px;letter-spacing:1px;}}
            QHeaderView::section:hover{{color:{C_TEXT.name()};}}
            QHeaderView::section:pressed{{background:{C_CARD.name()};}}
        """)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(_SEL.SelectRows)
        self._table.setSelectionMode(_SELM.SingleSelection)
        self._table.setEditTriggers(_ET.NoEditTriggers)
        hdr_h = self._table.horizontalHeader()
        hdr_h.setSectionResizeMode(ProcessModel.COL_NAME, _HRM.Stretch)
        hdr_h.setSortIndicatorShown(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.setShowGrid(True)
        self._table.setItemDelegateForColumn(ProcessModel.COL_CPU, CpuDelegate(self))
        for col, w in ((ProcessModel.COL_PID, 62), (ProcessModel.COL_CPU, 72),
                       (ProcessModel.COL_MEM, 88), (ProcessModel.COL_USER, 100),
                       (ProcessModel.COL_STATUS, 74)):
            self._table.setColumnWidth(col, w)
        lay.addWidget(self._table, 1)

        # Status bar
        self._status = QLabel("  Loading…")
        self._status.setFixedHeight(24)
        self._status.setStyleSheet(
            f"background:{C_PANEL.name()};color:{C_MUTED.name()};"
            f"font-size:10px;padding:0 4px;"
            f"border-top:1px solid {C_BORDER.name()};")
        lay.addWidget(self._status)

    def _refresh(self):
        rows = self._cache.snapshot()
        self._model.update_rows(rows)
        total_cpu = sum(r["cpu"] for r in rows)
        vm  = psutil.virtual_memory()
        ram_used  = vm.used  // (1024 ** 3)
        ram_total = vm.total // (1024 ** 3)
        self._status.setText(
            f"  {len(rows)} processes  ·  CPU {total_cpu:.1f}%  ·  "
            f"RAM {ram_used}/{ram_total} GB  ·  "
            f"Updated {datetime.now().strftime('%H:%M:%S')}")

    def _on_kill(self, force: bool):
        sel = self._table.selectionModel().selectedRows()
        if not sel: return
        src_idx = self._proxy.mapToSource(sel[0])
        row = self._model.get_row(src_idx.row())
        if not row: return
        pid  = row["pid"]
        name = row["name"]
        sig_label = "SIGKILL (force)" if force else "SIGTERM"
        reply = QMessageBox.question(
            self, "Confirm Kill",
            f"Send {sig_label} to:\n\n  PID {pid}  —  {name}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes: return
        ok, msg = _kill_proc(pid, force)
        if ok:
            self._status.setText(f"  ✓  {name} ({pid}): {msg}")
            QTimer.singleShot(800, self._refresh)
        else:
            QMessageBox.warning(self, "Kill Failed",
                                f"Could not kill {name} ({pid}):\n{msg}")




# ── CPU Tab (per-core heatmap) ────────────────────────────────────────────────

class CpuTab(QWidget):
    """Per-core load heatmap + frequency details."""
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self); outer.setContentsMargins(18, 18, 18, 18); outer.setSpacing(14)

        title = QLabel("CPU Cores")
        title.setStyleSheet(f"color:{C_TEXT.name()};font-size:15px;font-weight:bold;")
        outer.addWidget(title)

        card_w = QWidget()
        card_w.setStyleSheet(f"background:{C_CARD.name()};border-radius:10px;")
        cl = QVBoxLayout(card_w); cl.setContentsMargins(14, 12, 14, 12); cl.setSpacing(8)

        sec = QLabel("PER-CORE LOAD"); sec.setFont(QFont("", 9, _bold()))
        sec.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        cl.addWidget(sec)

        self._core_grid = CoreGrid()
        cl.addWidget(self._core_grid)

        outer.addWidget(card_w)

        # summary row below the heatmap
        stats_w = QWidget()
        stats_w.setStyleSheet(f"background:{C_CARD.name()};border-radius:10px;")
        sl = QVBoxLayout(stats_w); sl.setContentsMargins(14, 10, 14, 10); sl.setSpacing(4)
        sec2 = QLabel("SUMMARY"); sec2.setFont(QFont("", 9, _bold()))
        sec2.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        self._total_pct  = InfoRow("Total CPU Load")
        self._avg_freq   = InfoRow("Average Frequency")
        self._min_max    = InfoRow("Min / Max Core Load")
        for w in (sec2, self._total_pct, self._avg_freq, self._min_max):
            sl.addWidget(w)
        outer.addWidget(stats_w)
        outer.addStretch()

    def on_tick(self, d: dict):
        cores = d.get("cpu_cores", [])
        freqs = d.get("cpu_core_mhz", [])
        if not cores:
            return
        self._core_grid.update_cores(cores, freqs)
        self._total_pct.set(f"{d.get('cpu_pct', 0):.1f}%")
        if freqs:
            avg_f = sum(freqs) / len(freqs)
            self._avg_freq.set(f"{avg_f:.0f} MHz")
        if cores:
            self._min_max.set(f"{min(cores):.0f}% / {max(cores):.0f}%")


# ── Storage Tab ───────────────────────────────────────────────────────────────

class _DiskCard(QWidget):
    """One card per physical disk: model name, NVMe temp, partitions + throughput."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{C_CARD.name()};border-radius:10px;")
        lay = QVBoxLayout(self); lay.setContentsMargins(14, 12, 14, 12); lay.setSpacing(6)

        hdr = QHBoxLayout()
        self._name_lbl = QLabel("—"); self._name_lbl.setStyleSheet(
            f"color:{C_TEXT.name()};font-size:13px;font-weight:bold;")
        self._temp_lbl = QLabel(""); self._temp_lbl.setStyleSheet(
            f"color:{C_GPU.name()};font-size:12px;")
        hdr.addWidget(self._name_lbl); hdr.addStretch(); hdr.addWidget(self._temp_lbl)
        lay.addLayout(hdr)

        # throughput row
        io_row = QHBoxLayout()
        self._read_lbl  = QLabel("R: — MB/s"); self._read_lbl.setStyleSheet(
            f"color:{C_CPU.name()};font-size:11px;")
        self._write_lbl = QLabel("W: — MB/s"); self._write_lbl.setStyleSheet(
            f"color:{C_OC.name()};font-size:11px;")
        self._read_spark  = Sparkline("R", C_CPU, max_val=500)
        self._write_spark = Sparkline("W", C_OC,  max_val=500)
        self._read_spark.setFixedSize(100, 28)
        self._write_spark.setFixedSize(100, 28)
        io_row.addWidget(self._read_lbl); io_row.addWidget(self._read_spark)
        io_row.addSpacing(12)
        io_row.addWidget(self._write_lbl); io_row.addWidget(self._write_spark)
        io_row.addStretch()
        lay.addLayout(io_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{C_BORDER.name()};")
        lay.addWidget(sep)

        self._part_lay = QVBoxLayout(); self._part_lay.setSpacing(4)
        lay.addLayout(self._part_lay)
        self._part_widgets: list = []

    def update_data(self, info: dict, rates: tuple):
        model = info.get("model", info.get("phys_dev", "Unknown"))
        self._name_lbl.setText(model[:50])
        temp = info.get("temp")
        if temp is not None:
            c = _temp_color(temp)
            self._temp_lbl.setText(f"{temp:.0f} °C")
            self._temp_lbl.setStyleSheet(f"color:{c.name()};font-size:12px;")
        else:
            self._temp_lbl.setText("")

        r_mbs, w_mbs = rates
        self._read_lbl.setText(f"R: {r_mbs:.1f} MB/s")
        self._write_lbl.setText(f"W: {w_mbs:.1f} MB/s")
        self._read_spark.push(int(r_mbs))
        self._write_spark.push(int(w_mbs))

        mounts = info.get("mounts", [])
        # grow partition widgets as needed
        while len(self._part_widgets) < len(mounts):
            pw = QWidget()
            pl = QVBoxLayout(pw); pl.setContentsMargins(0, 2, 0, 2); pl.setSpacing(2)
            mp_lbl = QLabel(); mp_lbl.setStyleSheet(
                f"color:{C_MUTED.name()};font-size:10px;")
            bar = UsageBar(C_CPU)
            bar.setFixedHeight(8)
            info_lbl = QLabel(); info_lbl.setStyleSheet(
                f"color:{C_TEXT.name()};font-size:10px;")
            pl.addWidget(mp_lbl); pl.addWidget(bar); pl.addWidget(info_lbl)
            self._part_lay.addWidget(pw)
            self._part_widgets.append((mp_lbl, bar, info_lbl))

        for i, m in enumerate(mounts):
            mp_lbl, bar, info_lbl = self._part_widgets[i]
            mp_lbl.setText(f"{m['mountpoint']}  [{m['fstype']}]")
            bar.set(m["pct"])
            info_lbl.setText(
                f"{m['used_gb']:.1f} / {m['total_gb']:.1f} GB  ({m['pct']:.0f}%)")


class StorageTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict = {}   # phys_dev → _DiskCard
        outer = QVBoxLayout(self); outer.setContentsMargins(18, 18, 18, 18)

        title = QLabel("Storage")
        title.setStyleSheet(f"color:{C_TEXT.name()};font-size:15px;font-weight:bold;")
        outer.addWidget(title)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self._card_lay = QVBoxLayout(inner)
        self._card_lay.setSpacing(14)
        self._card_lay.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # own 2-second timer to poll disk rates
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)
        self._refresh()   # populate immediately

    def _refresh(self):
        try:
            disks = _disk_info()
            rates_map = _disk_rates()
        except Exception:
            return

        for info in disks:
            dev = info["phys_dev"]
            if dev not in self._cards:
                card = _DiskCard()
                self._cards[dev] = card
                # insert before the stretch at the end
                idx = self._card_lay.count() - 1
                self._card_lay.insertWidget(idx, card)

            r, w = rates_map.get(dev, (0.0, 0.0))
            self._cards[dev].update_data(info, (r, w))




# ── Network Tab ───────────────────────────────────────────────────────────────

class _NetCard(QWidget):
    """One card per active network interface."""
    def __init__(self, iface: str, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setStyleSheet(f"background:{C_CARD.name()};border-radius:10px;")
        lay = QVBoxLayout(self); lay.setContentsMargins(14, 12, 14, 12); lay.setSpacing(6)

        hdr = QHBoxLayout()
        name_lbl = QLabel(iface)
        name_lbl.setStyleSheet(f"color:{C_TEXT.name()};font-size:13px;font-weight:bold;")
        self._ip_lbl = QLabel("")
        self._ip_lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
        hdr.addWidget(name_lbl); hdr.addStretch(); hdr.addWidget(self._ip_lbl)
        lay.addLayout(hdr)

        io_row = QHBoxLayout(); io_row.setSpacing(16)
        up_col  = QVBoxLayout(); up_col.setSpacing(2)
        dn_col  = QVBoxLayout(); dn_col.setSpacing(2)

        up_lbl = QLabel("UPLOAD");   up_lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:9px;letter-spacing:1px;")
        dn_lbl = QLabel("DOWNLOAD"); dn_lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:9px;letter-spacing:1px;")
        self._up_val  = QLabel("0 B/s");  self._up_val.setStyleSheet(f"color:{C_OC.name()};font-size:14px;font-weight:bold;")
        self._dn_val  = QLabel("0 B/s");  self._dn_val.setStyleSheet(f"color:{C_GPU.name()};font-size:14px;font-weight:bold;")
        self._up_spark = Sparkline("↑", C_OC,  max_val=100); self._up_spark.setFixedSize(140, 32)
        self._dn_spark = Sparkline("↓", C_GPU, max_val=100); self._dn_spark.setFixedSize(140, 32)

        up_col.addWidget(up_lbl); up_col.addWidget(self._up_val); up_col.addWidget(self._up_spark)
        dn_col.addWidget(dn_lbl); dn_col.addWidget(self._dn_val); dn_col.addWidget(self._dn_spark)
        io_row.addLayout(up_col); io_row.addLayout(dn_col); io_row.addStretch()
        lay.addLayout(io_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{C_BORDER.name()};"); lay.addWidget(sep)

        totals = QHBoxLayout()
        self._sent_lbl = InfoRow("Total Sent")
        self._recv_lbl = InfoRow("Total Received")
        totals.addWidget(self._sent_lbl); totals.addWidget(self._recv_lbl)
        lay.addLayout(totals)

    @staticmethod
    def _fmt(mb: float) -> str:
        if mb < 1:       return f"{mb*1024:.0f} KB/s"
        if mb < 1024:    return f"{mb:.1f} MB/s"
        return f"{mb/1024:.2f} GB/s"

    @staticmethod
    def _fmt_total(b: int) -> str:
        if b < 1024**2:  return f"{b/1024:.1f} KB"
        if b < 1024**3:  return f"{b/1024**2:.1f} MB"
        return f"{b/1024**3:.2f} GB"

    def update_data(self, info: dict, up_mbs: float, dn_mbs: float):
        self._ip_lbl.setText(info.get("ip", ""))
        self._up_val.setText(self._fmt(up_mbs))
        self._dn_val.setText(self._fmt(dn_mbs))
        self._up_spark.push(int(up_mbs * 10))
        self._dn_spark.push(int(dn_mbs * 10))
        self._sent_lbl.set(self._fmt_total(info["bytes_sent"]))
        self._recv_lbl.set(self._fmt_total(info["bytes_recv"]))


class NetworkTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict = {}
        outer = QVBoxLayout(self); outer.setContentsMargins(18, 18, 18, 18)

        title = QLabel("Network")
        title.setStyleSheet(f"color:{C_TEXT.name()};font-size:15px;font-weight:bold;")
        outer.addWidget(title)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self._card_lay = QVBoxLayout(inner); self._card_lay.setSpacing(14)
        self._card_lay.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)
        self._refresh()

    def _refresh(self):
        try:
            ifaces = _net_ifaces()
            rates  = _net_rates()
        except Exception:
            return
        for info in ifaces:
            iface = info["iface"]
            if iface not in self._cards:
                card = _NetCard(iface)
                self._cards[iface] = card
                self._card_lay.insertWidget(self._card_lay.count() - 1, card)
            up, dn = rates.get(iface, (0.0, 0.0))
            self._cards[iface].update_data(info, up, dn)


# ── GPU Process Panel (used inside GPUTuningTab) ───────────────────────────────

class GpuProcessPanel(QWidget):
    """Compact table of processes currently using the GPU."""
    _COLS = ("Process", "PID", "VRAM")

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)

        sec = QLabel("GPU PROCESSES"); sec.setFont(QFont("", 9, _bold()))
        sec.setStyleSheet(f"color:{C_MUTED.name()};letter-spacing:2px;")
        lay.addWidget(sec)

        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(self._COLS)
        self._table.horizontalHeader().setSectionResizeMode(0, _HRM.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, _HRM.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, _HRM.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(_ET.NoEditTriggers)
        self._table.setSelectionBehavior(_SEL.SelectRows)
        self._table.setSelectionMode(_SELM.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(f"""
            QTableWidget {{background:{C_CARD.name()};color:{C_TEXT.name()};
                gridline-color:{C_BORDER.name()};border:none;font-size:11px;}}
            QHeaderView::section {{background:{C_PANEL.name()};color:{C_MUTED.name()};
                border:none;padding:4px 8px;font-size:10px;letter-spacing:1px;}}
            QTableWidget::item {{padding:4px 8px;}}
            QTableWidget::item:alternate {{background:{C_BG.name()};}}
        """)
        self._table.setMinimumHeight(80)
        self._table.setMaximumHeight(220)
        lay.addWidget(self._table)

        self._none_lbl = QLabel("No GPU processes")
        self._none_lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
        self._none_lbl.hide()
        lay.addWidget(self._none_lbl)

    def update_procs(self, procs: list):
        if not procs:
            self._table.setRowCount(0)
            self._table.hide(); self._none_lbl.show(); return
        self._none_lbl.hide(); self._table.show()
        self._table.setRowCount(len(procs))
        for r, p in enumerate(procs):
            self._table.setItem(r, 0, QTableWidgetItem(p["name"]))
            self._table.setItem(r, 1, QTableWidgetItem(str(p["pid"])))
            vram = f"{p['vram_mb']} MB" if p["vram_mb"] else "< 1 MB"
            item = QTableWidgetItem(vram)
            item.setForeground(C_GPU if p["vram_mb"] > 100 else C_MUTED)
            self._table.setItem(r, 2, item)
        self._table.resizeRowsToContents()


# ═════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NovaMon")
        self.setMinimumSize(920, 640)
        self._profile_key  = "balanced"
        self._profile_btns = {}
        _icon = _make_app_icon()
        self.setWindowIcon(_icon)
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
        self._cpu_tab        = CpuTab()
        self._gpu_tuning_tab = GPUTuningTab()
        self._storage_tab    = StorageTab()
        self._network_tab    = NetworkTab()
        self._task_mgr_tab   = TaskManagerTab()
        tabs.addTab(self._overview_tab,   "Overview")
        tabs.addTab(self._cpu_tab,        "CPU")
        tabs.addTab(self._gpu_tuning_tab, "GPU Tuning")
        tabs.addTab(self._storage_tab,    "Storage")
        tabs.addTab(self._network_tab,    "Network")
        tabs.addTab(self._task_mgr_tab,   "Processes")
        root.addWidget(tabs, 1)

    def _build_sidebar(self) -> QWidget:
        w = QWidget(); w.setFixedWidth(210)
        w.setStyleSheet(f"background:{C_PANEL.name()};border-right:1px solid {C_BORDER.name()};")
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        hdr = QWidget(); hdr.setFixedHeight(60)
        hdr.setStyleSheet(f"background:{C_PANEL.name()};")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(14, 0, 14, 0)
        brand = QLabel("Nova<b>Mon</b>"); brand.setFont(QFont("", 13))
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
        ver = QLabel("v3.0  ·  RTX 5070" if NVIDIA else "v3.0")
        ver.setStyleSheet(f"color:{C_MUTED.name()};font-size:9px;padding:8px 14px;")
        lay.addWidget(ver)
        return w

    def _start(self):
        self._col = Collector(); self._col.tick.connect(self._on_tick); self._col.start()
        self._blink_state = True
        QTimer(self, interval=800, timeout=self._blink).start()
        self._setup_tray()

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self.windowIcon(), self)
        self._tray.setToolTip("NovaMon — System Monitor")

        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu{{background:{C_CARD.name()};border:1px solid {C_BORDER.name()};
                color:{C_TEXT.name()};padding:4px;}}
            QMenu::item{{padding:6px 20px;border-radius:4px;}}
            QMenu::item:selected{{background:{C_BORDER.name()};color:{C_CPU.name()};}}
            QMenu::separator{{height:1px;background:{C_BORDER.name()};margin:4px 8px;}}
        """)
        show_act = QAction("Show / Hide", self)
        show_act.triggered.connect(self._toggle_window)
        quit_act  = QAction("Quit NovaMon", self)
        quit_act.triggered.connect(QApplication.instance().quit)
        menu.addAction(show_act)
        menu.addSeparator()
        menu.addAction(quit_act)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show(); self.raise_(); self.activateWindow()

    def _on_tray_activated(self, reason):
        try:    trigger = _TRAY.Trigger
        except: trigger = _TRAY.Trigger   # same attr, different namespace
        if reason == trigger:
            self._toggle_window()

    def _blink(self):
        self._blink_state = not self._blink_state
        c = C_GPU.name() if self._blink_state else C_BORDER.name()
        self._dot.setStyleSheet(f"color:{c};font-size:9px;")

    @pyqtSlot(dict)
    def _on_tick(self, d: dict):
        self._overview_tab.on_tick(d)
        self._cpu_tab.on_tick(d)
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
        if hasattr(self, "_tray") and self._tray.isVisible():
            self.hide(); e.ignore()   # minimize to tray; use Quit from tray menu to exit
        else:
            if hasattr(self, "_col"): self._col.requestInterruption(); self._col.wait(1000)
            super().closeEvent(e)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
    app = QApplication(sys.argv)
    app.setApplicationName("NovaMon")
    app.setQuitOnLastWindowClosed(False)   # keep alive in tray after window close
    app.setWindowIcon(_make_app_icon())
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
    _install_icon()
    win = MainWindow(); win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
