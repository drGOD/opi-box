# GrowBox for Orange Pi

Web controller for a small grow box on Orange Pi / Armbian. The app controls relays through GPIO, reads I2C sensors, shows a camera stream, stores sensor history in SQLite, takes timelapse snapshots, and can update itself from GitHub through a systemd timer.

The current default setup is aimed at Orange Pi Zero 3 with Armbian, but the project should work on other Orange Pi boards if Linux exposes GPIO through `libgpiod`, I2C through `/dev/i2c-*`, and the camera through V4L2 `/dev/video*`.

## Features

- Web UI on port `8080`
- Relay control through GPIO lines
- Manual and automatic mode
- Per-relay schedules
- AHT20/AHT21 temperature and air humidity sensor on I2C
- ADS1115 ADC on I2C for soil moisture channels
- Automatic humidifier control with hysteresis
- Automatic ventilation by humidity and temperature thresholds
- USB/V4L2 camera stream and snapshots
- Timelapse frame capture and GIF generation
- SQLite history database
- systemd service and OTA update timer

## Hardware

Typical hardware:

- Orange Pi Zero 3 or another Orange Pi with Armbian
- 5 V power supply with enough current for the board and connected modules
- Relay module, preferably opto-isolated
- AHT20/AHT21 I2C sensor, address `0x38`
- ADS1115 I2C ADC, default address `0x48`
- Capacitive soil moisture sensors connected to ADS1115 channels `A0` and `A1`
- USB camera or CSI camera exposed as `/dev/videoN`

Important wiring notes:

- Orange Pi GPIO is usually 3.3 V logic. Do not feed 5 V into GPIO pins.
- Many relay boards are `active_low`: GPIO low means relay ON. The default config uses `"active_low": true`.
- Use a common ground between Orange Pi and sensor/relay logic where required.
- Mains voltage is dangerous. Keep AC wiring isolated and use a proper enclosure.

## Fresh Orange Pi Setup

Flash Armbian for your board, boot it, connect to the network, then update the system:

```bash
sudo apt update
sudo apt upgrade -y
sudo reboot
```

After reboot, install I2C diagnostic tools. The project installer installs runtime packages, but `i2c-tools` is useful while wiring:

```bash
sudo apt install -y i2c-tools
```

## Enable I2C

On Armbian, the easiest way is:

```bash
sudo armbian-config
```

Open `System` -> `Hardware`, enable the I2C overlay for the pins you want to use, save, and reboot:

```bash
sudo reboot
```

Overlay names are board-specific. On one image the bus may appear as `i2c2`, on another as `i2c3` or similar. Do not guess; check what Linux created.

## Find the Correct I2C Bus

List available I2C adapters:

```bash
ls -l /dev/i2c-*
sudo i2cdetect -l
```

You will see devices such as `/dev/i2c-0`, `/dev/i2c-1`, `/dev/i2c-2`. The number after `i2c-` is the value used in `config.json`.

Scan each bus:

```bash
sudo i2cdetect -y 0
sudo i2cdetect -y 1
sudo i2cdetect -y 2
sudo i2cdetect -y 3
```

Expected addresses:

- `38` means AHT20/AHT21 was found
- `48` means ADS1115 was found

Example successful scan:

```text
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
30: -- -- -- -- -- -- -- -- 38 -- -- -- -- -- -- --
40: -- -- -- -- -- -- -- -- 48 -- -- -- -- -- -- --
```

If both addresses appear on `/dev/i2c-2`, set:

```json
"sensors": {
  "enabled": true,
  "i2c_bus": 2,
  "read_interval_seconds": 30,
  "soil_dry": [26000, 26000],
  "soil_wet": [13000, 13000]
}
```

If nothing appears:

- Check that I2C is enabled and the board was rebooted.
- Check SDA/SCL are connected to the enabled I2C pins, not just any header pins.
- Check power and ground.
- Try another bus number from `i2cdetect -l`.
- Disconnect one module at a time to find wiring or address conflicts.

## Find GPIO Lines for Relays

This project uses `libgpiod`, not the old `/sys/class/gpio` interface. First list GPIO chips:

```bash
sudo gpiodetect
```

Then inspect lines:

```bash
sudo gpioinfo gpiochip0
```

The number at the start of each line is the GPIO line offset used in `config.json`. For example, if `gpioinfo` shows:

