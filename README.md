# ThermalWatch

A unified CPU and GPU temperature monitor and performance tuner for Linux desktops, built with PyQt6.
Designed after the aesthetic of Tuxedo Control Center and the feature set of MSI Afterburner.

---

## Features

### Overview Tab
- Animated 270° arc gauges for CPU and GPU temperature
- Colour shifts automatically: blue → orange → red as temperature rises
- 90-second sparkline history for both CPU and GPU
- Per-core frequency and governor readout
- GPU: utilisation %, VRAM used/total, power draw, fan speed, core/memory clock
- Live blinking status dot in the sidebar

### GPU Tuning Tab
- **Power limit slider** — always works, no special config required (175 W – 250 W on RTX 5070)
- **Core clock offset slider** — requires Coolbits (see Setup)
- **Memory clock offset slider** — requires Coolbits
- **Interactive fan curve editor** — drag points to reshape the temperature → fan % curve; left-click to add a point, right-click to remove one
- **Manual fan curve mode** — applies the curve to the GPU every 3 seconds via nvidia-settings
- **Live monitoring panel** — six real-time sparklines: Core Clock, Mem Clock, GPU Usage, Power Draw, Temperature, Fan Speed
- **Five profile slots** — save and reload complete tuning states (power limit + offsets + fan curve) to `~/.config/thermalwatch/gpu_profiles.json`
- **Setup Guide dialog** — in-app instructions for enabling Coolbits

### Performance Profiles (sidebar)
Three system-wide profiles that set the CPU governor and scale NVIDIA power limit together:

| Profile     | CPU Governor | NVIDIA TDP |
|-------------|--------------|------------|
| Silent      | powersave    | 50%        |
| Balanced    | schedutil    | 75%        |
| Performance | performance  | 100%       |

---

## Test Results (v2)

Verified on: **NVIDIA GeForce RTX 5070**, Linux 6.16, driver 570+

```
✓  NVIDIA pynvml init          handle acquired
✓  CPU temp readable           49.0°C
✓  GPU temp readable           43.0°C
✓  GPU name                    NVIDIA GeForce RTX 5070
✓  VRAM used/total             2339/12227 MB
✓  Power draw                  26.2 W
✓  Core clock                  667 MHz
✓  Mem clock                   14001 MHz
✓  Fan speed                   0%
✓  CPU freq                    3378 MHz
✓  CPU usage                   OK
✓  CPU governor                powersave
✓  Available governors         performance powersave
✓  Power range valid           175–250 W
✓  Current limit in range      250 W
✓  Fan interp @   0°C          ≈0%,   got 0.0%
✓  Fan interp @  40°C          ≈30%,  got 30.0%
✓  Fan interp @  50°C          ≈42%,  got 42.5%
✓  Fan interp @  60°C          ≈55%,  got 55.0%
✓  Fan interp @  85°C          ≈90%,  got 90.0%
✓  Fan interp @ 100°C          ≈100%, got 100.0%
✓  Profile save                slot 99 persisted
✓  Profile power value         220 W
✓  Profile fan curve           6 control points
✓  nvidia-settings installed
⚠  Coolbits (fan/OC control)   not set — needs xorg.conf change (see Setup)

25 passed · 0 failed · 1 info
```

---

## Requirements

| Package        | Purpose                                 | Install                                   |
|----------------|-----------------------------------------|-------------------------------------------|
| PyQt6 ≥ 6.4    | GUI framework                           | `pip install PyQt6`                       |
| psutil ≥ 5.9   | CPU temperature, frequency, governor    | `pip install psutil`                      |
| pynvml ≥ 11    | NVIDIA GPU metrics via NVML             | `pip install pynvml`                      |
| nvidia-settings | Fan control and clock offsets          | `sudo apt install nvidia-settings`        |
| lm-sensors     | CPU thermal sensor data (recommended)   | `sudo apt install lm-sensors`             |

---

## Installation

### Quick start

```bash
git clone <repo>
cd linux-monitoring
pip install -r requirements.txt --break-system-packages   # or use a venv
python3 thermalwatch.py
```

### Automated install (sudo rule + desktop entry)

```bash
chmod +x install.sh
./install.sh
```

This does three things:
1. Installs Python dependencies
2. Adds a passwordless `sudoers` rule so the app can change the CPU governor and NVIDIA power limit without prompting
3. Creates a `.desktop` launcher in `~/.local/share/applications/`

### Sudoers rule (what install.sh writes)

```
%<username> ALL=(ALL) NOPASSWD: \
  /usr/bin/tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor, \
  /usr/bin/nvidia-smi --power-limit=*
```

If you prefer not to run the installer, the app still works — power limit changes and governor switches will silently fail, and you'll need to run as root or configure sudo manually.

---

## Enabling Fan Control & Overclocking (Coolbits)

By default, NVIDIA drivers on Linux lock fan control and clock offset APIs. To unlock them:

**Step 1 — Create the X.org config file:**

```bash
sudo nano /etc/X11/xorg.conf.d/20-nvidia.conf
```

**Step 2 — Paste:**

```
Section "Device"
    Identifier  "GPU0"
    Driver      "nvidia"
    Option      "Coolbits" "28"
EndSection
```

**Step 3 — Log out and log back in.**

