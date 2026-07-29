import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from systemd_watchdog import notify_watchdog

logger = logging.getLogger(__name__)


class GrowboxScheduler:
    """Coordinate schedules, climate relays, alarms, GPIO sync, and timelapse."""

    def __init__(
        self,
        relays: dict,
        camera,
        config: dict,
        mode: dict,
        sensor_hub=None,
        relay_notify: Optional[Callable] = None,
        watchdog_notify: Optional[Callable[[], bool]] = None,
    ):
        self.relays = relays
        self.camera = camera
        self.config = config
        self.mode = mode
        self.sensor_hub = sensor_hub
        self.relay_notify = relay_notify or (lambda relay, event_mode="auto": None)
        self.watchdog_notify = watchdog_notify or notify_watchdog
        self._running = False
        self._thread = None
        self._started_at = time.time()
        self._last_minute: int = -1
        self._timelapse_counter: int = 0
        self._last_humidity_switch_ts: float = 0.0
        self._last_climate_switch_ts: float = 0.0
        self._last_resync_minute: int = -1
        self._vent_temperature_request = False
        self._vent_humidity_request = False
        self._vent_initialized = False
        self._humidity_out_of_range_since: float | None = None
        self._humidity_out_of_range_side: str | None = None
        self._alarms: dict[str, dict] = {}
        self._last_climate_status: dict = {}
        self._state_lock = threading.Lock()

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
            self._check_climate_control(now)
            self._resync_relay_states(now)
            try:
                self.watchdog_notify()
            except OSError:
                logger.exception("Failed to notify systemd watchdog")
            time.sleep(20)

    def _notify(self, relay, event_mode: str) -> None:
        try:
            self.relay_notify(relay, event_mode)
        except TypeError:
            # Preserve compatibility with older one-argument callbacks.
            self.relay_notify(relay)

    def _set_relay(
        self,
        relay,
        desired_state: bool,
        *,
        now_ts: float,
        last_switch_attr: str,
        min_interval: int,
        event_mode: str,
        bypass_interval: bool = False,
    ) -> bool:
        if relay.state == desired_state:
            return False
        last_switch = getattr(self, last_switch_attr)
        if not bypass_interval and last_switch and now_ts - last_switch < min_interval:
            return False
        relay.set(
            desired_state,
            notify=lambda relay_obj: self._notify(relay_obj, event_mode),
        )
        setattr(self, last_switch_attr, now_ts)
        return True

    def _check_relay_schedules(self, now: datetime) -> None:
        if not self.mode.get("auto", True):
            return
        humidity_relay_id = self.config.get("humidity_control", {}).get("relay_id")
        climate_relay_id = self.config.get("climate_ventilation", {}).get("relay_id")
        current_hm = now.strftime("%H:%M")
        for sched in self.config.get("schedules", []):
            if not sched.get("enabled"):
                continue
            if sched.get("relay_id") in (humidity_relay_id, climate_relay_id):
                continue
            relay = self.relays.get(sched["relay_id"])
            if relay is None:
                continue
            if current_hm == sched.get("on_time") and not relay.state:
                relay.set(True, notify=lambda r: self._notify(r, "auto_schedule"))
                logger.info("Schedule: ON  -> %s", relay.name)
            elif current_hm == sched.get("off_time") and relay.state:
                relay.set(False, notify=lambda r: self._notify(r, "auto_schedule"))
                logger.info("Schedule: OFF -> %s", relay.name)

    def _sensor_snapshot(self, now_ts: float) -> tuple[dict, bool]:
        latest = getattr(self.sensor_hub, "latest", None) or {}
        sensors = self.config.get("sensors", {})
        stale_after = max(1, int(sensors.get("stale_after_seconds", 60)))
        last_success_at = getattr(self.sensor_hub, "last_success_at", None)

        if last_success_at is None:
            stale = not latest and now_ts - self._started_at >= stale_after
        else:
            stale = now_ts - float(last_success_at) >= stale_after
        return latest, stale

    def _set_alarm(
        self,
        code: str,
        active: bool,
        *,
        now_ts: float,
        severity: str,
        message: str,
    ) -> None:
        with self._state_lock:
            existing = self._alarms.get(code)
            if active and existing is None:
                alarm = {
                    "code": code,
                    "severity": severity,
                    "message": message,
                    "active_since": datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec="seconds"),
                }
                self._alarms[code] = alarm
                logger.warning("Climate alarm activated [%s]: %s", code, message)
            elif active and existing is not None:
                existing["message"] = message
                existing["severity"] = severity
            elif not active and existing is not None:
                self._alarms.pop(code, None)
                logger.info("Climate alarm cleared [%s]", code)

    def _update_alarms(
        self,
        *,
        now_ts: float,
        temperature: float | None,
        humidity: float | None,
        sensor_stale: bool,
        humidity_low: float,
        humidity_high: float,
        min_temperature: float,
        max_temperature: float,
        alarm_delay: int,
    ) -> None:
        self._set_alarm(
            "sensor_stale",
            sensor_stale,
            now_ts=now_ts,
            severity="critical",
            message="Нет свежих показаний датчика климата",
        )

        if temperature is not None and not sensor_stale:
            overheat_active = "overheat" in self._alarms
            if temperature >= max_temperature:
                overheat_active = True
            elif temperature <= max_temperature - 2.0:
                overheat_active = False
            self._set_alarm(
                "overheat",
                overheat_active,
                now_ts=now_ts,
                severity="critical",
                message=f"Перегрев: {temperature:.1f}°C (порог {max_temperature:.1f}°C)",
            )
            self._set_alarm(
                "low_temperature",
                temperature <= min_temperature,
                now_ts=now_ts,
                severity="warning",
                message=f"Температура достигла защитного минимума {min_temperature:.1f}°C",
            )

        if humidity is None or sensor_stale:
            self._humidity_out_of_range_since = None
            self._humidity_out_of_range_side = None
            self._set_alarm(
                "humidity_out_of_range",
                False,
                now_ts=now_ts,
                severity="warning",
                message="",
            )
            return

        side = "low" if humidity < humidity_low else "high" if humidity > humidity_high else None
        if side is None:
            self._humidity_out_of_range_since = None
            self._humidity_out_of_range_side = None
            active = False
        else:
            if side != self._humidity_out_of_range_side:
                self._humidity_out_of_range_side = side
                self._humidity_out_of_range_since = now_ts
            active = (
                self._humidity_out_of_range_since is not None
                and now_ts - self._humidity_out_of_range_since >= alarm_delay
            )
        message = (
            f"Влажность {humidity:.1f}% остается вне диапазона "
            f"{humidity_low:.1f}–{humidity_high:.1f}% более {alarm_delay // 60} минут"
        )
        self._set_alarm(
            "humidity_out_of_range",
            active,
            now_ts=now_ts,
            severity="warning",
            message=message,
        )

    def _check_climate_control(self, now: datetime) -> None:
        self._evaluate_climate(now, control_humidifier=True, control_ventilation=True)

    def _check_humidity_control(self, now: datetime) -> None:
        """Compatibility wrapper used by existing callers and tests."""
        self._evaluate_climate(now, control_humidifier=True, control_ventilation=False)

    def _check_climate_ventilation(self, now: datetime) -> None:
        """Compatibility wrapper used by existing callers and tests."""
        self._evaluate_climate(now, control_humidifier=False, control_ventilation=True)

    def _evaluate_climate(
        self,
        now: datetime,
        *,
        control_humidifier: bool,
        control_ventilation: bool,
    ) -> None:
        now_ts = now.timestamp()
        latest, sensor_stale = self._sensor_snapshot(now_ts)
        temperature = latest.get("temperature")
        humidity = latest.get("air_humidity")

        humidity_cfg = self.config.get("humidity_control", {})
        target_humidity = float(humidity_cfg.get("target_humidity", 52.5))
        humidity_band = max(0.0, float(humidity_cfg.get("hysteresis", 5.0)))
        humidifier_on = target_humidity - humidity_band / 2.0
        humidifier_off = target_humidity + humidity_band / 2.0

        vent_cfg = self.config.get("climate_ventilation", {})
        target_temperature = float(vent_cfg.get("target_temperature", 26.0))
        temperature_band = max(0.0, float(vent_cfg.get("temperature_hysteresis", 2.0)))
        temperature_off = target_temperature - temperature_band / 2.0
        temperature_on = target_temperature + temperature_band / 2.0
        humidity_on = float(vent_cfg.get("max_humidity", 60.0))
        vent_humidity_band = max(0.0, float(vent_cfg.get("humidity_hysteresis", 5.0)))
        humidity_off = humidity_on - vent_humidity_band
        min_temperature = float(vent_cfg.get("min_temperature", 18.0))
        max_temperature = float(vent_cfg.get("max_temperature", 35.0))
        alarm_delay = max(0, int(vent_cfg.get("out_of_range_alarm_seconds", 1800)))

        self._update_alarms(
            now_ts=now_ts,
            temperature=temperature,
            humidity=humidity,
            sensor_stale=sensor_stale,
            humidity_low=humidifier_on,
            humidity_high=humidity_on,
            min_temperature=min_temperature,
            max_temperature=max_temperature,
            alarm_delay=alarm_delay,
        )

        vent_relay = self.relays.get(vent_cfg.get("relay_id"))
        humidifier_relay = self.relays.get(humidity_cfg.get("relay_id"))

        if temperature is not None:
            if temperature >= temperature_on:
                self._vent_temperature_request = True
            elif temperature <= temperature_off:
                self._vent_temperature_request = False
            elif not self._vent_initialized and vent_relay is not None:
                self._vent_temperature_request = bool(vent_relay.state)

        if humidity is not None:
            if humidity >= humidity_on:
                self._vent_humidity_request = True
            elif humidity <= humidity_off:
                self._vent_humidity_request = False
            elif not self._vent_initialized and vent_relay is not None:
                self._vent_humidity_request = bool(vent_relay.state)

        if temperature is not None or humidity is not None:
            self._vent_initialized = True

        humidity_floor_blocked = temperature is not None and temperature <= min_temperature
        active_reasons: list[str] = []
        if self._vent_temperature_request:
            active_reasons.append("temperature")
        if self._vent_humidity_request and not humidity_floor_blocked:
            active_reasons.append("humidity")
        ventilation_expected = bool(active_reasons)
        if sensor_stale:
            ventilation_expected = True
            active_reasons = ["sensor_failsafe"]
        elif temperature is None and humidity is None and vent_relay is not None:
            # Preserve the persisted state while the sensor performs its first
            # read. Otherwise a restart could switch the fan off and then keep
            # it off for the minimum-switch interval after a hot first reading.
            ventilation_expected = bool(vent_relay.state)
            active_reasons = ["sensor_initializing"]

        humidifier_expected = humidifier_relay.state if humidifier_relay is not None else False
        if humidity is not None:
            if humidity <= humidifier_on:
                humidifier_expected = True
            elif humidity >= humidifier_off:
                humidifier_expected = False
        if sensor_stale or self._vent_humidity_request:
            humidifier_expected = False

        with self._state_lock:
            self._last_climate_status = {
                "enabled": bool(vent_cfg.get("enabled")),
                "relay_id": vent_cfg.get("relay_id"),
                "current_temperature": temperature,
                "current_humidity": humidity,
                "sensor_stale": sensor_stale,
                "ventilation_expected": ventilation_expected,
                "active_reasons": active_reasons,
                "temperature_on": temperature_on,
                "temperature_off": temperature_off,
                "humidity_on": humidity_on,
                "humidity_off": humidity_off,
                "min_temperature": min_temperature,
                "max_temperature": max_temperature,
                "humidifier_on": humidifier_on,
                "humidifier_off": humidifier_off,
                "humidifier_expected": humidifier_expected,
            }

        if not self.mode.get("auto", True):
            return

        if control_humidifier and humidity_cfg.get("enabled") and humidifier_relay is not None:
            min_interval = max(0, int(humidity_cfg.get("min_switch_interval_seconds", 180)))
            if sensor_stale:
                humidifier_mode = "failsafe_sensor_stale"
            elif self._vent_humidity_request:
                humidifier_mode = "auto_humidity_high"
            elif humidifier_expected:
                humidifier_mode = "auto_humidity_low"
            else:
                humidifier_mode = "auto_humidity_recovered"
            if self._set_relay(
                humidifier_relay,
                humidifier_expected,
                now_ts=now_ts,
                last_switch_attr="_last_humidity_switch_ts",
                min_interval=min_interval,
                event_mode=humidifier_mode,
                bypass_interval=sensor_stale and not humidifier_expected,
            ):
                logger.info(
                    "Humidity control: %s -> %s (humidity=%s, bounds=%.1f/%.1f, reason=%s)",
                    humidifier_relay.name,
                    "ON" if humidifier_expected else "OFF",
                    f"{humidity:.1f}%" if humidity is not None else "stale",
                    humidifier_on,
                    humidifier_off,
                    humidifier_mode,
                )

        if control_ventilation and vent_cfg.get("enabled") and vent_relay is not None:
            min_interval = max(0, int(vent_cfg.get("min_switch_interval_seconds", 180)))
            if sensor_stale:
                vent_mode = "failsafe_sensor_stale"
            elif humidity_floor_blocked and self._vent_humidity_request and not self._vent_temperature_request:
                vent_mode = "auto_min_temperature"
            elif len(active_reasons) == 2:
                vent_mode = "auto_climate_temperature_humidity"
            elif active_reasons:
                vent_mode = f"auto_climate_{active_reasons[0]}"
            else:
                vent_mode = "auto_climate_recovered"
            bypass = sensor_stale or (
                humidity_floor_blocked
                and self._vent_humidity_request
                and not self._vent_temperature_request
                and not ventilation_expected
            )
            if self._set_relay(
                vent_relay,
                ventilation_expected,
                now_ts=now_ts,
                last_switch_attr="_last_climate_switch_ts",
                min_interval=min_interval,
                event_mode=vent_mode,
                bypass_interval=bypass,
            ):
                logger.info(
                    "Climate ventilation: %s -> %s (temp=%s, humidity=%s, reasons=%s)",
                    vent_relay.name,
                    "ON" if ventilation_expected else "OFF",
                    f"{temperature:.1f}°C" if temperature is not None else "stale",
                    f"{humidity:.1f}%" if humidity is not None else "stale",
                    ",".join(active_reasons) or vent_mode,
                )

    def climate_status(self) -> dict:
        with self._state_lock:
            return dict(self._last_climate_status)

    def alarms(self) -> list[dict]:
        with self._state_lock:
            return [dict(alarm) for alarm in self._alarms.values()]

    def _resync_relay_states(self, now: datetime) -> None:
        """Every 15 minutes, re-apply GPIO state for all relays to prevent drift."""
        if not self.mode.get("auto", True):
            return
        minute_of_day = now.hour * 60 + now.minute
        resync_slot = minute_of_day // 15
        if resync_slot == self._last_resync_minute:
            return
        self._last_resync_minute = resync_slot
        for relay in self.relays.values():
            if hasattr(relay, "_apply"):
                relay._apply()
        logger.info("Relay GPIO states re-synced")

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
        except Exception as exc:
            logger.error("Timelapse error: %s", exc)
