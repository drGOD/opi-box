import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "telegram_token": "",
    "telegram_chat_id": "",
    "telegram_timelapse": True,
    "timelapse_interval_minutes": 30,
    "timelapse_enabled": True,
    "camera_device": 1,  # 0 = cedrus HW decoder on OPi Zero 3, camera starts at 1
    "gpio_chip": "gpiochip0",
    "relays": [
        {"id": 1, "name": "Свет",       "gpio_pin": 7, "active_low": True, "state": False},
        {"id": 2, "name": "Вентиляция", "gpio_pin": 8, "active_low": True, "state": False},
    ],
    "schedules": [
        {"relay_id": 1, "enabled": True, "on_time": "08:00", "off_time": "22:00"},
        {"relay_id": 2, "enabled": True, "on_time": "08:00", "off_time": "22:00"},
    ],
}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, encoding="utf-8") as f:
        data = json.load(f)
    merged = DEFAULT_CONFIG.copy()
    merged.update(data)
    return merged


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
