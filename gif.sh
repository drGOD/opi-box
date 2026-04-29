#!/usr/bin/env bash
# GrowBox timelapse GIF builder.
# Usage:
#   /opt/growbox/gif.sh
#   /opt/growbox/gif.sh --start 2026-04-05T12:00:00 --end 2026-04-05T18:00:00
#   /opt/growbox/gif.sh -o /tmp/growbox.gif --width 640 --height 480

set -e

APP_DIR="${GROWBOX_APP_DIR:-/opt/growbox}"
PYTHON="$APP_DIR/venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

if [ -z "$PYTHON" ]; then
    echo "ERROR: python3 not found" >&2
    exit 1
fi

cd "$APP_DIR"

OUTPUT="$APP_DIR/timelapse/growbox_timelapse_$(date '+%Y%m%d_%H%M%S').gif"

exec "$PYTHON" "$APP_DIR/timelapse_gif.py" \
    --input "$APP_DIR/timelapse" \
    --output "$OUTPUT" \
    "$@"
