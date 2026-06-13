#!/usr/bin/env bash
# ThermalWatch — install dependencies and create launcher
set -e

pip install -r "$(dirname "$0")/requirements.txt" --break-system-packages 2>/dev/null \
  || pip install -r "$(dirname "$0")/requirements.txt"

# sudo rule so the app can switch CPU governor and NVIDIA power limit without a password prompt
RULE_FILE="/etc/sudoers.d/thermalwatch"
if [ ! -f "$RULE_FILE" ]; then
  echo "Installing sudo rule for governor + NVIDIA power control (needs your password once)..."
  USER_NAME="$(whoami)"
  RULE="%${USER_NAME} ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor, /usr/bin/nvidia-smi --power-limit=*"
  echo "$RULE" | sudo tee "$RULE_FILE" > /dev/null
  sudo chmod 0440 "$RULE_FILE"
  echo "Sudo rule installed at $RULE_FILE"
fi

# Desktop launcher
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_FILE="$HOME/.local/share/applications/thermalwatch.desktop"
mkdir -p "$HOME/.local/share/applications"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=ThermalWatch
Comment=CPU & GPU temperature and performance monitor
Exec=python3 ${SCRIPT_DIR}/thermalwatch.py
Icon=preferences-system
Terminal=false
Categories=System;Monitor;
Keywords=temperature;gpu;cpu;nvidia;monitor;thermal;
EOF

echo "Desktop entry created at $DESKTOP_FILE"
echo ""
echo "Done! Run with:  python3 ${SCRIPT_DIR}/thermalwatch.py"
