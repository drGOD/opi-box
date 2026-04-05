import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class GrowboxScheduler:
    """
    Background thread that:
      - Enforces relay schedules in auto mode
      - Controls a humidifier relay from air humidity with hysteresis
      - Triggers timelapse snapshots at the configured interval
    """

    def __init__(
        self,
        relays: dict,
        camera,
        notifier,
        config: dict,
        mode: dict,
        sensor_hub=None,
        relay_notify: Optional[Callable] = None,
    ):
        self.relays = relays
        self.camera = camera
        self.notifier = notifier
        self.config = config
        self.mode = mode
        self.sensor_hub = sensor_hub
        self.relay_notify = relay_notify or self._notify_relay
        self._running = False
        self._thread = None
        self._last_minute: int = -1
        self._timelapse_counter: int = 0
        self._last_humidity_switch_ts: float = 0.0

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            now = datetime.now()
            minute_of_day = now.hour * 60 + now.minute
            if minute_of_day != self._last_minute:
                self._last_minute = minute_of_day
                self._check_relay_schedules(now)
                self._tick_timelapse()
            self._check_humidity_control(now)
            time.sleep(20)

    def _check_relay_schedules(self, now: datetime) -> None:
        if not self.mode.get("auto", True):
            return
        humidity_relay_id = self.config.get("humidity_control", {}).get("relay_id")
        current_hm = now.strftime("%H:%M")
        for sched in self.config.get("schedules", []):
            if not sched.get("enabled"):
                continue
            if sched.get("relay_id") == humidity_relay_id:
                continue
            relay = self.relays.get(sched["relay_id"])
            if relay is None:
                continue
            if current_hm == sched.get("on_time") and not relay.state:
                relay.set(True, notify=self.relay_notify)
                logger.info("Schedule: ON  -> %s", relay.name)
            elif current_hm == sched.get("off_time") and relay.state:
                relay.set(False, notify=self.relay_notify)
                logger.info("Schedule: OFF -> %s", relay.name)

    def _check_humidity_control(self, now: datetime) -> None:
        if not self.mode.get("auto", True):
            return

        control = self.config.get("humidity_control", {})
        if not control.get("enabled"):
            return

        relay = self.relays.get(control.get("relay_id"))
        if relay is None:
            return

        latest = getattr(self.sensor_hub, "latest", None) or {}
        humidity = latest.get("air_humidity")
        if humidity is None:
            return

        target = float(control.get("target_humidity", 65.0))
        hysteresis = max(0.0, float(control.get("hysteresis", 6.0)))
        min_interval = max(0, int(control.get("min_switch_interval_seconds", 180)))
        lower_bound = target - hysteresis / 2.0
        upper_bound = target + hysteresis / 2.0

        desired_state = None
        if humidity <= lower_bound:
            desired_state = True
        elif humidity >= upper_bound:
            desired_state = False

        if desired_state is None or desired_state == relay.state:
            return

        now_ts = now.timestamp()
        if self._last_humidity_switch_ts and now_ts - self._last_humidity_switch_ts < min_interval:
            return

        relay.set(desired_state, notify=self.relay_notify)
        self._last_humidity_switch_ts = now_ts
        logger.info(
            "Humidity control: %s -> %s (humidity=%.1f%%, target=%.1f%%, band=%.1f%%)",
            relay.name,
            "ON" if desired_state else "OFF",
            humidity,
            target,
            hysteresis,
        )

    def _notify_relay(self, relay) -> None:
        self.notifier.notify_relay_change(relay)
        from database import insert_relay_event
        insert_relay_event(relay.id, relay.name, relay.state, "auto")
        from config import load_config, save_config
        cfg = load_config()
        for r in cfg["relays"]:
            if r["id"] == relay.id:
                r["state"] = relay.state
        save_config(cfg)

    def _tick_timelapse(self) -> None:
        if not self.config.get("timelapse_enabled", True):
            return
        interval = self.config.get("timelapse_interval_minutes", 30)
        self._timelapse_counter += 1
        if self._timelapse_counter < interval:
            return
        self._timelapse_counter = 0
        try:
            path = self.camera.save_timelapse_frame()
            if path:
                logger.info("Timelapse frame saved: %s", path)
                if self.config.get("telegram_timelapse", True):
                    snap = self.camera.get_snapshot()
                    if snap:
                        self.notifier.notify_timelapse(snap)
        except Exception as exc:
            logger.error("Timelapse error: %s", exc)
