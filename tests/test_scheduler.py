import unittest

from scheduler import GrowboxScheduler


class FakeRelay:
    def __init__(self, state=False):
        self.state = state
        self.name = "Relay"
        self.calls = []
        self._apply_calls = 0

    def set(self, state, notify=None):
        self.state = state
        self.calls.append(state)
        if notify:
            notify(self)

    def _apply(self):
        self._apply_calls += 1


class FakeCamera:
    def __init__(self):
        self.saved = 0

    def save_timelapse_frame(self):
        self.saved += 1
        return "frame.jpg"

    def get_snapshot(self):
        return b"jpeg"


class FakeSensorHub:
    def __init__(self, latest=None):
        self.latest = latest or {}


class SchedulerTests(unittest.TestCase):
    def test_schedule_switches_relay_on_at_matching_time(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={1: relay},
            camera=FakeCamera(),
            config={"schedules": [{"relay_id": 1, "enabled": True, "on_time": "08:00", "off_time": "22:00"}]},
            mode={"auto": True},
            sensor_hub=FakeSensorHub(),
        )
        scheduler.relay_notify = lambda relay: None

        class Now:
            def strftime(self, fmt):
                return "08:00"

        scheduler._check_relay_schedules(Now())

        self.assertEqual(relay.calls, [True])

    def test_schedule_does_not_switch_in_manual_mode(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={1: relay},
            camera=FakeCamera(),
            config={"schedules": [{"relay_id": 1, "enabled": True, "on_time": "08:00", "off_time": "22:00"}]},
            mode={"auto": False},
            sensor_hub=FakeSensorHub(),
        )

        class Now:
            def strftime(self, fmt):
                return "08:00"

        scheduler._check_relay_schedules(Now())

        self.assertEqual(relay.calls, [])

    def test_schedule_skips_climate_ventilation_relay(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={2: relay},
            camera=FakeCamera(),
            config={
                "schedules": [{"relay_id": 2, "enabled": True, "on_time": "08:00", "off_time": "22:00"}],
                "climate_ventilation": {"enabled": True, "relay_id": 2},
            },
            mode={"auto": True},
            sensor_hub=FakeSensorHub(),
        )
        scheduler.relay_notify = lambda relay: None

        class Now:
            def strftime(self, fmt):
                return "08:00"

        scheduler._check_relay_schedules(Now())

        self.assertEqual(relay.calls, [])

    def test_timelapse_respects_interval(self):
        camera = FakeCamera()
        scheduler = GrowboxScheduler(
            relays={},
            camera=camera,
            config={"timelapse_enabled": True, "timelapse_interval_minutes": 2},
            mode={"auto": True},
            sensor_hub=FakeSensorHub(),
        )

        scheduler._tick_timelapse()
        self.assertEqual(camera.saved, 0)

        scheduler._tick_timelapse()
        self.assertEqual(camera.saved, 1)

    def test_humidity_control_uses_hysteresis_and_skips_inside_band(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={3: relay},
            camera=FakeCamera(),
            config={
                "humidity_control": {
                    "enabled": True,
                    "relay_id": 3,
                    "target_humidity": 60,
                    "hysteresis": 10,
                    "min_switch_interval_seconds": 0,
                },
            },
            mode={"auto": True},
            sensor_hub=FakeSensorHub({"air_humidity": 54}),
        )
        scheduler.relay_notify = lambda relay_obj: None

        class Now:
            def timestamp(self):
                return 1000

        scheduler._check_humidity_control(Now())
        self.assertEqual(relay.calls, [True])

        scheduler.sensor_hub.latest = {"air_humidity": 58}
        scheduler._check_humidity_control(Now())
        self.assertEqual(relay.calls, [True])

        scheduler.sensor_hub.latest = {"air_humidity": 66}
        scheduler._check_humidity_control(Now())
        self.assertEqual(relay.calls, [True, False])

    def test_humidity_control_respects_min_switch_interval(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={3: relay},
            camera=FakeCamera(),
            config={
                "humidity_control": {
                    "enabled": True,
                    "relay_id": 3,
                    "target_humidity": 60,
                    "hysteresis": 4,
                    "min_switch_interval_seconds": 180,
                },
            },
            mode={"auto": True},
            sensor_hub=FakeSensorHub({"air_humidity": 50}),
        )
        scheduler.relay_notify = lambda relay_obj: None

        class Now:
            def __init__(self, ts):
                self._ts = ts

            def timestamp(self):
                return self._ts

        scheduler._check_humidity_control(Now(1000))
        scheduler.sensor_hub.latest = {"air_humidity": 70}
        scheduler._check_humidity_control(Now(1100))
        self.assertEqual(relay.calls, [True])

        scheduler._check_humidity_control(Now(1300))
        self.assertEqual(relay.calls, [True, False])

    # --- Climate ventilation tests ---

    def test_climate_ventilation_turns_on_high_humidity(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={2: relay},
            camera=FakeCamera(),
            config={
                "climate_ventilation": {
                    "enabled": True,
                    "relay_id": 2,
                    "max_humidity": 80,
                    "min_humidity": 40,
                    "max_temperature": 35,
                    "min_temperature": 18,
                    "max_co2_ppm": 1500,
                    "min_switch_interval_seconds": 0,
                },
            },
            mode={"auto": True},
            sensor_hub=FakeSensorHub({"air_humidity": 85, "temperature": 25}),
        )
        scheduler.relay_notify = lambda r: None

        class Now:
            def timestamp(self):
                return 1000

        scheduler._check_climate_ventilation(Now())
        self.assertEqual(relay.calls, [True])

    def test_climate_ventilation_turns_off_low_readings(self):
        relay = FakeRelay(state=True)
        scheduler = GrowboxScheduler(
            relays={2: relay},
            camera=FakeCamera(),
            config={
                "climate_ventilation": {
                    "enabled": True,
                    "relay_id": 2,
                    "max_humidity": 80,
                    "min_humidity": 40,
                    "max_temperature": 35,
                    "min_temperature": 18,
                    "max_co2_ppm": 1500,
                    "min_switch_interval_seconds": 0,
                },
            },
            mode={"auto": True},
            sensor_hub=FakeSensorHub({"air_humidity": 35, "temperature": 16}),
        )
        scheduler.relay_notify = lambda r: None

        class Now:
            def timestamp(self):
                return 1000

        scheduler._check_climate_ventilation(Now())
        self.assertEqual(relay.calls, [False])

    def test_climate_ventilation_turns_on_high_co2(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={2: relay},
            camera=FakeCamera(),
            config={
                "climate_ventilation": {
                    "enabled": True,
                    "relay_id": 2,
                    "max_humidity": 80,
                    "min_humidity": 40,
                    "max_temperature": 35,
                    "min_temperature": 18,
                    "max_co2_ppm": 1500,
                    "min_switch_interval_seconds": 0,
                },
            },
            mode={"auto": True},
            sensor_hub=FakeSensorHub({"air_humidity": 50, "temperature": 25, "eco2_ppm": 2000}),
        )
        scheduler.relay_notify = lambda r: None

        class Now:
            def timestamp(self):
                return 1000

        scheduler._check_climate_ventilation(Now())
        self.assertEqual(relay.calls, [True])

    def test_climate_ventilation_keeps_state_in_normal_range(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={2: relay},
            camera=FakeCamera(),
            config={
                "climate_ventilation": {
                    "enabled": True,
                    "relay_id": 2,
                    "max_humidity": 80,
                    "min_humidity": 40,
                    "max_temperature": 35,
                    "min_temperature": 18,
                    "max_co2_ppm": 1500,
                    "min_switch_interval_seconds": 0,
                },
            },
            mode={"auto": True},
            sensor_hub=FakeSensorHub({"air_humidity": 60, "temperature": 25}),
        )
        scheduler.relay_notify = lambda r: None

        class Now:
            def timestamp(self):
                return 1000

        scheduler._check_climate_ventilation(Now())
        self.assertEqual(relay.calls, [])

    def test_climate_ventilation_respects_min_switch_interval(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={2: relay},
            camera=FakeCamera(),
            config={
                "climate_ventilation": {
                    "enabled": True,
                    "relay_id": 2,
                    "max_humidity": 80,
                    "min_humidity": 40,
                    "max_temperature": 35,
                    "min_temperature": 18,
                    "max_co2_ppm": 1500,
                    "min_switch_interval_seconds": 180,
                },
            },
            mode={"auto": True},
            sensor_hub=FakeSensorHub({"air_humidity": 85, "temperature": 25}),
        )
        scheduler.relay_notify = lambda r: None

        class Now:
            def __init__(self, ts):
                self._ts = ts
            def timestamp(self):
                return self._ts

        scheduler._check_climate_ventilation(Now(1000))
        self.assertEqual(relay.calls, [True])

        # Try to switch off too soon
        scheduler.sensor_hub.latest = {"air_humidity": 35, "temperature": 16}
        scheduler._check_climate_ventilation(Now(1100))
        self.assertEqual(relay.calls, [True])  # blocked by interval

        scheduler._check_climate_ventilation(Now(1300))
        self.assertEqual(relay.calls, [True, False])

    def test_climate_ventilation_disabled_does_nothing(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={2: relay},
            camera=FakeCamera(),
            config={
                "climate_ventilation": {
                    "enabled": False,
                    "relay_id": 2,
                    "max_humidity": 80,
                    "min_humidity": 40,
                },
            },
            mode={"auto": True},
            sensor_hub=FakeSensorHub({"air_humidity": 95}),
        )

        class Now:
            def timestamp(self):
                return 1000

        scheduler._check_climate_ventilation(Now())
        self.assertEqual(relay.calls, [])

    # --- Resync tests ---

    def test_resync_calls_apply_on_all_relays(self):
        relay1 = FakeRelay(state=True)
        relay2 = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={1: relay1, 2: relay2},
            camera=FakeCamera(),
            config={},
            mode={"auto": True},
            sensor_hub=FakeSensorHub(),
        )

        class Now:
            def __init__(self, h, m):
                self.hour = h
                self.minute = m

        # First call at minute 0 (slot 0)
        scheduler._resync_relay_states(Now(0, 0))
        self.assertEqual(relay1._apply_calls, 1)
        self.assertEqual(relay2._apply_calls, 1)

        # Same slot — should not re-apply
        scheduler._resync_relay_states(Now(0, 5))
        self.assertEqual(relay1._apply_calls, 1)

        # Next slot (minute 15)
        scheduler._resync_relay_states(Now(0, 15))
        self.assertEqual(relay1._apply_calls, 2)
        self.assertEqual(relay2._apply_calls, 2)

    def test_resync_skipped_in_manual_mode(self):
        relay = FakeRelay(state=True)
        scheduler = GrowboxScheduler(
            relays={1: relay},
            camera=FakeCamera(),
            config={},
            mode={"auto": False},
            sensor_hub=FakeSensorHub(),
        )

        class Now:
            def __init__(self):
                self.hour = 0
                self.minute = 0

        scheduler._resync_relay_states(Now())
        self.assertEqual(relay._apply_calls, 0)


if __name__ == "__main__":
    unittest.main()
