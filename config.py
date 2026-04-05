import json
from copy import deepcopy
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
        {"id": 1, "name": "Свет", "gpio_pin": 7, "active_low": True, "state": False},
        {"id": 2, "name": "Вентиляция", "gpio_pin": 8, "active_low": True, "state": False},
        {"id": 3, "name": "Увлажнитель", "gpio_pin": 9, "active_low": True, "state": False},
    ],
    "schedules": [
        {"relay_id": 1, "enabled": True, "on_time": "08:00", "off_time": "22:00"},
        {"relay_id": 2, "enabled": True, "on_time": "08:00", "off_time": "22:00"},
        {"relay_id": 3, "enabled": False, "on_time": "00:00", "off_time": "00:00"},
    ],
    "humidity_control": {
        "enabled": False,
        "relay_id": 3,
        "target_humidity": 65.0,
        "hysteresis": 6.0,
        "min_switch_interval_seconds": 180,
    },
    "sensors": {
        "enabled": True,
        "i2c_bus": 2,
        "read_interval_seconds": 30,
        "soil_dry": [26000, 26000],
        "soil_wet": [13000, 13000],
    },
}

BROKEN_RELAY_NAMES = {
    1: "Р РЋР Р†Р ВµРЎвЂљ",
    2: "Р вЂ™Р ВµР Р…РЎвЂљР С‘Р В»РЎРЏРЎвЂ Р С‘РЎРЏ",
    3: "Р Р€Р Р†Р В»Р В°Р В¶Р Р…Р С‘РЎвЂљР ВµР В»РЎРЉ",
}


def _normalize_relay_name(relay: dict) -> dict:
    normalized = dict(relay)
    default_name = next(
        (item["name"] for item in DEFAULT_CONFIG["relays"] if item["id"] == normalized.get("id")),
        None,
    )
    broken_name = BROKEN_RELAY_NAMES.get(normalized.get("id"))
    if default_name and normalized.get("name") == broken_name:
        normalized["name"] = default_name
    return normalized


def _merge_relay_lists(data: dict) -> list:
    relays = [_normalize_relay_name(item) for item in data.get("relays", [])]
    existing = {item.get("id") for item in relays}
    for item in DEFAULT_CONFIG["relays"]:
        if item["id"] not in existing:
            relays.append(dict(item))
    return relays


def _merge_schedule_lists(data: dict) -> list:
    schedules = [dict(item) for item in data.get("schedules", [])]
    existing = {item.get("relay_id") for item in schedules}
    for item in DEFAULT_CONFIG["schedules"]:
        if item["relay_id"] not in existing:
            schedules.append(dict(item))
    return schedules


def _merge_config(data: dict) -> dict:
    merged = deepcopy(DEFAULT_CONFIG)
    merged.update(data)

    sensors = deepcopy(DEFAULT_CONFIG["sensors"])
    sensors.update(data.get("sensors", {}))
    merged["sensors"] = sensors

    humidity_control = deepcopy(DEFAULT_CONFIG["humidity_control"])
    humidity_control.update(data.get("humidity_control", {}))
    merged["humidity_control"] = humidity_control

    merged["relays"] = _merge_relay_lists(data)
    merged["schedules"] = _merge_schedule_lists(data)
    return merged


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return _merge_config(DEFAULT_CONFIG)

    with open(CONFIG_FILE, encoding="utf-8") as f:
        data = json.load(f)

    merged = _merge_config(data)
    if merged != data:
        save_config(merged)
    return merged


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
