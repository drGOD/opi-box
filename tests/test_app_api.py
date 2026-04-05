import importlib
import os
import tempfile
import unittest
from pathlib import Path


class FakeRelay:
    def __init__(self, relay_id, name, gpio_pin, active_low=True, gpio_chip="gpiochip0", state=False):
        self.id = relay_id
        self.name = name
        self.gpio_pin = gpio_pin
        self.active_low = active_low
        self.gpio_chip = gpio_chip
        self.state = state
        self.closed = False
        self.mock = True

    def set(self, state, notify=None):
        changed = self.state != state
        self.state = state
        if changed and notify:
            notify(self)
        return self.state

    def toggle(self, notify=None):
        return self.set(not self.state, notify)

    def close(self):
        self.closed = True

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "gpio_pin": self.gpio_pin,
            "mock": self.mock,
        }


class FakeCamera:
    def __init__(self, snapshot=b"jpeg"):
        self.snapshot = snapshot
        self.is_available = snapshot is not None

    def get_snapshot(self):
        return self.snapshot

    def generate_stream(self):
        yield b"frame"


class FakeNotifier:
    def __init__(self):
        self.messages = []
        self.startups = 0
        self.relay_changes = []
        self.token = ""
        self.chat_id = ""

    def notify_startup(self):
        self.startups += 1

    def notify_relay_change(self, relay):
        self.relay_changes.append((relay.id, relay.state))

    def send_message(self, text):
        self.messages.append(text)
        return True


class FakeSensorHub:
    def __init__(self, available=True, latest=None):
        self.available = available
        self.latest = latest or {}


class FakeScheduler:
    def __init__(self, config):
        self.config = config


class FakeDatabase:
    def __init__(self):
        self.history_calls = []
        self.relay_events = []

    def get_history(self, hours):
        self.history_calls.append(hours)
        return {"sensors": [], "relays": {}}

    def insert_relay_event(self, relay_id, relay_name, state, mode):
        self.relay_events.append((relay_id, relay_name, state, mode))


class AppApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_skip_bootstrap = os.environ.get("GROWBOX_SKIP_BOOTSTRAP")
        os.environ["GROWBOX_SKIP_BOOTSTRAP"] = "1"
        try:
            app_module = importlib.import_module("app")
        except Exception as exc:  # pragma: no cover - skip only for missing local deps
            cls._import_error = exc
            cls.Runtime = None
            cls.create_app = None
        else:
            cls._import_error = None
            cls.Runtime = app_module.Runtime
            cls.create_app = app_module.create_app

    @classmethod
    def tearDownClass(cls):
        if cls._old_skip_bootstrap is None:
            os.environ.pop("GROWBOX_SKIP_BOOTSTRAP", None)
        else:
            os.environ["GROWBOX_SKIP_BOOTSTRAP"] = cls._old_skip_bootstrap

    def setUp(self):
        if self._import_error is not None:
            self.skipTest(f"app import unavailable: {self._import_error}")

        self.saved_configs = []
        self.config = {
            "telegram_token": "",
            "telegram_chat_id": "",
            "telegram_timelapse": True,
            "timelapse_interval_minutes": 30,
            "timelapse_enabled": True,
            "camera_device": 0,
            "gpio_chip": "gpiochip0",
            "relays": [
                {"id": 1, "name": "Light", "gpio_pin": 7, "active_low": True, "state": False},
                {"id": 2, "name": "Fan", "gpio_pin": 8, "active_low": True, "state": True},
            ],
            "schedules": [
                {"relay_id": 1, "enabled": True, "on_time": "08:00", "off_time": "22:00"},
                {"relay_id": 2, "enabled": False, "on_time": "09:00", "off_time": "21:00"},
            ],
            "sensors": {"enabled": True},
        }
        self.db = FakeDatabase()
        self.notifier = FakeNotifier()
        self.relays = {
            1: FakeRelay(1, "Light", 7, state=False),
            2: FakeRelay(2, "Fan", 8, state=True),
        }
        self.runtime = self.Runtime(
            config=self.config,
            camera=FakeCamera(),
            relays=self.relays,
            mode={"auto": True},
            notifier=self.notifier,
            sensor_hub=FakeSensorHub(available=True, latest={"temperature": 22.0}),
            scheduler=FakeScheduler(self.config),
            config_loader=self.load_config,
            config_saver=self.save_config,
            db_module=self.db,
            timelapse_dir=Path(tempfile.mkdtemp()),
        )
        self.app = self.create_app(self.runtime)
        self.client = self.app.test_client()

    def load_config(self):
        return {
            **self.config,
            "relays": [dict(item) for item in self.config["relays"]],
            "schedules": [dict(item) for item in self.config["schedules"]],
            "sensors": dict(self.config["sensors"]),
        }

    def save_config(self, cfg):
        self.saved_configs.append(cfg)
        self.config = cfg

    def test_status_endpoint_returns_runtime_state(self):
        response = self.client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["relays"]), 2)
        self.assertTrue(self.notifier.startups >= 1)

    def test_snapshot_endpoint_returns_jpeg(self):
        response = self.client.get("/api/snapshot")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/jpeg")

    def test_snapshot_endpoint_handles_missing_camera_frame(self):
        self.runtime.camera.snapshot = None
        self.runtime.camera.is_available = False

        response = self.client.get("/api/snapshot")

        self.assertEqual(response.status_code, 503)

    def test_toggle_relay_turns_off_auto_mode_and_persists(self):
        response = self.client.post("/api/relay/1/toggle", json={})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.runtime.mode["auto"])
        self.assertTrue(self.db.relay_events)
        self.assertTrue(self.saved_configs)

    def test_set_relay_404_for_unknown_id(self):
        response = self.client.post("/api/relay/99/set", json={"state": True})

        self.assertEqual(response.status_code, 404)

    def test_schedule_update_validates_payload(self):
        bad = self.client.post("/api/schedule", json={"bad": True})
        good = self.client.post("/api/schedule", json=[{"relay_id": 1, "enabled": False}])

        self.assertEqual(bad.status_code, 400)
        self.assertEqual(good.status_code, 200)
        self.assertFalse(self.runtime.config["schedules"][0]["enabled"])

    def test_settings_update_updates_runtime_and_notifier(self):
        response = self.client.post(
            "/api/settings",
            json={
                "telegram_token": "abc",
                "telegram_chat_id": "42",
                "sensors": {"read_interval_seconds": 10},
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.runtime.notifier.token, "abc")
        self.assertEqual(self.runtime.notifier.chat_id, "42")
        self.assertEqual(self.runtime.config["sensors"]["read_interval_seconds"], 10)

    def test_history_endpoint_clamps_hours(self):
        response = self.client.get("/api/history?hours=99999")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.history_calls[-1], 24 * 30)

    def test_sensors_endpoint_returns_latest_reading(self):
        response = self.client.get("/api/sensors")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["temperature"], 22.0)

    def test_telegram_test_endpoint_uses_notifier(self):
        response = self.client.post("/api/telegram/test", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ok"], True)
        self.assertEqual(len(self.notifier.messages), 1)

    def test_timelapse_endpoints_list_get_and_delete_files(self):
        image_path = self.runtime.timelapse_dir / "frame_20260405_120000.jpg"
        image_path.write_bytes(b"img")

        listing = self.client.get("/api/timelapse")
        fetch = self.client.get(f"/api/timelapse/{image_path.name}")
        fetch.close()
        delete = self.client.delete(f"/api/timelapse/{image_path.name}")

        self.assertEqual(listing.status_code, 200)
        self.assertIn(image_path.name, listing.get_json())
        self.assertEqual(fetch.status_code, 200)
        self.assertEqual(fetch.mimetype, "image/jpeg")
        self.assertEqual(delete.status_code, 200)
        self.assertFalse(image_path.exists())


if __name__ == "__main__":
    unittest.main()