`Coolbits 28` = 4 (fan control) + 8 (clock offsets) + 16 (voltage). Without this, only the **Power Limit** slider works. The clock offset and fan curve sliders will show a warning label and remain locked.

> Note: Coolbits is **not** needed for power limit control. That always works via `nvidia-smi`.

---

## Fan Curve Editor

The fan curve editor (GPU Tuning → Fan Curve section) lets you define a custom temperature-to-fan-speed mapping:

- **Left-click on a point** → drag it to a new position
- **Left-click on empty space** → add a new control point
- **Right-click on a point** → remove it (minimum 2 points)
- Points are constrained so temperature order is always maintained
- The curve uses **linear interpolation** between points — predictable and stable for fan control
- **Apply Curve** activates manual mode; the curve is applied every 3 seconds
- **Auto** reverts the GPU to driver-managed fan control (`GPUFanControlState=0`)
- **Reset** restores the default S-curve: `0°C→0%, 40°C→30%, 60°C→55%, 70°C→70%, 85°C→90%, 100°C→100%`

Fan curve state is saved as part of GPU profiles (see below).

---

## GPU Profiles

The profile bar at the bottom of the GPU Tuning tab has five numbered slots.

**To save:** click a slot number to select it (it highlights), then click **Save**.

**To load:** select a slot, click **Load** — the sliders and fan curve update immediately, and Apply is called automatically.

Profiles are stored at:
```
~/.config/thermalwatch/gpu_profiles.json
```

Each profile stores:
```json
{
  "power": 220,
  "core_offset": 100,
  "mem_offset": 500,
  "fan_curve": [[0,0],[40,30],[60,55],[70,70],[85,90],[100,100]],
  "fan_mode": "auto"
}
```

---

## Architecture

Single-file application: `thermalwatch.py` (~1270 lines).

```
MainWindow
├── Sidebar (always visible)
│   ├── Live status dot (blinks every 800ms)
│   ├── CPU Governor readout
│   └── ProfileCard × 3  (Silent / Balanced / Performance)
│
└── QTabWidget
    ├── OverviewTab
    │   ├── Gauge (CPU)  ← Gauge widget, 270° arc, 60fps animation
    │   ├── Gauge (GPU)
    │   ├── Sparkline (CPU history, 90s)
    │   ├── Sparkline (GPU history, 90s)
    │   └── InfoRow × 5  (freq, governor, VRAM, power, clocks)
    │
    └── GPUTuningTab
        ├── Header bar  (status label, Apply, Reset, Setup Guide)
        ├── SliderControl × 3  (Power Limit, Core Offset, Mem Offset)
        ├── MetricRow × 6  (each has MiniGraph sparkline)
        ├── FanCurveEditor  (interactive, mouse-driven)
        └── ProfileBar  (5 slots, Save / Load)

Collector (QThread)  →  tick signal (every 1s)  →  OverviewTab + GPUTuningTab
```

**Data flow:**
- `Collector` runs in a background `QThread`, calls sensor functions, emits a `dict` signal every second
- Both tabs connect to the same signal — no polling on the main thread
- Fan curve timer fires every 3 seconds (only when manual mode is active) to call `nvidia-settings`

---

## What Works Without Coolbits

| Feature                  | Works without Coolbits? |
|--------------------------|------------------------|
| CPU / GPU temperature    | Yes                    |
| GPU usage / VRAM / power | Yes                    |
| CPU governor switching   | Yes (needs sudo)       |
| Power limit slider       | Yes (needs sudo)       |
| Core clock offset slider | No — locked           |
| Memory clock offset      | No — locked           |
| Fan curve (manual mode)  | No — locked           |

---

## Known Limitations

- **RTX 50-series (Blackwell):** nvidia-settings may not expose `GPUGraphicsClockOffset` or `GPUTargetFanSpeed` even with Coolbits, depending on the driver version. NVIDIA is still rolling out full software support for this architecture. Power limit control via `nvidia-smi` works on all supported driver versions.

- **Fan speed reads as 0%:** On some NVIDIA cards the fan does not spin until a temperature threshold is reached (0-RPM mode). The monitoring panel will show 0% at idle — this is correct behaviour.

- **Wayland / XWayland:** The app runs on XWayland. Launch with `QT_QPA_PLATFORM=xcb` if it does not start automatically. `nvidia-settings` also requires an active X display.

- **CPU temperatures:** Depends on `lm-sensors` and the kernel thermal driver for your CPU. Install `lm-sensors` and run `sudo sensors-detect` if CPU temperature shows 0.

- **schedutil governor:** If `schedutil` is not in the available governors list (some distros only ship `ondemand`), the Balanced profile automatically falls back to `ondemand`.

---

## File Layout

```
linux-monitoring/
├── thermalwatch.py      # entire application (~1270 lines)
├── requirements.txt     # PyQt6, psutil, pynvml
├── install.sh           # deps + sudoers rule + .desktop entry
└── README.md            # this file
```

Profile data written at runtime:
```
~/.config/thermalwatch/gpu_profiles.json
```

---

## Running

```bash
python3 thermalwatch.py
```

Or, if Wayland is the default session and the app does not open:

```bash
QT_QPA_PLATFORM=xcb python3 thermalwatch.py
```
