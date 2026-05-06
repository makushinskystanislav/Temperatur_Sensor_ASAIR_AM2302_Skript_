# Temperature Monitoring System
Raspberry Pi + DHT22 (AM2302) temperature and humidity monitor with email alerts and automatic watchdog.

## What it does
- Reads temperature and humidity every 60 seconds
- Sends email alerts at WARNING / CRITICAL / EMERGENCY levels
- Watchdog automatically restarts the sensor script if it crashes
- Logs all readings and errors with daily log rotation

---

## Requirements
- Raspberry Pi with Raspberry Pi OS
- DHT22 / AM2302 sensor connected to GPIO pin 4
- Gmail account for sending alerts

---

## Installation

### 1. Clone the repository
```bash
cd ~/Desktop
git clone git@github.com:makushinskystanislav/Temperatur_Sensor_ASAIR_AM2302_Skript_.git
cd Temperatur_Sensor_ASAIR_AM2302_Skript_
```

### 2. Run the install script
```bash
chmod +x install.sh
./install.sh
```
The script will:
- Create a Python virtual environment
- Install dependencies from the `vendor/` folder
- Create an empty `.env` file
- Register and enable both systemd services

### 3. Fill in your email credentials
```bash
nano .env
```
```
EMAIL_SENDER="your_email@gmail.com"
EMAIL_PASSWORD="your_app_password"
EMAIL_RECEIVER="alert_recipient@gmail.com"
```
> **Note:** Use a Gmail App Password, not your regular password.  
> Generate one at: Google Account → Security → 2-Step Verification → App passwords

### 4. Start the services
```bash
sudo systemctl start dht.service watchdog.service
```

### 5. Verify everything is running
```bash
sudo systemctl status dht.service
sudo systemctl status watchdog.service
```
Both should show `active (running)`.

---

## Check logs
```bash
# Live temperature readings
tail -f logs/temperature/$(date +%Y-%m-%d)_temperature.log

# Errors
tail -f logs/errors/$(date +%Y-%m-%d)_error.log

# Watchdog activity
tail -f logs/watchdog/$(date +%Y-%m-%d)_watchdog.log
```

---

## Alert levels

| Level | Condition | Delay |
|---|---|---|
| WARNING | temp ≥ 28°C | sustained 10 min |
| CRITICAL | temp ≥ 32°C | sustained 3 min |
| EMERGENCY | temp ≥ 40°C | immediate |
| HUMIDITY | humidity ≥ 70% | immediate |

All alerts fire at most **once per day**. A higher level always overrides a lower one.

---

## Project structure
```
.
├── sensor.py           # main monitoring script
├── watchdog.py         # watchdog service
├── install.sh          # one-command setup script
├── requirements.txt    # Python dependencies
├── vendor/             # offline package files (.whl)
├── services/
│   ├── dht.service     # systemd service for sensor
│   └── watchdog.service
├── logs/
│   ├── temperature/
│   ├── errors/
│   └── watchdog/
└── .env                # credentials (not in git)
```

---

## Uninstall
```bash
sudo systemctl stop dht.service watchdog.service
sudo systemctl disable dht.service watchdog.service
sudo rm /etc/systemd/system/dht.service /etc/systemd/system/watchdog.service
sudo systemctl daemon-reload
rm -rf ~/Desktop/Temperatur_Sensor_ASAIR_AM2302_Skript_
```
