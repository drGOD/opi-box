import tempfile
import unittest
from pathlib import Path

import config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._old_config_file = config.CONFIG_FILE
        self._tmp_dir = tempfile.TemporaryDirectory()
        config.CONFIG_FILE = Path(self._tmp_dir.name) / "config.json"

    def tearDown(self):
        config.CONFIG_FILE = self._old_config_file
        self._tmp_dir.cleanup()

    def test_load_config_creates_default_file_when_missing(self):
        loaded = config.load_config()

        self.assertTrue(config.CONFIG_FILE.exists())
        self.assertEqual(loaded["gpio_chip"], config.DEFAULT_CONFIG["gpio_chip"])
        self.assertEqual([relay["id"] for relay in loaded["relays"]], [1, 2, 3])
        self.assertEqual(loaded["humidity_control"]["relay_id"], 3)

    def test_save_and_load_config_roundtrip_top_level_overrides(self):
        payload = {
            **config.DEFAULT_CONFIG,
            "camera_device": 3,
            "sensors": {"enabled": False},
        }

        config.save_config(payload)
        loaded = config.load_config()

        self.assertEqual(loaded["camera_device"], 3)
        self.assertEqual(loaded["sensors"]["enabled"], False)
        self.assertIn("read_interval_seconds", loaded["sensors"])

    def test_load_config_appends_missing_default_relay_and_humidity_control(self):
        config.save_config({
            "relays": [
                {"id": 1, "name": "Light", "gpio_pin": 7, "active_low": True, "state": False},
                {"id": 2, "name": "Fan", "gpio_pin": 8, "active_low": True, "state": False},
            ],
            "schedules": [],
        })

        loaded = config.load_config()

        self.assertEqual([relay["id"] for relay in loaded["relays"]], [1, 2, 3])
        self.assertEqual(loaded["humidity_control"]["relay_id"], 3)

    def test_load_config_merges_missing_climate_ventilation_defaults(self):
        config.save_config({
            "climate_ventilation": {
                "enabled": False,
                "relay_id": 5,
            }
        })

        loaded = config.load_config()

        self.assertFalse(loaded["climate_ventilation"]["enabled"])
        self.assertEqual(loaded["climate_ventilation"]["relay_id"], 5)
        self.assertEqual(loaded["climate_ventilation"]["max_temperature"], 35.0)


if __name__ == "__main__":
    unittest.main()
