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


class SchedulerTests(unittest.TestCase):
    def test_schedule_switches_relay_on_at_matching_time(self):
        relay = FakeRelay(state=False)
        scheduler = GrowboxScheduler(
            relays={1: relay},
            camera=FakeCamera(),
            notifier=FakeNotifier(),
            config={"schedules": [{"relay_id": 1, "enabled": True, "on_time": "08:00", "off_time": "22:00"}]},
            mode={"auto": True},
        )
        scheduler._notify_relay = lambda relay: None

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
        )

        scheduler._tick_timelapse()
        self.assertEqual(camera.saved, 0)
        self.assertEqual(notifier.timelapse_notifications, 0)

        scheduler._tick_timelapse()
        self.assertEqual(camera.saved, 1)
        self.assertEqual(notifier.timelapse_notifications, 1)


if __name__ == "__main__":
    unittest.main()
