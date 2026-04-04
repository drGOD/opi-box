import io
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    @property
    def _base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    # ------------------------------------------------------------------ send helpers

    def send_message(self, text: str) -> bool:
        if not self.configured:
            return False
        try:
            r = requests.post(
                f"{self._base}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if not r.ok:
                logger.warning("Telegram sendMessage: %s", r.text)
            return r.ok
        except Exception as exc:
            logger.error("Telegram error: %s", exc)
            return False

    def send_photo(self, image_bytes: bytes, caption: str = "") -> bool:
        if not self.configured:
            return False
        try:
            r = requests.post(
                f"{self._base}/sendPhoto",
                data={"chat_id": self.chat_id, "caption": caption},
                files={"photo": ("snap.jpg", io.BytesIO(image_bytes), "image/jpeg")},
                timeout=30,
            )
            if not r.ok:
                logger.warning("Telegram sendPhoto: %s", r.text)
            return r.ok
        except Exception as exc:
            logger.error("Telegram photo error: %s", exc)
            return False

    # ------------------------------------------------------------------ domain helpers

    def notify_relay_change(self, relay) -> None:
        emoji = "💡" if "свет" in relay.name.lower() else "💨"
        word = "включён" if relay.state else "выключен"
        self.send_message(f"{emoji} <b>{relay.name}</b> {word}")

    def notify_timelapse(self, image_bytes: bytes) -> None:
        caption = f"📸 GrowBox — {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        self.send_photo(image_bytes, caption)

    def notify_startup(self) -> None:
        self.send_message("🌱 <b>GrowBox запущен</b>\nСистема готова к работе.")
