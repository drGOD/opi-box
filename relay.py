import logging

logger = logging.getLogger(__name__)


class Relay:
    """Controls a single relay via GPIO (or mock if GPIO unavailable)."""

    def __init__(self, relay_id: int, name: str, gpio_pin: int,
                 active_low: bool = True, gpio_chip: str = "gpiochip0"):
        self.id = relay_id
        self.name = name
        self.gpio_pin = gpio_pin
        self.active_low = active_low
        self.gpio_chip = gpio_chip
        self.state = False
        self._mock = False
        self._request = None
        self._setup()

    def _chip_path(self) -> str:
        chip = self.gpio_chip
        return chip if chip.startswith("/") else f"/dev/{chip}"

    def _setup(self) -> None:
        try:
            import gpiod
            from gpiod.line import Direction, Value  # noqa: F401 — confirms v2 is available
            settings = gpiod.LineSettings(direction=Direction.OUTPUT)
            self._request = gpiod.request_lines(
                self._chip_path(),
                consumer="growbox",
                config={self.gpio_pin: settings},
            )
            self._apply()
            logger.info("Relay '%s' on %s:%d ready", self.name, self.gpio_chip, self.gpio_pin)
        except Exception as exc:
            logger.warning("GPIO init failed for '%s': %s — mock mode", self.name, exc)
            self._mock = True

    def set(self, state: bool, notify=None) -> bool:
        old = self.state
        self.state = state
        self._apply()
        if notify and old != state:
            try:
                notify(self)
            except Exception as exc:
                logger.error("Notify callback error: %s", exc)
        return self.state

    def toggle(self, notify=None) -> bool:
        return self.set(not self.state, notify)

    def _apply(self) -> None:
        if self._mock or self._request is None:
            return
        try:
            from gpiod.line import Value
            active = not self.state if self.active_low else self.state
            self._request.set_value(self.gpio_pin, Value.ACTIVE if active else Value.INACTIVE)
        except Exception as exc:
            logger.error("GPIO write error for '%s': %s", self.name, exc)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "gpio_pin": self.gpio_pin,
            "mock": self._mock,
        }

    def __del__(self):
        if self._request:
            try:
                self._request.release()
            except Exception:
                pass
