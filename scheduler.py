import threading
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class GrowboxScheduler:
    """
    Background thread that:
      - Enforces relay on/off schedules (checked every 30 s) — only in auto mode
      - Triggers timelapse snapshots at the configured interval
    """

    def __init__(self, relays: dict, camera, notifier, config: dict, mode: dict):
        self.relays   = relays        # {relay_id: Relay}
        self.camera   = camera
        self.notifier = notifier
        self.config   = config
        self.mode     = mode          # shared {"auto": bool}
        self._running = False
        self._thread  = None
        self._last_minute: int = -1
        self._timelapse_counter: int = 0

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        self._running = False

    # ── Main loop ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            now = datetime.now()
            minute_of_day = now.hour * 60 + now.minute
            if minute_of_day != self._last_minute:
                self._last_minute = minute_of_day
                self._check_relay_schedules(now)
                self._tick_timelapse()
            time.sleep(20)

    # ── Relay schedule ─────────────────────────────────────────────────────

    def _check_relay_schedules(self, now: datetime) -> None:
        if not self.mode.get("auto", True):
            return  # manual mode — do not touch relays
        current_hm = now.strftime("%H:%M")
        for sched in self.config.get("schedules", []):
            if not sched.get("enabled"):
                continue
            relay = self.relays.get(sched["relay_id"])
            if relay is None:
                continue
            if current_hm == sched.get("on_time") and not relay.state:
                relay.set(True, notify=self._notify_relay)
                logger.info("Schedule: ON  → %s", relay.name)
            elif current_hm == sched.get("off_time") and relay.state:
                relay.set(False, notify=self._notify_relay)
                logger.info("Schedule: OFF → %s", relay.name)

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

    # ── Timelapse ──────────────────────────────────────────────────────────

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
