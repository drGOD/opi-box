import sys
import unittest

if sys.version_info >= (3, 9):
    from sensors import SensorHub
else:
    SensorHub = None


class FakeAHT:
    def read(self):
        return 23.44, 55.55


class FakeADS:
    def __init__(self):
        self.values = {0: 20000, 1: 15000}

    def read_raw(self, channel):
        return self.values[channel]


class FakeBus:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@unittest.skipUnless(SensorHub is not None, "SensorHub requires Python 3.9+")
class SensorHubTests(unittest.TestCase):
    def setUp(self):
        self._old_setup = SensorHub._setup
        SensorHub._setup = lambda self: None

    def tearDown(self):
        SensorHub._setup = self._old_setup

    def test_read_once_collects_air_and_soil_values_and_calls_callback(self):
        readings = []
        hub = SensorHub(
            {"sensors": {"soil_dry": [26000, 26000], "soil_wet": [13000, 13000]}},
            on_reading=readings.append,
        )
        hub._aht = FakeAHT()
        hub._ads = FakeADS()

        hub._read_once()

        self.assertEqual(hub.latest["temperature"], 23.4)
        self.assertEqual(hub.latest["air_humidity"], 55.5)
        self.assertEqual(hub.latest["soil"][0]["moisture_pct"], 46.2)
        self.assertEqual(hub.latest["soil"][1]["moisture_pct"], 84.6)
        self.assertEqual(readings[0]["temperature"], 23.4)

    def test_read_once_handles_sensor_failure_without_callback(self):
        hub = SensorHub({"sensors": {}}, on_reading=lambda data: self.fail("callback should not run"))

        class BrokenAHT:
            def read(self):
                raise RuntimeError("broken")

        hub._aht = BrokenAHT()
        hub._read_once()

        self.assertEqual(hub.latest, {})

    def test_read_once_keeps_other_sensor_values_when_aht_fails(self):
        readings = []
        hub = SensorHub(
            {"sensors": {"soil_dry": [26000, 26000], "soil_wet": [13000, 13000]}},
            on_reading=readings.append,
        )

        class BrokenAHT:
            def read(self):
                raise RuntimeError("broken")

        hub._aht = BrokenAHT()
        hub._ads = FakeADS()

        hub._read_once()

        self.assertNotIn("air_humidity", hub.latest)
        self.assertEqual(hub.latest["soil"][0]["moisture_pct"], 46.2)
        self.assertEqual(readings[0]["soil"][0]["moisture_pct"], 46.2)

    def test_available_and_close_reflect_current_hardware(self):
        hub = SensorHub({"sensors": {}}, on_reading=None)
        hub._bus = FakeBus()
        hub._ads = FakeADS()

        self.assertTrue(hub.available)
        hub.close()
        self.assertTrue(hub._bus.closed)


if __name__ == "__main__":
    unittest.main()
