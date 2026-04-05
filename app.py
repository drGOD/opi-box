import io
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from flask import Flask, Response, jsonify, render_template, request, send_file

import database
from camera import Camera, TIMELAPSE_DIR
from config import load_config, save_config
from relay import Relay
from scheduler import GrowboxScheduler
from sensors import SensorHub
from telegram_bot import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    config: dict
    camera: object
    relays: dict
    mode: dict
    notifier: object
    sensor_hub: object
    scheduler: object
    config_loader: Callable[[], dict]
    config_saver: Callable[[dict], None]
    db_module: object
    timelapse_dir: Path


def build_runtime(
    *,
    config_loader: Callable[[], dict] = load_config,
    config_saver: Callable[[dict], None] = save_config,
    db_module=database,
    camera_cls=Camera,
    relay_cls=Relay,
    scheduler_cls=GrowboxScheduler,
    sensor_hub_cls=SensorHub,
    notifier_cls=TelegramNotifier,
    timelapse_dir: Path = TIMELAPSE_DIR,
    start_background: bool = True,
) -> Runtime:
    db_module.init_db()
    config = config_loader()

    camera = camera_cls(device=config.get("camera_device", 0))
    if start_background and hasattr(camera, "start"):
        camera.start()

    relays = {}
    for relay_cfg in config.get("relays", []):
        relay = relay_cls(
            relay_id=relay_cfg["id"],
            name=relay_cfg["name"],
            gpio_pin=relay_cfg["gpio_pin"],
            active_low=relay_cfg.get("active_low", True),
            gpio_chip=config.get("gpio_chip", "gpiochip0"),
        )
        relay.state = relay_cfg.get("state", False)
        relays[relay_cfg["id"]] = relay

    mode = {"auto": True}
    notifier = notifier_cls(
        token=config.get("telegram_token", ""),
        chat_id=config.get("telegram_chat_id", ""),
    )
    sensor_hub = sensor_hub_cls(config, on_reading=db_module.insert_sensor_reading)
    if start_background and hasattr(sensor_hub, "start"):
        sensor_hub.start()

    scheduler = scheduler_cls(relays, camera, notifier, config, mode)
    if start_background and hasattr(scheduler, "start"):
        scheduler.start()

    return Runtime(
        config=config,
        camera=camera,
        relays=relays,
        mode=mode,
        notifier=notifier,
        sensor_hub=sensor_hub,
        scheduler=scheduler,
        config_loader=config_loader,
        config_saver=config_saver,
        db_module=db_module,
        timelapse_dir=timelapse_dir,
    )


