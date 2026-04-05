"""
Sensor management: AHT21 (temp/humidity), ENS160 (CO₂/TVOC/AQI), ADS1115 (soil moisture)
Runs a background thread; each sensor is optional — failures are logged and skipped.
"""
import threading
import time
import logging

logger = logging.getLogger(__name__)


# ── Low-level drivers ──────────────────────────────────────────────────────

class AHT21:
    CMD_INIT = [0xBE, 0x08, 0x00]
    CMD_MEASURE = [0xAC, 0x33, 0x00]

    def __init__(self, bus, addr: int = 0x38):
        self.bus = bus
        self.addr = addr
        bus.write_i2c_block_data(addr, self.CMD_INIT[0], self.CMD_INIT[1:])
        time.sleep(0.04)

    def read(self) -> tuple[float, float]:
        """Returns (temperature °C, humidity %)."""
        self.bus.write_i2c_block_data(self.addr, self.CMD_MEASURE[0], self.CMD_MEASURE[1:])
        time.sleep(0.08)
        for _ in range(10):
            if not (self.bus.read_byte(self.addr) & 0x80):
                break
            time.sleep(0.01)
        data = self.bus.read_i2c_block_data(self.addr, 0x00, 7)
        raw_hum = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
        raw_tmp = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]
        return (raw_tmp / 0x100000) * 200.0 - 50.0, (raw_hum / 0x100000) * 100.0


class ENS160:
    REG_OPMODE = 0x10
    REG_TEMP_IN = 0x13
    REG_RH_IN = 0x15
    REG_STATUS = 0x20
    REG_AQI = 0x21
    REG_TVOC = 0x22
    REG_ECO2 = 0x24
    VALIDITY = {0: "Normal", 1: "Warm-up", 2: "Initial Start-up", 3: "Invalid"}

    def __init__(self, bus, addr: int = 0x53):
        self.bus = bus
        self.addr = addr
        bus.write_byte_data(addr, self.REG_OPMODE, 0xF0)   # reset
        time.sleep(0.01)
        bus.write_byte_data(addr, self.REG_OPMODE, 0x01)   # idle
        time.sleep(0.01)
        bus.write_byte_data(addr, self.REG_OPMODE, 0x02)   # standard
        time.sleep(0.05)

    def set_compensation(self, temp: float, hum: float) -> None:
        t_raw = int((temp + 273.15) * 64)
        h_raw = int(hum * 512)
        self.bus.write_i2c_block_data(self.addr, self.REG_TEMP_IN,
                                      [t_raw & 0xFF, (t_raw >> 8) & 0xFF])
        self.bus.write_i2c_block_data(self.addr, self.REG_RH_IN,
                                      [h_raw & 0xFF, (h_raw >> 8) & 0xFF])

    def read(self) -> dict:
        status = self.bus.read_byte_data(self.addr, self.REG_STATUS)
        validity = (status >> 2) & 0x03
        aqi = self.bus.read_byte_data(self.addr, self.REG_AQI) & 0x07
        lo, hi = (self.bus.read_byte_data(self.addr, self.REG_TVOC + i) for i in range(2))
        tvoc = lo | (hi << 8)
        lo, hi = (self.bus.read_byte_data(self.addr, self.REG_ECO2 + i) for i in range(2))
        eco2 = lo | (hi << 8)
        return {
            "aqi": aqi, "tvoc_ppb": tvoc, "eco2_ppm": eco2,
            "validity": self.VALIDITY.get(validity, "Unknown"),
        }


class ADS1115:
    REG_CONVERT = 0x00
    REG_CONFIG = 0x01
    MUX = {0: 0x4000, 1: 0x5000, 2: 0x6000, 3: 0x7000}

    def __init__(self, bus, addr: int = 0x48):
        self.bus = bus
        self.addr = addr

    def read_raw(self, channel: int) -> int:
        config = (0x8000 | self.MUX[channel] | 0x0200 | 0x0100 | 0x0080 | 0x0003)
        self.bus.write_i2c_block_data(
            self.addr, self.REG_CONFIG,
            [(config >> 8) & 0xFF, config & 0xFF],
        )
        time.sleep(0.02)
        data = self.bus.read_i2c_block_data(self.addr, self.REG_CONVERT, 2)
        raw = (data[0] << 8) | data[1]
        return raw - 65536 if raw > 32767 else raw


