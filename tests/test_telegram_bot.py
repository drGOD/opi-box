import unittest

import telegram_bot
from telegram_bot import TelegramNotifier


class FakeResponse:
    def __init__(self, ok, text=""):
        self.ok = ok
        self.text = text


class TelegramNotifierTests(unittest.TestCase):
    def setUp(self):
        self._old_post = telegram_bot.requests.post

    def tearDown(self):
        telegram_bot.requests.post = self._old_post

    def test_send_message_skips_when_not_configured(self):
        notifier = TelegramNotifier("", "")

        self.assertFalse(notifier.send_message("hello"))

    def test_send_message_posts_to_telegram_api(self):
        calls = []

        def fake_post(url, json=None, timeout=None, **kwargs):
            calls.append((url, json, timeout))
            return FakeResponse(True)

        telegram_bot.requests.post = fake_post
        notifier = TelegramNotifier("token", "chat")

        result = notifier.send_message("hello")

        self.assertTrue(result)
        self.assertEqual(calls[0][0], "https://api.telegram.org/bottoken/sendMessage")
        self.assertEqual(calls[0][1]["text"], "hello")
        self.assertEqual(calls[0][2], 10)

    def test_send_photo_handles_request_error(self):
        def fake_post(*args, **kwargs):
            raise RuntimeError("boom")

        telegram_bot.requests.post = fake_post
        notifier = TelegramNotifier("token", "chat")

        self.assertFalse(notifier.send_photo(b"img", "caption"))

    def test_notify_helpers_delegate_to_transport(self):
        notifier = TelegramNotifier("token", "chat")
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
