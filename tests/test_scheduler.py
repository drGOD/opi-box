import unittest

from scheduler import GrowboxScheduler


class FakeRelay:
    def __init__(self, state=False):
        self.state = state
        self.name = "Relay"
        self.calls = []

    def set(self, state, notify=None):
        self.state = state
        self.calls.append(state)
        if notify:
            notify(self)


class FakeCamera:
    def __init__(self):
        self.saved = 0

    def save_timelapse_frame(self):
        self.saved += 1
        return "frame.jpg"

    def get_snapshot(self):
        return b"jpeg"


class FakeNotifier:
    def __init__(self):
        self.relay_notifications = 0
        self.timelapse_notifications = 0

    def notify_relay_change(self, relay):
        self.relay_notifications += 1

    def notify_timelapse(self, image_bytes):
        self.timelapse_notifications += 1


class FakeSensorHub:
    def __init__(self, latest=None):
        self.latest = latest or {}


class SchedulerTests(unittest.TestCase):
    def test_schedule_switches_relay_on_at_matching_time(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={1: relay},
            camera=FakeCamera(),
            notifier=FakeNotifier(),
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
            notifier=FakeNotifier(),
            config={"schedules": [{"relay_id": 1, "enabled": True, "on_time": "08:00", "off_time": "22:00"}]},
            mode={"auto": False},
            sensor_hub=FakeSensorHub(),
        )

        class Now:
            def strftime(self, fmt):
                return "08:00"

        scheduler._check_relay_schedules(Now())

        self.assertEqual(relay.calls, [])

    def test_timelapse_respects_interval(self):
        camera = FakeCamera()
        notifier = FakeNotifier()
        scheduler = GrowboxScheduler(
            relays={},
            camera=camera,
            notifier=notifier,
            config={"timelapse_enabled": True, "timelapse_interval_minutes": 2, "telegram_timelapse": True},
            mode={"auto": True},
            sensor_hub=FakeSensorHub(),
        )

        scheduler._tick_timelapse()
        self.assertEqual(camera.saved, 0)
        self.assertEqual(notifier.timelapse_notifications, 0)

        scheduler._tick_timelapse()
        self.assertEqual(camera.saved, 1)
        self.assertEqual(notifier.timelapse_notifications, 1)

    def test_humidity_control_uses_hysteresis_and_skips_inside_band(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={3: relay},
            camera=FakeCamera(),
            notifier=FakeNotifier(),
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
            notifier=FakeNotifier(),
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


if __name__ == "__main__":
    unittest.main()
