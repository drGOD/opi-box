import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_TIMELAPSE_TS_RE = re.compile(r"(\d{8}_\d{6})")
_GIF_FPS = 24
_GIF_FRAME_DURATION_MS = round(1000 / _GIF_FPS)
_GIF_MAX_SIZE = (960, 720)


def _timelapse_timestamp(path: Path) -> datetime | None:
    match = _TIMELAPSE_TS_RE.search(path.name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")


def _parse_gif_range(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y%m%d_%H%M%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(value)


def _link_frame_sequence(files: list[tuple[Path, datetime]], temp_dir: Path) -> None:
    for index, (filepath, _ts) in enumerate(files):
        link_path = temp_dir / f"frame_{index:06d}.jpg"
        try:
            os.symlink(filepath, link_path)
        except OSError:
            try:
                os.link(filepath, link_path)
            except OSError:
                shutil.copyfile(filepath, link_path)


def _write_label_sequence(files: list[tuple[Path, datetime]], temp_dir: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        font = ImageFont.load_default()

    margin = 0
    padding_x = 10
    padding_y = 6
    for index, (_filepath, ts) in enumerate(files):
        label = ts.strftime("%d.%m.%Y %H:%M:%S")
        scratch = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        draw = ImageDraw.Draw(scratch)
        bbox = draw.textbbox((0, 0), label, font=font)
        label_w = bbox[2] - bbox[0]
        label_h = bbox[3] - bbox[1]
        image = Image.new(
            "RGBA",
            (label_w + padding_x * 2, label_h + padding_y * 2),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (margin, margin, image.width - 1, image.height - 1),
            radius=6,
            fill=(0, 0, 0, 150),
        )
        draw.text((padding_x, padding_y), label, font=font, fill=(255, 255, 255, 240))
        image.save(temp_dir / f"label_{index:06d}.png")


def _generate_gif_with_ffmpeg(files: list[tuple[Path, datetime]], output_path: Path, temp_dir: Path) -> None:
    _link_frame_sequence(files, temp_dir)
    _write_label_sequence(files, temp_dir)
    width, height = _GIF_MAX_SIZE
    filter_complex = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bilinear,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[base];"
        "[base][1:v]overlay=14:14:format=auto,"
        "split[s0][s1];"
        "[s0]palettegen=stats_mode=diff[p];"
        "[s1][p]paletteuse=dither=bayer:bayer_scale=5"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            str(_GIF_FPS),
            "-i",
            str(temp_dir / "frame_%06d.jpg"),
            "-framerate",
            str(_GIF_FPS),
            "-i",
            str(temp_dir / "label_%06d.png"),
            "-filter_complex",
            filter_complex,
            str(output_path),
        ],
        check=True,
    )


@dataclass
class Runtime:
    config: dict
    camera: object
    relays: dict
    mode: dict
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
        desired = relay_cfg.get("state", False)
        relay.set(desired)
        relays[relay_cfg["id"]] = relay

    mode = {"auto": True}
    sensor_hub = sensor_hub_cls(config, on_reading=db_module.insert_sensor_reading)
    if start_background and hasattr(sensor_hub, "start"):
        sensor_hub.start()

    scheduler = scheduler_cls(
        relays,
        camera,
        config,
        mode,
        sensor_hub=sensor_hub,
    )
    if start_background and hasattr(scheduler, "start"):
        scheduler.start()

    return Runtime(
        config=config,
        camera=camera,
        relays=relays,
        mode=mode,
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
        humidity_relay_id = runtime.config.get("humidity_control", {}).get("relay_id")
        climate_relay_id = runtime.config.get("climate_ventilation", {}).get("relay_id")
        for sched in runtime.config.get("schedules", []):
            if not sched.get("enabled"):
                continue
            if sched.get("relay_id") == humidity_relay_id:
                continue
            if sched.get("relay_id") == climate_relay_id:
                continue
            on = tuple(map(int, sched.get("on_time", "00:00").split(":")))
            off = tuple(map(int, sched.get("off_time", "00:00").split(":")))
            if on <= off:
                should_on = on <= cur < off
            else:
                should_on = cur >= on or cur < off
            expected[sched["relay_id"]] = should_on
        return expected

    def humidity_control_status() -> dict:
        control = runtime.config.get("humidity_control", {})
        relay_id = control.get("relay_id")
        latest = getattr(runtime.sensor_hub, "latest", None) or {}
        humidity = latest.get("air_humidity")
        target = float(control.get("target_humidity", 65.0))
        hysteresis = max(0.0, float(control.get("hysteresis", 6.0)))
        lower_bound = target - hysteresis / 2.0
        upper_bound = target + hysteresis / 2.0

        desired_state = None
        if humidity is not None:
            if humidity <= lower_bound:
                desired_state = True
            elif humidity >= upper_bound:
                desired_state = False

        return {
            "enabled": bool(control.get("enabled")),
            "relay_id": relay_id,
            "current_humidity": humidity,
            "target_humidity": target,
            "hysteresis": hysteresis,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "desired_state": desired_state,
        }

    def persist_relay_state(relay: Relay, event_mode: str = "manual") -> None:
        runtime.db_module.insert_relay_event(relay.id, relay.name, relay.state, event_mode)
        cfg = runtime.config_loader()
        for relay_cfg in cfg["relays"]:
            if relay_cfg["id"] == relay.id:
                relay_cfg["state"] = relay.state
        runtime.config_saver(cfg)

    runtime.scheduler.relay_notify = lambda relay_obj: persist_relay_state(relay_obj, "auto")

    def apply_auto_mode() -> None:
        for relay_id, should_on in schedule_expected_states().items():
            relay = runtime.relays.get(relay_id)
            if relay is not None:
                relay.set(should_on, notify=lambda relay_obj: persist_relay_state(relay_obj, "auto"))
        runtime.scheduler._check_humidity_control(datetime.now())
        runtime.scheduler._check_climate_ventilation(datetime.now())
        logger.info("Auto mode applied")

    apply_auto_mode()

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def api_status():
        expected = schedule_expected_states()
        humidity_status = humidity_control_status()
        relay_list = []
        for relay in runtime.relays.values():
            relay_data = relay.to_dict()
            relay_data["schedule_expected"] = expected.get(relay.id)
            relay_data["humidity_controlled"] = relay.id == humidity_status["relay_id"] and humidity_status["enabled"]
            relay_data["humidity_expected"] = (
                humidity_status["desired_state"] if relay.id == humidity_status["relay_id"] else None
            )
            relay_list.append(relay_data)
        return jsonify({
            "ok": True,
            "camera": runtime.camera.is_available,
            "relays": relay_list,
            "auto_mode": runtime.mode["auto"],
            "humidity_control": humidity_status,
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
            "timelapse_enabled": cfg.get("timelapse_enabled", True),
            "timelapse_interval_minutes": cfg.get("timelapse_interval_minutes", 30),
            "camera_device": cfg.get("camera_device", 0),
            "gpio_chip": cfg.get("gpio_chip", "gpiochip0"),
            "sensors": cfg.get("sensors", {}),
            "humidity_control": cfg.get("humidity_control", {}),
            "climate_ventilation": cfg.get("climate_ventilation", {}),
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
            "timelapse_enabled", "timelapse_interval_minutes",
            "camera_device", "gpio_chip",
        ]
        for key in allowed:
            if key in data:
                cfg[key] = data[key]
        if "sensors" in data and isinstance(data["sensors"], dict):
            cfg.setdefault("sensors", {}).update(data["sensors"])
        if "humidity_control" in data and isinstance(data["humidity_control"], dict):
            cfg.setdefault("humidity_control", {}).update(data["humidity_control"])
        if "climate_ventilation" in data and isinstance(data["climate_ventilation"], dict):
            cfg.setdefault("climate_ventilation", {}).update(data["climate_ventilation"])
        runtime.config_saver(cfg)
        runtime.config.update(cfg)
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
        old_relays = runtime.relays
        for relay in old_relays.values():
            relay.close()
        runtime.relays = {}
        for relay_cfg in cfg["relays"]:
            old = old_relays.get(relay_cfg["id"])
            new_relay = Relay(
                relay_id=relay_cfg["id"],
                name=relay_cfg["name"],
                gpio_pin=relay_cfg["gpio_pin"],
                active_low=relay_cfg.get("active_low", True),
                gpio_chip=cfg.get("gpio_chip", "gpiochip0"),
            )
            desired = old.state if old else relay_cfg.get("state", False)
            new_relay.set(desired)
            runtime.relays[relay_cfg["id"]] = new_relay
        runtime.scheduler.relays = runtime.relays
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

    @app.route("/api/timelapse")
    def list_timelapse():
        files = sorted(runtime.timelapse_dir.glob("*.jpg"), reverse=True)
        return jsonify([item.name for item in files])

    @app.route("/api/timelapse/gif")
    def get_timelapse_gif():
        try:
            start = _parse_gif_range(request.args.get("start"))
            end = _parse_gif_range(request.args.get("end"))
        except ValueError:
            return jsonify({"error": "Invalid date range"}), 400

        if start and end and start > end:
            return jsonify({"error": "Start date must be before end date"}), 400

        dated_files = []
        for filepath in sorted(runtime.timelapse_dir.glob("*.jpg")):
            ts = _timelapse_timestamp(filepath)
            if ts is None:
                continue
            if start and ts < start:
                continue
            if end and ts > end:
                continue
            dated_files.append((filepath, ts))

        files = dated_files
        if not files:
            return jsonify({"error": "No timelapse frames"}), 404

        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".gif",
            prefix="growbox_timelapse_",
            dir=runtime.timelapse_dir,
            delete=False,
        ) as output:
            output_path = Path(output.name)

        try:
            if shutil.which("ffmpeg"):
                with tempfile.TemporaryDirectory(prefix="growbox_gif_", dir=runtime.timelapse_dir) as temp_dir:
                    _generate_gif_with_ffmpeg(files, output_path, Path(temp_dir))
            else:
                try:
                    from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
                except ImportError:
                    logger.exception("Pillow or ffmpeg is required to generate timelapse GIFs")
                    output_path.unlink(missing_ok=True)
                    return jsonify({"error": "GIF support is not installed"}), 500

                try:
                    font = ImageFont.truetype("DejaVuSans.ttf", 22)
                except OSError:
                    font = ImageFont.load_default()

                frame_size = None

                def render_frame(filepath: Path, ts: datetime):
                    nonlocal frame_size
                    try:
                        with Image.open(filepath) as image:
                            frame = image.convert("RGB")
                            frame.thumbnail(_GIF_MAX_SIZE, Image.Resampling.BILINEAR)
                            if frame_size is None:
                                frame_size = frame.size
                            contained = ImageOps.contain(frame, frame_size, Image.Resampling.BILINEAR)
                            canvas = Image.new("RGB", frame_size, (0, 0, 0))
                            offset = (
                                (frame_size[0] - contained.width) // 2,
                                (frame_size[1] - contained.height) // 2,
                            )
                            canvas.paste(contained, offset)
                            label = ts.strftime("%d.%m.%Y %H:%M:%S")
                            overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
                            draw = ImageDraw.Draw(overlay)
                            bbox = draw.textbbox((0, 0), label, font=font)
                            label_w = bbox[2] - bbox[0]
                            label_h = bbox[3] - bbox[1]
                            margin = 14
                            padding_x = 10
                            padding_y = 6
                            box = (
                                margin,
                                margin,
                                margin + label_w + padding_x * 2,
                                margin + label_h + padding_y * 2,
                            )
                            draw.rounded_rectangle(box, radius=6, fill=(0, 0, 0, 150))
                            draw.text(
                                (margin + padding_x, margin + padding_y),
                                label,
                                font=font,
                                fill=(255, 255, 255, 240),
                            )
                            canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
                            return canvas
                    except (OSError, UnidentifiedImageError):
                        logger.warning("Skipping unreadable timelapse frame %s", filepath)
                        return None

                first_frame = None
                first_index = 0
                for index, (filepath, ts) in enumerate(files):
                    first_frame = render_frame(filepath, ts)
                    if first_frame is not None:
                        first_index = index
                        break

                if first_frame is None:
                    output_path.unlink(missing_ok=True)
                    return jsonify({"error": "No readable timelapse frames"}), 422

                def rendered_tail():
                    for filepath, ts in files[first_index + 1:]:
                        frame = render_frame(filepath, ts)
                        if frame is not None:
                            yield frame

                first_frame.save(
                    output_path,
                    format="GIF",
                    save_all=True,
                    append_images=rendered_tail(),
                    duration=_GIF_FRAME_DURATION_MS,
                    loop=0,
                    optimize=False,
                    disposal=2,
                )
        except Exception:
            output_path.unlink(missing_ok=True)
            logger.exception("Failed to generate timelapse GIF")
            return jsonify({"error": "Failed to generate GIF"}), 500
        filename = f"growbox_timelapse_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gif"
        response = send_file(
            output_path,
            mimetype="image/gif",
            as_attachment=True,
            download_name=filename,
        )
        response.call_on_close(lambda: output_path.unlink(missing_ok=True))
        return response

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
