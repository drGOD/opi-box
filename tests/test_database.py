import tempfile
import time
import unittest
from pathlib import Path

import database


class DatabaseHistoryTests(unittest.TestCase):
    def setUp(self):
        self._old_db_file = database.DB_FILE
        self._tmp_dir = tempfile.TemporaryDirectory()
        database.DB_FILE = Path(self._tmp_dir.name) / "test_growbox.db"
        database.init_db()

    def tearDown(self):
        database.DB_FILE = self._old_db_file
        self._tmp_dir.cleanup()

    def test_get_history_includes_prior_relay_state_at_window_start(self):
        now = time.time()
        since = now - 3600

        with database._conn() as conn:
            conn.execute(
                "INSERT INTO relay_events (ts, relay_id, relay_name, state, mode) VALUES (?, ?, ?, ?, ?)",
                (since - 120, 1, "Light", 1, "auto"),
            )
            conn.execute(
                "INSERT INTO relay_events (ts, relay_id, relay_name, state, mode) VALUES (?, ?, ?, ?, ?)",
                (since + 120, 1, "Light", 0, "auto"),
            )

        history = database.get_history(hours=1, max_points=50)
        events = history["relays"]["1"]

        self.assertEqual(events[0]["state"], 1)
        self.assertAlmostEqual(events[0]["ts"], since, delta=2)
        self.assertEqual(events[1]["state"], 0)

    def test_get_history_downsamples_sensor_points(self):
        now = time.time()

        with database._conn() as conn:
            for idx in range(25):
                conn.execute(
                    """
                    INSERT INTO sensor_readings
                    (ts, temperature, air_humidity, eco2_ppm, tvoc_ppb, aqi, soil0_pct, soil1_pct)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (now - idx * 10, 20 + idx, 50, 600, 10, 1, 30, 40),
                )

        history = database.get_history(hours=1, max_points=5)
        self.assertLessEqual(len(history["sensors"]), 5)

    def test_insert_helpers_and_cleanup_old_data(self):
        old_ts = time.time() - 10 * 86400

        with database._conn() as conn:
            conn.execute(
                """
                INSERT INTO sensor_readings
                (ts, temperature, air_humidity, eco2_ppm, tvoc_ppb, aqi, soil0_pct, soil1_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (old_ts, 18, 40, 500, 9, 1, 20, 30),
            )
            conn.execute(
                "INSERT INTO relay_events (ts, relay_id, relay_name, state, mode) VALUES (?, ?, ?, ?, ?)",
                (old_ts, 1, "Light", 1, "auto"),
            )

        database.insert_sensor_reading(
            {
                "temperature": 22.2,
                "air_humidity": 60.1,
                "eco2_ppm": 650,
                "tvoc_ppb": 11,
                "aqi": 2,
                "soil": [
                    {"channel": 0, "moisture_pct": 33.3},
                    {"channel": 1, "moisture_pct": 44.4},
                ],
            }
        )
        database.insert_relay_event(2, "Fan", False, "manual")
        database.cleanup_old_data(keep_days=1)

        with database._conn() as conn:
            sensor_rows = conn.execute("SELECT soil0_pct, soil1_pct FROM sensor_readings").fetchall()
            relay_rows = conn.execute("SELECT relay_id, state, mode FROM relay_events").fetchall()

        self.assertEqual(len(sensor_rows), 1)
        self.assertEqual(sensor_rows[0]["soil0_pct"], 33.3)
        self.assertEqual(sensor_rows[0]["soil1_pct"], 44.4)
        self.assertEqual(len(relay_rows), 1)
        self.assertEqual(relay_rows[0]["relay_id"], 2)
        self.assertEqual(relay_rows[0]["mode"], "manual")


if __name__ == "__main__":
    unittest.main()