```text
line   7:      "PH2"       unused   input  active-high
```

then the config line is `"gpio_pin": 7`.

You can also search a named line if your kernel exposes names:

```bash
gpiofind PH2
```

Test a relay line manually:

```bash
sudo gpioset gpiochip0 7=1
sudo gpioset gpiochip0 7=0
```

On active-low relay boards, `0` often turns the relay on and `1` turns it off. If the web UI switch is inverted, change `"active_low"` for that relay.

Example relay config:

```json
"gpio_chip": "gpiochip0",
"relays": [
  {"id": 1, "name": "Свет", "gpio_pin": 7, "active_low": true, "state": false},
  {"id": 2, "name": "Вентиляция", "gpio_pin": 8, "active_low": true, "state": false},
  {"id": 3, "name": "Увлажнитель", "gpio_pin": 9, "active_low": true, "state": false}
]
```

If a relay stays in mock mode, check logs:

```bash
journalctl -u growbox -f
```

Look for messages like `GPIO init failed`. Common causes are a wrong `gpiochip`, wrong line number, missing `gpiod`, or another process holding the line.

## Find the Camera Device

List V4L2 devices:

```bash
ls -l /dev/video*
v4l2-ctl --list-devices
```

Inspect a candidate:

```bash
v4l2-ctl --device=/dev/video0 --all
```

The app accepts either an integer device number or a path-like camera source. In `config.json`:

```json
"camera_device": 0
```

means `/dev/video0`. On some Orange Pi images `/dev/video0` can be a hardware decoder or another internal device, and the real camera starts at `/dev/video1`. In that case use:

```json
"camera_device": 1
```

After changing the camera device, restart:

```bash
sudo systemctl restart growbox
```

## Install GrowBox