# ── SensorHub ──────────────────────────────────────────────────────────────

class SensorHub:
    """
    Manages all sensors and polls them in a background daemon thread.
    Holds a reference to the shared config dict — picks up live changes.
    on_reading(data) is called after each successful read (e.g. to write to DB).
    """

    def __init__(self, config: dict, on_reading=None):
        self._config = config
        self._on_reading = on_reading
        self._lock = threading.Lock()
        self._data: dict | None = None
        self._running = False
        self._thread = None
        self._bus = None
        self._aht: AHT21 | None = None
        self._ens: ENS160 | None = None
        self._ads: ADS1115 | None = None
        self._setup()

    # ── Init ──────────────────────────────────────────────────────────────

    def _scfg(self) -> dict:
        return self._config.get("sensors", {})

    def _setup(self) -> None:
        if not self._scfg().get("enabled", True):
            return
        try:
            import smbus2
            self._bus = smbus2.SMBus(self._scfg().get("i2c_bus", 2))
        except Exception as exc:
            logger.warning("I2C bus unavailable: %s — sensors disabled", exc)
            return

        for name, cls, attr in [
            ("AHT21",   AHT21,   "_aht"),
            ("ENS160",  ENS160,  "_ens"),
            ("ADS1115", ADS1115, "_ads"),
        ]:
            try:
                setattr(self, attr, cls(self._bus))
                logger.info("Sensor %s ready", name)
            except Exception as exc:
                logger.warning("Sensor %s init failed: %s", name, exc)

    @property
    def available(self) -> bool:
        return any([self._aht, self._ens, self._ads])

    # ── Background thread ─────────────────────────────────────────────────

    def start(self) -> None:
        if not self.available:
            logger.info("No sensors detected — hub not started")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("SensorHub started")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            self._read_once()
            time.sleep(self._scfg().get("read_interval_seconds", 30))

    # ── Sensor reading ────────────────────────────────────────────────────

    def _read_once(self) -> None:
        data: dict = {}
        try:
            temp = hum = None

            if self._aht:
                temp, hum = self._aht.read()
                data["temperature"] = round(temp, 1)
                data["air_humidity"] = round(hum, 1)

            if self._ens:
                if temp is not None:
                    try:
                        self._ens.set_compensation(temp, hum)
                    except Exception:
                        pass
                air = self._ens.read()
                data["aqi"] = air["aqi"]
                data["tvoc_ppb"] = air["tvoc_ppb"]
                data["eco2_ppm"] = air["eco2_ppm"]
                data["ens_status"] = air["validity"]

            if self._ads:
                dry_vals = self._scfg().get("soil_dry", [26000, 26000])
                wet_vals = self._scfg().get("soil_wet", [13000, 13000])
                soil = []
                for i, ch in enumerate([0, 1]):
                    try:
                        raw = self._ads.read_raw(ch)
                        dry = dry_vals[i] if i < len(dry_vals) else 26000
                        wet = wet_vals[i] if i < len(wet_vals) else 13000
                        span = dry - wet
                        pct = (dry - raw) / span * 100.0 if span else 0.0
                        soil.append({
                            "channel": ch,
                            "moisture_pct": round(max(0.0, min(100.0, pct)), 1),
                            "raw": raw,
                        })
                    except Exception as exc:
                        logger.warning("ADS1115 ch%d: %s", ch, exc)
                data["soil"] = soil

        except Exception as exc:
            logger.error("Sensor read error: %s", exc)
            return

        with self._lock:
            self._data = data

        if self._on_reading and data:
            try:
                self._on_reading(data)
            except Exception as exc:
                logger.error("on_reading callback error: %s", exc)

    @property
    def latest(self) -> dict | None:
        with self._lock:
            return self._data

    # ── Cleanup ───────────────────────────────────────────────────────────

    def close(self) -> None:
        self._running = False
        if self._bus:
            try:
                self._bus.close()
            except Exception:
                pass
