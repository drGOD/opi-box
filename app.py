import io
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

from camera import Camera, TIMELAPSE_DIR
from config import load_config, save_config
from relay import Relay
from scheduler import GrowboxScheduler
from telegram_bot import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App + component init
# ---------------------------------------------------------------------------

app = Flask(__name__)
config = load_config()

camera = Camera(device=config.get("camera_device", 0))
camera.start()

relays: dict[int, Relay] = {}
for _r in config.get("relays", []):
    rel = Relay(
        relay_id=_r["id"],
        name=_r["name"],
        gpio_pin=_r["gpio_pin"],
        active_low=_r.get("active_low", True),
        gpio_chip=config.get("gpio_chip", "gpiochip0"),
    )
    rel.state = _r.get("state", False)
    relays[_r["id"]] = rel

notifier = TelegramNotifier(
    token=config.get("telegram_token", ""),
    chat_id=config.get("telegram_chat_id", ""),
)

scheduler = GrowboxScheduler(relays, camera, notifier, config)
scheduler.start()

notifier.notify_startup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _persist_relay_state(relay: Relay) -> None:
    """Write relay.state to config.json after a manual toggle."""
    notifier.notify_relay_change(relay)
    cfg = load_config()
    for r in cfg["relays"]:
        if r["id"] == relay.id:
            r["state"] = relay.state
    save_config(cfg)


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "camera": camera.is_available,
        "relays": [r.to_dict() for r in relays.values()],
        "time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "timelapse_enabled": config.get("timelapse_enabled", True),
        "timelapse_interval": config.get("timelapse_interval_minutes", 30),
    })


# Camera ---------------------------------------------------------------

@app.route("/video_feed")
def video_feed():
    return Response(
        camera.generate_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/snapshot")
def api_snapshot():
    frame = camera.get_snapshot()
    if frame is None:
        return jsonify({"error": "Camera not available"}), 503
    return send_file(io.BytesIO(frame), mimetype="image/jpeg")


# Relays ---------------------------------------------------------------

@app.route("/api/relay/<int:relay_id>/toggle", methods=["POST"])
def toggle_relay(relay_id: int):
    relay = relays.get(relay_id)
    if relay is None:
        return jsonify({"error": "Not found"}), 404
    relay.toggle(notify=_persist_relay_state)
    return jsonify(relay.to_dict())


@app.route("/api/relay/<int:relay_id>/set", methods=["POST"])
def set_relay(relay_id: int):
    relay = relays.get(relay_id)
    if relay is None:
        return jsonify({"error": "Not found"}), 404
    state = bool(request.json.get("state", False))
    relay.set(state, notify=_persist_relay_state)
    return jsonify(relay.to_dict())


# Schedules ------------------------------------------------------------

@app.route("/api/schedule")
def get_schedule():
    return jsonify(load_config().get("schedules", []))


@app.route("/api/schedule", methods=["POST"])
def update_schedule():
    data = request.json
    if not isinstance(data, list):
        return jsonify({"error": "Expected list"}), 400
    cfg = load_config()
    cfg["schedules"] = data
    save_config(cfg)
    scheduler.config = cfg
    config.update(cfg)
    return jsonify({"ok": True})


# Settings -------------------------------------------------------------

@app.route("/api/settings")
def get_settings():
    cfg = load_config()
    return jsonify({
        "telegram_token": cfg.get("telegram_token", ""),
        "telegram_chat_id": cfg.get("telegram_chat_id", ""),
        "telegram_timelapse": cfg.get("telegram_timelapse", True),
        "timelapse_enabled": cfg.get("timelapse_enabled", True),
        "timelapse_interval_minutes": cfg.get("timelapse_interval_minutes", 30),
        "camera_device": cfg.get("camera_device", 0),
        "gpio_chip": cfg.get("gpio_chip", "gpiochip0"),
        "relays": [
            {"id": r["id"], "name": r["name"], "gpio_pin": r["gpio_pin"],
             "active_low": r.get("active_low", True)}
            for r in cfg.get("relays", [])
        ],
    })


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.json
    cfg = load_config()
    allowed = [
        "telegram_token", "telegram_chat_id", "telegram_timelapse",
        "timelapse_enabled", "timelapse_interval_minutes",
        "camera_device", "gpio_chip",
    ]
    for key in allowed:
        if key in data:
            cfg[key] = data[key]
    save_config(cfg)
    config.update(cfg)
    notifier.token = cfg["telegram_token"]
    notifier.chat_id = cfg["telegram_chat_id"]
    scheduler.config = cfg
    return jsonify({"ok": True})


@app.route("/api/relays", methods=["POST"])
def update_relays():
    data = request.json
    if not isinstance(data, list):
        return jsonify({"error": "Expected list"}), 400
    cfg = load_config()
    updates = {r["id"]: r for r in data if "id" in r}
    for r in cfg["relays"]:
        upd = updates.get(r["id"])
        if upd is None:
            continue
        r["name"] = str(upd.get("name", r["name"]))
        r["gpio_pin"] = int(upd.get("gpio_pin", r["gpio_pin"]))
        r["active_low"] = bool(upd.get("active_low", r.get("active_low", True)))
    save_config(cfg)
    config.update(cfg)
    # Reinitialize relay objects with new settings
    for r in cfg["relays"]:
        old = relays.get(r["id"])
        new_rel = Relay(
            relay_id=r["id"],
            name=r["name"],
            gpio_pin=r["gpio_pin"],
            active_low=r.get("active_low", True),
            gpio_chip=cfg.get("gpio_chip", "gpiochip0"),
        )
        new_rel.state = old.state if old else r.get("state", False)
        relays[r["id"]] = new_rel
    return jsonify({"ok": True})


@app.route("/api/version")
def api_version():
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = subprocess.call(
            ["git", "diff", "--quiet"],
            cwd=Path(__file__).parent,
            stderr=subprocess.DEVNULL,
        ) != 0
        return jsonify({"commit": commit + ("-dev" if dirty else "")})
    except Exception:
        return jsonify({"commit": "unknown"})


@app.route("/api/telegram/test", methods=["POST"])
def test_telegram():
    ok = notifier.send_message("🌱 <b>GrowBox</b> — тест уведомлений работает!")
    return jsonify({"ok": ok})


# Timelapse gallery ----------------------------------------------------

@app.route("/api/timelapse")
def list_timelapse():
    files = sorted(TIMELAPSE_DIR.glob("*.jpg"), reverse=True)[:100]
    return jsonify([f.name for f in files])


@app.route("/api/timelapse/<filename>")
def get_timelapse_image(filename: str):
    # Prevent path traversal
    filepath = TIMELAPSE_DIR / Path(filename).name
    if not filepath.exists():
        return jsonify({"error": "Not found"}), 404
    return send_file(filepath, mimetype="image/jpeg")


@app.route("/api/timelapse/<filename>", methods=["DELETE"])
def delete_timelapse_image(filename: str):
    filepath = TIMELAPSE_DIR / Path(filename).name
    if filepath.exists():
        filepath.unlink()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
