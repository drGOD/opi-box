import unittest

import relay


class RelayTests(unittest.TestCase):
    def setUp(self):
        self._old_setup = relay.Relay._setup

    def tearDown(self):
        relay.Relay._setup = self._old_setup

    def test_chip_path_uses_dev_prefix_for_chip_name(self):
        relay.Relay._setup = lambda self: None
        item = relay.Relay(1, "Light", 7, gpio_chip="gpiochip0")

        self.assertEqual(item._chip_path(), "/dev/gpiochip0")

    def test_chip_path_keeps_absolute_path(self):
        relay.Relay._setup = lambda self: None
        item = relay.Relay(1, "Light", 7, gpio_chip="/dev/customchip")

        self.assertEqual(item._chip_path(), "/dev/customchip")

    def test_set_notifies_only_on_state_change(self):
        relay.Relay._setup = lambda self: setattr(self, "_mock", True)
        item = relay.Relay(1, "Light", 7)
        calls = []

        item.set(True, notify=lambda relay_obj: calls.append(relay_obj.state))
        item.set(True, notify=lambda relay_obj: calls.append(relay_obj.state))
        item.toggle(notify=lambda relay_obj: calls.append(relay_obj.state))

        self.assertEqual(calls, [True, False])
        self.assertFalse(item.state)
        self.assertTrue(item.to_dict()["mock"])

    def test_close_releases_request(self):
        relay.Relay._setup = lambda self: None
        item = relay.Relay(1, "Light", 7)

        class FakeRequest:
            def __init__(self):
                self.released = False

            def release(self):
                self.released = True

        request = FakeRequest()
        item._request = request
        item.close()

        self.assertTrue(request.released)
        self.assertIsNone(item._request)


if __name__ == "__main__":
    unittest.main()