def create_app(runtime: Optional[Runtime] = None) -> Flask:
    runtime = runtime or build_runtime()
    app = Flask(__name__)
    app.extensions["growbox_runtime"] = runtime

    def schedule_expected_states() -> Dict[int, bool]:
        """Return {relay_id: should_be_on} based on current time and schedule."""
        cur = tuple(map(int, datetime.now().strftime("%H:%M").split(":")))
        expected: Dict[int, bool] = {}
        for sched in runtime.config.get("schedules", []):
            if not sched.get("enabled"):
                continue
            on = tuple(map(int, sched.get("on_time", "00:00").split(":")))
            off = tuple(map(int, sched.get("off_time", "00:00").split(":")))
            if on <= off:
                should_on = on <= cur < off
            else:
                should_on = cur >= on or cur < off
            expected[sched["relay_id"]] = should_on
        return expected

    def persist_relay_state(relay: Relay, event_mode: str = "manual") -> None:
        """Persist relay state to config.json, log to DB, send Telegram notification."""
        runtime.notifier.notify_relay_change(relay)
        runtime.db_module.insert_relay_event(relay.id, relay.name, relay.state, event_mode)
        cfg = runtime.config_loader()
        for relay_cfg in cfg["relays"]:
            if relay_cfg["id"] == relay.id:
                relay_cfg["state"] = relay.state
        runtime.config_saver(cfg)

    def apply_auto_mode() -> None:
        """Apply schedule to relays immediately (called on startup / auto-mode restore)."""
        for relay_id, should_on in schedule_expected_states().items():
            relay = runtime.relays.get(relay_id)
            if relay is not None:
                relay.set(should_on, notify=lambda relay_obj: persist_relay_state(relay_obj, "auto"))
        logger.info("Auto mode applied")

    apply_auto_mode()
    runtime.notifier.notify_startup()

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def api_status():
        expected = schedule_expected_states()
        relay_list = []
        for relay in runtime.relays.values():
            relay_data = relay.to_dict()
            relay_data["schedule_expected"] = expected.get(relay.id)
            relay_list.append(relay_data)
        return jsonify({
            "ok": True,
            "camera": runtime.camera.is_available,
            "relays": relay_list,
            "auto_mode": runtime.mode["auto"],
            "time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            "timelapse_enabled": runtime.config.get("timelapse_enabled", True),
            "timelapse_interval": runtime.config.get("timelapse_interval_minutes", 30),
        })

    @app.route("/video_feed")
    def video_feed():
        return Response(
            runtime.camera.generate_stream(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    @app.route("/api/snapshot")
    def api_snapshot():
        frame = runtime.camera.get_snapshot()
        if frame is None:
            return jsonify({"error": "Camera not available"}), 503
        return send_file(io.BytesIO(frame), mimetype="image/jpeg")

    @app.route("/api/relay/<int:relay_id>/toggle", methods=["POST"])
    def toggle_relay(relay_id: int):
        relay = runtime.relays.get(relay_id)
        if relay is None:
            return jsonify({"error": "Not found"}), 404
        runtime.mode["auto"] = False
        relay.toggle(notify=lambda relay_obj: persist_relay_state(relay_obj, "manual"))
        return jsonify({**relay.to_dict(), "auto_mode": runtime.mode["auto"]})

    @app.route("/api/relay/<int:relay_id>/set", methods=["POST"])
    def set_relay(relay_id: int):
        relay = runtime.relays.get(relay_id)
        if relay is None:
            return jsonify({"error": "Not found"}), 404
        payload = request.get_json(silent=True) or {}
        state = bool(payload.get("state", False))
        runtime.mode["auto"] = False
        relay.set(state, notify=lambda relay_obj: persist_relay_state(relay_obj, "manual"))
        return jsonify({**relay.to_dict(), "auto_mode": runtime.mode["auto"]})

    @app.route("/api/auto_mode", methods=["POST"])
    def enable_auto_mode():
        runtime.mode["auto"] = True
        apply_auto_mode()
        return jsonify({"ok": True, "auto_mode": True})

    @app.route("/api/history")
    def api_history():
        hours = float(request.args.get("hours", 24))
        hours = max(1.0, min(hours, 24 * 30))
        return jsonify(runtime.db_module.get_history(hours))

    @app.route("/api/sensors")
    def get_sensors():
        return jsonify({
            "available": runtime.sensor_hub.available,
            "data": runtime.sensor_hub.latest or {},
        })

    @app.route("/api/schedule")
    def get_schedule():
        return jsonify(runtime.config_loader().get("schedules", []))

    @app.route("/api/schedule", methods=["POST"])
    def update_schedule():
        data = request.get_json(silent=True)
        if not isinstance(data, list):
            return jsonify({"error": "Expected list"}), 400
        cfg = runtime.config_loader()
        cfg["schedules"] = data
        runtime.config_saver(cfg)
        runtime.scheduler.config = cfg
        runtime.config.update(cfg)
        return jsonify({"ok": True})

    @app.route("/api/settings")
    def get_settings():
        cfg = runtime.config_loader()
        return jsonify({
            "telegram_token": cfg.get("telegram_token", ""),
            "telegram_chat_id": cfg.get("telegram_chat_id", ""),
            "telegram_timelapse": cfg.get("telegram_timelapse", True),
            "timelapse_enabled": cfg.get("timelapse_enabled", True),
            "timelapse_interval_minutes": cfg.get("timelapse_interval_minutes", 30),
            "camera_device": cfg.get("camera_device", 0),
            "gpio_chip": cfg.get("gpio_chip", "gpiochip0"),
            "sensors": cfg.get("sensors", {}),
            "relays": [
                {
                    "id": relay_cfg["id"],
                    "name": relay_cfg["name"],
                    "gpio_pin": relay_cfg["gpio_pin"],
                    "active_low": relay_cfg.get("active_low", True),
                }
                for relay_cfg in cfg.get("relays", [])
            ],
        })

    @app.route("/api/settings", methods=["POST"])
    def update_settings():
        data = request.get_json(silent=True) or {}
        cfg = runtime.config_loader()
        allowed = [
            "telegram_token", "telegram_chat_id", "telegram_timelapse",
            "timelapse_enabled", "timelapse_interval_minutes",
            "camera_device", "gpio_chip",
        ]
        for key in allowed:
            if key in data:
                cfg[key] = data[key]
        if "sensors" in data and isinstance(data["sensors"], dict):
            cfg.setdefault("sensors", {}).update(data["sensors"])
        runtime.config_saver(cfg)
        runtime.config.update(cfg)
        runtime.notifier.token = cfg["telegram_token"]
        runtime.notifier.chat_id = cfg["telegram_chat_id"]
        runtime.scheduler.config = cfg
        return jsonify({"ok": True})

    @app.route("/api/relays", methods=["POST"])
    def update_relays():
        data = request.get_json(silent=True)
        if not isinstance(data, list):
            return jsonify({"error": "Expected list"}), 400
        cfg = runtime.config_loader()
        updates = {relay_cfg["id"]: relay_cfg for relay_cfg in data if "id" in relay_cfg}
        for relay_cfg in cfg["relays"]:
            updated = updates.get(relay_cfg["id"])
            if updated is None:
                continue
            relay_cfg["name"] = str(updated.get("name", relay_cfg["name"]))
            relay_cfg["gpio_pin"] = int(updated.get("gpio_pin", relay_cfg["gpio_pin"]))
            relay_cfg["active_low"] = bool(updated.get("active_low", relay_cfg.get("active_low", True)))
        runtime.config_saver(cfg)
        runtime.config.update(cfg)
        for relay in runtime.relays.values():
            relay.close()
        for relay_cfg in cfg["relays"]:
            old = runtime.relays.get(relay_cfg["id"])
            new_relay = Relay(
                relay_id=relay_cfg["id"],
                name=relay_cfg["name"],
                gpio_pin=relay_cfg["gpio_pin"],
                active_low=relay_cfg.get("active_low", True),
                gpio_chip=cfg.get("gpio_chip", "gpiochip0"),
            )
            new_relay.state = old.state if old else relay_cfg.get("state", False)
            runtime.relays[relay_cfg["id"]] = new_relay
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
        ok = runtime.notifier.send_message(
            "рџЊ± <b>GrowBox</b> вЂ” С‚РµСЃС‚ СѓРІРµРґРѕРјР»РµРЅРёР№ СЂР°Р±РѕС‚Р°РµС‚!"
        )
        return jsonify({"ok": ok})

    @app.route("/api/timelapse")
    def list_timelapse():
        files = sorted(runtime.timelapse_dir.glob("*.jpg"), reverse=True)[:100]
        return jsonify([item.name for item in files])

    @app.route("/api/timelapse/<filename>")
    def get_timelapse_image(filename: str):
        filepath = runtime.timelapse_dir / Path(filename).name
        if not filepath.exists():
            return jsonify({"error": "Not found"}), 404
        return send_file(filepath, mimetype="image/jpeg")

    @app.route("/api/timelapse/<filename>", methods=["DELETE"])
    def delete_timelapse_image(filename: str):
        filepath = runtime.timelapse_dir / Path(filename).name
        if filepath.exists():
            filepath.unlink()
        return jsonify({"ok": True})

    return app


if os.environ.get("GROWBOX_SKIP_BOOTSTRAP") == "1":
    app = Flask(__name__)
else:
    app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
