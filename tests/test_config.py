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
        self.assertEqual(loaded["relays"][0]["id"], config.DEFAULT_CONFIG["relays"][0]["id"])

    def test_save_and_load_config_roundtrip_top_level_overrides(self):
        payload = {
            **config.DEFAULT_CONFIG,
            "telegram_token": "token",
            "camera_device": 3,
            "sensors": {"enabled": False},
        }

        config.save_config(payload)
        loaded = config.load_config()

        self.assertEqual(loaded["telegram_token"], "token")
        self.assertEqual(loaded["camera_device"], 3)
        self.assertEqual(loaded["sensors"], {"enabled": False})


if __name__ == "__main__":
    unittest.main()
