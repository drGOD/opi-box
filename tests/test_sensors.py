import sys
import unittest

if sys.version_info >= (3, 9):
    from sensors import AHT21, SensorHub
else:
    AHT21 = None
    SensorHub = None


class FakeAHT:
    def read(self):
        return 23.44, 55.55


class FakeENS:
    def __init__(self):
        self.compensation = []

    def set_compensation(self, temp, hum):
        self.compensation.append((temp, hum))

    def read(self):
        return {"aqi": 2, "tvoc_ppb": 15, "eco2_ppm": 700, "validity": "Normal"}


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


class FakeAHTBus:
    def __init__(self, data):
        self.data = data
        self.init_writes = 0
        self.measure_writes = 0

    def write_i2c_block_data(self, addr, register, values):
        if register == AHT21.CMD_INIT[0]:
            self.init_writes += 1
        if register == AHT21.CMD_MEASURE[0]:
            self.measure_writes += 1

    def read_byte(self, addr):
        return 0x18

    def read_i2c_block_data(self, addr, register, count):
        return list(self.data)


@unittest.skipUnless(SensorHub is not None, "SensorHub requires Python 3.9+")
class SensorHubTests(unittest.TestCase):
    def setUp(self):
        self._old_setup = SensorHub._setup
        SensorHub._setup = lambda self: None

    def tearDown(self):
        SensorHub._setup = self._old_setup

    def test_read_once_collects_all_sensor_values_and_calls_callback(self):
        readings = []
        hub = SensorHub(
            {"sensors": {"soil_dry": [26000, 26000], "soil_wet": [13000, 13000]}},
            on_reading=readings.append,
        )
        hub._aht = FakeAHT()
        hub._ens = FakeENS()
        hub._ads = FakeADS()

        hub._read_once()

        self.assertEqual(hub.latest["temperature"], 23.4)
        self.assertEqual(hub.latest["air_humidity"], 55.5)
        self.assertEqual(hub.latest["eco2_ppm"], 700)
        self.assertEqual(hub.latest["soil"][0]["moisture_pct"], 46.2)
        self.assertEqual(hub.latest["soil"][1]["moisture_pct"], 84.6)
        self.assertEqual(readings[0]["aqi"], 2)
        self.assertEqual(hub._ens.compensation[0], (23.44, 55.55))

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
        hub._ens = FakeENS()
        hub._ads = FakeADS()

        hub._read_once()

        self.assertNotIn("air_humidity", hub.latest)
        self.assertEqual(hub.latest["eco2_ppm"], 700)
        self.assertEqual(hub.latest["soil"][0]["moisture_pct"], 46.2)
        self.assertEqual(readings[0]["aqi"], 2)

    def test_available_and_close_reflect_current_hardware(self):
        hub = SensorHub({"sensors": {}}, on_reading=None)
        hub._bus = FakeBus()
        hub._ads = FakeADS()

        self.assertTrue(hub.available)
        hub.close()
        self.assertTrue(hub._bus.closed)


@unittest.skipUnless(AHT21 is not None, "AHT21 requires Python 3.9+")
class AHT21Tests(unittest.TestCase):
    def test_read_accepts_crc_calculated_from_measurement_bytes(self):
        payload = [0x08, 0x80, 0x00, 0x06, 0x00]
        crc = AHT21._crc8(payload)
        bus = FakeAHTBus([0x18, *payload, crc])

        sensor = AHT21(bus)
        temperature, humidity = sensor.read()

        self.assertEqual(bus.measure_writes, 1)
        self.assertAlmostEqual(temperature, 25.0, places=1)
        self.assertAlmostEqual(humidity, 50.0, places=1)


if __name__ == "__main__":
    unittest.main()
