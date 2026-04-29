#!/usr/bin/env bash
# GrowBox install / update script for Orange Pi Zero 3 / Armbian
# Usage:
#   One-liner:    curl -fsSL https://raw.githubusercontent.com/drGOD/opi-box/main/install.sh | sudo bash -s https://github.com/drGOD/opi-box.git
#   Local file:   sudo bash install.sh https://github.com/drGOD/opi-box.git
#   Re-run/update (already cloned): sudo bash /opt/growbox/install.sh

set -e

REPO_URL="${1:-}"
APP_DIR=/opt/growbox

echo "=== GrowBox installer ==="

# --- Swap (prevents OOM crashes during heavy pip installs on low-RAM boards) ---
if [ ! -f /swapfile ]; then
    echo ">> Creating 2 GB swapfile..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
    swapon /swapfile 2>/dev/null || true
fi

# --- System packages (only small, essential ones — OpenCV comes via pip) ---
apt-get update -qq

GPIOD_LIB_PACKAGE=""
for candidate in libgpiod3 libgpiod2; do
    if apt-cache show "$candidate" >/dev/null 2>&1; then
        GPIOD_LIB_PACKAGE="$candidate"
        break
    fi
done

if [ -z "$GPIOD_LIB_PACKAGE" ]; then
    echo "ERROR: neither libgpiod3 nor libgpiod2 is available from apt."
    echo "Check your Debian/Armbian package sources and run: apt-get update"
    exit 1
fi

apt-get install -y --no-install-recommends \
    git python3 python3-venv python3-pip \
    "$GPIOD_LIB_PACKAGE" gpiod python3-libgpiod \
    v4l-utils ffmpeg

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
chmod +x "$APP_DIR/update.sh" "$APP_DIR/gif.sh" "$APP_DIR/timelapse_gif.py"
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
echo "  GIF    : /opt/growbox/gif.sh"
echo ""
echo "GPIO tips:"
echo "  List chips : gpiodetect"
echo "  List lines : gpioinfo gpiochip0"
echo "  Test pin 7 : gpioset gpiochip0 7=1"
echo ""
echo "Next: edit /opt/growbox/config.json with your GPIO pins and sensor settings"
