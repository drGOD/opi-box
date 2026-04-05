import importlib
import unittest


class FakeResponse:
    def __init__(self, ok, text=""):
        self.ok = ok
        self.text = text


class TelegramNotifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.telegram_bot = importlib.import_module("telegram_bot")
        except Exception as exc:  # pragma: no cover - local env fallback
            cls._import_error = exc
            cls.TelegramNotifier = None
        else:
            cls._import_error = None
            cls.TelegramNotifier = cls.telegram_bot.TelegramNotifier

    def setUp(self):
        if self._import_error is not None:
            self.skipTest(f"telegram_bot import unavailable: {self._import_error}")
        self._old_post = self.telegram_bot.requests.post

    def tearDown(self):
        if self._import_error is None:
            self.telegram_bot.requests.post = self._old_post

    def test_send_message_skips_when_not_configured(self):
        notifier = self.TelegramNotifier("", "")

        self.assertFalse(notifier.send_message("hello"))

    def test_send_message_posts_to_telegram_api(self):
        calls = []

        def fake_post(url, json=None, timeout=None, **kwargs):
            calls.append((url, json, timeout))
            return FakeResponse(True)

        self.telegram_bot.requests.post = fake_post
        notifier = self.TelegramNotifier("token", "chat")

        result = notifier.send_message("hello")

        self.assertTrue(result)
        self.assertEqual(calls[0][0], "https://api.telegram.org/bottoken/sendMessage")
        self.assertEqual(calls[0][1]["text"], "hello")
        self.assertEqual(calls[0][2], 10)

    def test_send_photo_handles_request_error(self):
        def fake_post(*args, **kwargs):
            raise RuntimeError("boom")

        self.telegram_bot.requests.post = fake_post
        notifier = self.TelegramNotifier("token", "chat")

        self.assertFalse(notifier.send_photo(b"img", "caption"))

    def test_notify_helpers_delegate_to_transport(self):
        notifier = self.TelegramNotifier("token", "chat")
        messages = []
        photos = []
        notifier.send_message = lambda text: messages.append(text) or True
        notifier.send_photo = lambda image, caption="": photos.append((image, caption)) or True

        class FakeRelay:
            name = "vent"
            state = True

        notifier.notify_relay_change(FakeRelay())
        notifier.notify_timelapse(b"img")
        notifier.notify_startup()

        self.assertEqual(len(messages), 2)
        self.assertIn("<b>vent</b>", messages[0])
        self.assertEqual(photos[0][0], b"img")


if __name__ == "__main__":
    unittest.main()
