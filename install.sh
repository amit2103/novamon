#!/usr/bin/env bash
# NovaMon — install dependencies and create launcher
# Run with: bash install.sh  (will prompt for sudo password once)
set -e

echo "NovaMon installer — sudo access is needed for the sudoers rule."
sudo -v

pip install -r "$(dirname "$0")/requirements.txt" --break-system-packages 2>/dev/null \
  || pip install -r "$(dirname "$0")/requirements.txt"

# sudo rule so the app can switch CPU governor and NVIDIA power limit without a password prompt
RULE_FILE="/etc/sudoers.d/novamon"
if [ ! -f "$RULE_FILE" ]; then
  echo "Installing sudo rule for governor + NVIDIA power control (needs your password once)..."
  USER_NAME="$(whoami)"
  RULE="%${USER_NAME} ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor, /usr/bin/nvidia-smi --power-limit=*"
  echo "$RULE" | sudo tee "$RULE_FILE" > /dev/null
  sudo chmod 0440 "$RULE_FILE"
  echo "Sudo rule installed at $RULE_FILE"
fi

# Install icon PNGs into hicolor icon theme so desktop environments pick them up
echo "Generating NovaMon icons…"
QT_QPA_PLATFORM=offscreen python3 - <<PYEOF
import sys, os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, '$(cd "$(dirname "$0")" && pwd)')
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from thermalwatch import _draw_icon
from pathlib import Path
base = Path.home() / '.local' / 'share' / 'icons' / 'hicolor'
for sz in (16, 24, 32, 48, 64, 128, 256):
    dest = base / f'{sz}x{sz}' / 'apps'
    dest.mkdir(parents=True, exist_ok=True)
    _draw_icon(sz).save(str(dest / 'novamon.png'))
    print(f'  {sz}x{sz} → {dest}/novamon.png')
PYEOF
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

# Desktop launcher
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_FILE="$HOME/.local/share/applications/novamon.desktop"
mkdir -p "$HOME/.local/share/applications"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=NovaMon
Comment=CPU & GPU temperature, performance and process monitor
Exec=python3 ${SCRIPT_DIR}/thermalwatch.py
Icon=novamon
Terminal=false
Categories=System;Monitor;
Keywords=temperature;gpu;cpu;nvidia;monitor;thermal;processes;
StartupNotify=true
EOF
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "Desktop entry created at $DESKTOP_FILE"
echo ""
echo "Done! Run with:  python3 ${SCRIPT_DIR}/thermalwatch.py"
