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
        self._line = None
        self._setup()

    def _setup(self) -> None:
        try:
            import gpiod
            chip = gpiod.Chip(self.gpio_chip)
            self._line = chip.get_line(self.gpio_pin)
            self._line.request(consumer="growbox", type=gpiod.LINE_REQ_DIR_OUT)
            self._apply()
            logger.info("Relay '%s' on GPIO %d ready", self.name, self.gpio_pin)
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
        if self._mock or self._line is None:
            return
        try:
            value = (not self.state) if self.active_low else self.state
            self._line.set_value(int(value))
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
        if self._line:
            try:
                self._line.release()
            except Exception:
                pass
