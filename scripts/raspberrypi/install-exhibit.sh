#!/usr/bin/env bash
# Sets up the exhibition kiosk on a Raspberry Pi (Raspberry Pi OS with desktop).
# Run it once from the cloned repository:   bash scripts/raspberrypi/install-exhibit.sh
# After a reboot the web server starts by itself and Chromium opens the exhibit play page full screen.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USER_NAME="$(id -un)"
PORT="${EXHIBIT_PORT:-8765}"

echo "== packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip libfluidsynth3 libportaudio2 unclutter curl
CHROMIUM="$(command -v chromium-browser || command -v chromium || true)"
if [ -z "$CHROMIUM" ]; then sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium; fi

echo "== python environment"
python3 -m venv "$REPO/.venv"
# The project pin of scikit-learn 1.5.1 has no Python 3.13/aarch64 wheel
# (Debian Trixie), so use a compatible wheel release instead of compiling it
# on the Pi. Keep matplotlib: position_model imports the shared simulation
# helpers which currently import it during Web server startup.
{
  grep -Ev '^scikit-learn' "$REPO/requirements.txt"
  echo 'scikit-learn>=1.6,<2'
} | "$REPO/.venv/bin/pip" install -r /dev/stdin

echo "== serial port permission"
sudo usermod -aG dialout "$USER_NAME"

echo "== localhost admin actions"
bash "$REPO/scripts/raspberrypi/install-admin-actions.sh"

echo "== web server service"
sudo tee /etc/systemd/system/acrylic-pan-exhibit.service >/dev/null <<UNIT
[Unit]
Description=Acrylic Pan exhibit web server
After=network.target

[Service]
User=$USER_NAME
Group=dialout
SupplementaryGroups=dialout plugdev
WorkingDirectory=$REPO
Environment=PYTHONUNBUFFERED=1
ExecStart=$REPO/.venv/bin/python -m pc.acrylic_pan_web --host 127.0.0.1 --port $PORT --no-browser --auto-connect
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now acrylic-pan-exhibit.service

echo "== kiosk browser at login"
chmod +x "$REPO/scripts/raspberrypi/start-exhibit-kiosk.sh"
START="$REPO/scripts/raspberrypi/start-exhibit-kiosk.sh"
# Raspberry Pi OS Bookworm (labwc / Wayland)
mkdir -p "$HOME/.config/labwc"
touch "$HOME/.config/labwc/autostart"
sed -i '/start-exhibit-kiosk.sh/d' "$HOME/.config/labwc/autostart"
echo "EXHIBIT_PORT=$PORT $START &" >> "$HOME/.config/labwc/autostart"
# Older Raspberry Pi OS (X11) and other desktops that read XDG autostart
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/acrylic-pan-exhibit.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Acrylic Pan exhibit
Exec=env EXHIBIT_PORT=$PORT $START
X-GNOME-Autostart-enabled=true
DESKTOP

echo "== no screen blanking"
sudo raspi-config nonint do_blanking 1 || true

echo "Done. Reboot to start the exhibit:  sudo reboot"