One-line install from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/drGOD/opi-box/main/install.sh | sudo bash -s https://github.com/drGOD/opi-box.git
```

Or clone and run locally:

```bash
git clone https://github.com/drGOD/opi-box.git
cd opi-box
sudo bash install.sh https://github.com/drGOD/opi-box.git
```

The installer will:

- create a 2 GB swap file if missing
- install system packages
- clone or update the app in `/opt/growbox`
- create `/opt/growbox/config.json` if missing
- create a Python virtualenv
- install Python dependencies
- install and start `growbox.service`
- install and start the OTA timer

Open the web UI:

```text
http://ORANGE_PI_IP:8080
```

Find the board IP:

```bash
hostname -I
```

## Configuration

The main config file is:

```bash
/opt/growbox/config.json
```

Edit it:

```bash
sudo nano /opt/growbox/config.json
sudo systemctl restart growbox
```

The app also exposes settings through the web UI/API, but direct editing is useful during first hardware setup.

Full example:

```json
{
  "timelapse_interval_minutes": 30,
  "timelapse_enabled": true,
  "camera_device": 1,
  "gpio_chip": "gpiochip0",
  "relays": [
    {"id": 1, "name": "Свет", "gpio_pin": 7, "active_low": true, "state": false},
    {"id": 2, "name": "Вентиляция", "gpio_pin": 8, "active_low": true, "state": false},
    {"id": 3, "name": "Увлажнитель", "gpio_pin": 9, "active_low": true, "state": false}
  ],
  "schedules": [
    {"relay_id": 1, "enabled": true, "on_time": "08:00", "off_time": "22:00"},
    {"relay_id": 2, "enabled": false, "on_time": "08:00", "off_time": "22:00"},
    {"relay_id": 3, "enabled": false, "on_time": "00:00", "off_time": "00:00"}
  ],
  "humidity_control": {
    "enabled": false,
    "relay_id": 3,
    "target_humidity": 65.0,
    "hysteresis": 6.0,
    "min_switch_interval_seconds": 180
  },
  "climate_ventilation": {
    "enabled": true,
    "relay_id": 2,
    "max_humidity": 80.0,
    "min_humidity": 40.0,
    "max_temperature": 35.0,
    "min_temperature": 18.0,
    "min_switch_interval_seconds": 180
  },
  "sensors": {
    "enabled": true,
    "i2c_bus": 2,
    "read_interval_seconds": 30,
    "soil_dry": [26000, 26000],
    "soil_wet": [13000, 13000]
  }
}
```

Config notes:

- `camera_device`: `0` means `/dev/video0`, `1` means `/dev/video1`.
- `gpio_chip`: usually `gpiochip0`, but confirm with `gpiodetect`.
- `gpio_pin`: GPIO line offset from `gpioinfo`, not physical header pin number.
- `active_low`: set `true` for most low-level-trigger relay modules.
- `sensors.i2c_bus`: number from `/dev/i2c-N`.
- `soil_dry` and `soil_wet`: raw ADS1115 calibration values for each soil channel.

## Soil Sensor Calibration

The ADS1115 raw values depend on the exact sensor, voltage, soil, and wiring. Calibrate before trusting percentages.

1. Put the soil sensor in dry air or very dry soil.
2. Read current sensor data from the API:

```bash
curl http://127.0.0.1:8080/api/sensors
```

3. Copy the raw values into `soil_dry`.
4. Put the sensor in wet soil or water according to your sensor's safety limits.
5. Copy the raw values into `soil_wet`.
6. Restart the service or save settings through the UI.

Example:

```json
"soil_dry": [26000, 25500],
"soil_wet": [13000, 12800]
```

## Service Management

Start, stop, restart:

```bash
sudo systemctl start growbox
sudo systemctl stop growbox
sudo systemctl restart growbox
```

Enable on boot:

```bash
sudo systemctl enable growbox
```

Logs:

```bash
journalctl -u growbox -f
```

Status:

```bash
systemctl status growbox
```

## OTA Updates

The installer enables `growbox-ota.timer`. It runs every 15 minutes, fetches `main`, pulls fast-forward updates, reinstalls dependencies if `requirements.txt` changed, and restarts the app.

Check timer:

```bash
systemctl list-timers | grep growbox
```

Run update manually:

```bash
sudo systemctl start growbox-ota.service
```

OTA logs:

```bash
journalctl -u growbox-ota -f
```

Disable OTA:

```bash
sudo systemctl disable --now growbox-ota.timer
```

## Timelapse and GIF

Timelapse frames are stored in:

```bash
/opt/growbox/timelapse
```

Build a GIF from all frames:

```bash
/opt/growbox/gif.sh
```

Build a GIF for a range:

```bash
/opt/growbox/gif.sh --start 2026-04-05T12:00:00 --end 2026-04-05T18:00:00
```

Custom output size:

```bash
/opt/growbox/gif.sh -o /tmp/growbox.gif --width 640 --height 480
```

## API Quick Check

From the Orange Pi:

```bash
curl http://127.0.0.1:8080/api/status
curl http://127.0.0.1:8080/api/sensors
curl http://127.0.0.1:8080/api/settings
```

From another computer on the same network:

```bash
curl http://ORANGE_PI_IP:8080/api/status
```

## Troubleshooting

### Web UI does not open

Check service and logs:

```bash
systemctl status growbox
journalctl -u growbox -n 100 --no-pager
```

Check that the app listens on port `8080`:

```bash
sudo ss -lntp | grep 8080
```

### I2C sensors are unavailable

Check buses and scan:

```bash
ls -l /dev/i2c-*
sudo i2cdetect -l
sudo i2cdetect -y 2
```

If `0x38` or `0x48` is missing, the problem is below the app level: overlay, bus number, wiring, power, address, or damaged module.

### Relays do not switch

Check GPIO discovery:

```bash
sudo gpiodetect
sudo gpioinfo gpiochip0
```

Test the line manually:

```bash
sudo gpioset gpiochip0 7=0
sudo gpioset gpiochip0 7=1
```

If manual switching works but the app does not, check `gpio_chip`, `gpio_pin`, `active_low`, and service logs.

### Camera is black or unavailable

List devices:

```bash
v4l2-ctl --list-devices
ls -l /dev/video*
```

Try another `camera_device` in `config.json`, then restart:

```bash
sudo systemctl restart growbox
```

### App works but sensors/relays are in mock or disabled mode

The app intentionally continues running when hardware init fails. This is useful for testing the UI, but on real hardware it means you should inspect:

```bash
journalctl -u growbox -f
```

Search for:

- `I2C bus unavailable`
- `Sensor AHT20 init failed`
- `Sensor ADS1115 init failed`
- `GPIO init failed`
- `Cannot open camera device`

## Development on a PC

Create a virtualenv and run tests:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Run without real hardware:

```bash
python app.py
```

If GPIO or I2C is unavailable, the app logs warnings and continues where possible.

