#!/usr/bin/env bash
# GrowBox install / update script for Orange Pi Zero 3 / Armbian
# Usage:
#   First install:  sudo bash install.sh https://github.com/YOUR/opi-box.git
#   Re-run safely:  sudo bash install.sh   (updates existing installation)

set -e

REPO_URL="${1:-}"
APP_DIR=/opt/growbox

echo "=== GrowBox installer ==="

# --- System packages ---
apt-get update -qq
apt-get install -y \
    git python3 python3-venv python3-pip \
    libgpiod2 gpiod python3-libgpiod \
    libopencv-dev python3-opencv \
    ffmpeg v4l-utils

# --- Clone or update source ---
if [ -d "$APP_DIR/.git" ]; then
    echo ">> Existing installation found, pulling latest..."
    cd "$APP_DIR"
    git pull origin main
elif [ -n "$REPO_URL" ]; then
    echo ">> Cloning $REPO_URL → $APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
else
    echo "ERROR: $APP_DIR is not a git repo and no REPO_URL provided."
    echo "Usage: sudo bash install.sh https://github.com/YOUR/opi-box.git"
    exit 1
fi

mkdir -p "$APP_DIR/timelapse"

# --- Preserve config.json if already exists ---
if [ ! -f "$APP_DIR/config.json" ]; then
    echo ">> No config.json found — creating default (edit it later)"
    cat > "$APP_DIR/config.json" << 'EOF'
{
  "telegram_token": "",
  "telegram_chat_id": "",
  "telegram_timelapse": true,
  "timelapse_interval_minutes": 30,
  "timelapse_enabled": true,
  "camera_device": 0,
  "gpio_chip": "gpiochip0",
  "relays": [
    {"id": 1, "name": "Свет",       "gpio_pin": 7, "active_low": true, "state": false},
    {"id": 2, "name": "Вентиляция", "gpio_pin": 8, "active_low": true, "state": false}
  ],
  "schedules": [
    {"relay_id": 1, "enabled": false, "on_time": "08:00", "off_time": "22:00"},
    {"relay_id": 2, "enabled": false, "on_time": "08:00", "off_time": "22:00"}
  ]
}
EOF
fi

# --- Python venv ---
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip --quiet
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

# --- Systemd: main service ---
cp "$APP_DIR/growbox.service" /etc/systemd/system/growbox.service

# --- Systemd: OTA timer ---
chmod +x "$APP_DIR/update.sh"
cp "$APP_DIR/ota.service" /etc/systemd/system/growbox-ota.service
cp "$APP_DIR/ota.timer"   /etc/systemd/system/growbox-ota.timer

systemctl daemon-reload

systemctl enable --now growbox
systemctl enable --now growbox-ota.timer

echo ""
echo "=== Done! ==="
IP=$(hostname -I | awk '{print $1}')
echo "  Web UI : http://${IP}:8080"
echo "  Logs   : journalctl -u growbox -f"
echo "  OTA    : journalctl -u growbox-ota -f"
echo ""
echo "GPIO tips:"
echo "  List chips : gpiodetect"
echo "  List lines : gpioinfo gpiochip0"
echo "  Test pin 7 : gpioset gpiochip0 7=1"
echo ""
echo "Next: edit /opt/growbox/config.json with your GPIO pins and Telegram token"
