# ==========================================================
# 📦 TEMPERATURE MONITORING SYSTEM (Raspberry Pi + DHT22)
# ==========================================================
# Features:
# - Periodic temperature & humidity monitoring
# - Multi-level alerts (WARNING / CRITICAL / EMERGENCY)
# - Sensor failure detection (based on error ratio)
# - Email notifications with attached logs
# - Separate logging for data and errors
# - Startup delay to stabilize sensor readings
# ==========================================================

import time
import board
import adafruit_dht
import smtplib
import os
from email.message import EmailMessage
from datetime import datetime
from dotenv import load_dotenv


# ==========================================================
# 📁 PATH CONFIGURATION
# ==========================================================
# Base project directory
BASE_DIR = "/home/pi/Desktop/temp_sensor_project"

# Path to environment variables file (.env)
ENV_PATH = os.path.join(BASE_DIR, ".env")


# ==========================================================
# 🔐 LOAD ENVIRONMENT VARIABLES
# ==========================================================
# Email credentials are stored in .env file
load_dotenv(ENV_PATH)

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")


# ==========================================================
# 📂 LOG DIRECTORY SETUP
# ==========================================================
LOG_BASE = os.path.join(BASE_DIR, "logs")
TEMP_DIR = os.path.join(LOG_BASE, "temperature")
ERROR_DIR = os.path.join(LOG_BASE, "errors")

# Ensure directories exist
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(ERROR_DIR, exist_ok=True)


# ==========================================================
# 🕒 TIME HELPER
# ==========================================================
def now():
    """Return current timestamp as formatted string"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==========================================================
# 📄 LOG FILE HANDLING
# ==========================================================
def get_temp_log():
    """Return path to today's temperature log file"""
    return os.path.join(
        TEMP_DIR,
        f"{datetime.now().strftime('%Y-%m-%d')}_temperature.log"
    )

def get_error_log():
    """Return path to today's error log file"""
    return os.path.join(
        ERROR_DIR,
        f"{datetime.now().strftime('%Y-%m-%d')}_error.log"
    )


# ==========================================================
# 📝 LOGGING FUNCTIONS
# ==========================================================
def log_temp(message):
    """Write operational messages (data, alerts, emails)"""
    with open(get_temp_log(), "a") as f:
        f.write(f"{now()} | {message}\n")

def log_error(message):
    """Write errors (sensor failures, email issues)"""
    with open(get_error_log(), "a") as f:
        f.write(f"{now()} | {message}\n")


# ==========================================================
# 📧 EMAIL SENDER (WITH LOG ATTACHMENTS)
# ==========================================================
def send_email(subject, body):
    """
    Send email notification with attached logs.
    Temperature and error logs are attached automatically.
    """
    try:
        msg = EmailMessage()
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        msg["Subject"] = subject
        msg.set_content(body)

        # Attach current logs
        for file_path in [get_temp_log(), get_error_log()]:
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    msg.add_attachment(
                        f.read(),
                        maintype="application",
                        subtype="octet-stream",
                        filename=os.path.basename(file_path)
                    )

        # Send via SMTP
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

        # Log successful send
        log_temp(f"EMAIL SENT: {subject}")

    except Exception as e:
        # Log email errors separately
        log_error(f"EMAIL ERROR: {e}")


# ==========================================================
# 🚨 ALERT FUNCTIONS
# ==========================================================
def send_alert(temp, hum, level):
    """
    Send temperature/humidity alert with appropriate severity level.
    """
    timestamp = now()

    if level == "EMERGENCY":
        subject = "🚨 EMERGENCY: OVERHEAT"
    elif level == "CRITICAL":
        subject = "🔴 CRITICAL TEMPERATURE"
    elif level == "WARNING":
        subject = "🟡 WARNING TEMPERATURE"
    elif level == "HUMIDITY":
        subject = "💧 HIGH HUMIDITY"

    body = f"""
{subject}

Temperature: {temp:.1f}C
Humidity: {hum:.1f}%

Time: {timestamp}
"""

    send_email(subject, body)


def send_error_alert(ratio, total, errors):
    """
    Send alert when sensor becomes unreliable (too many errors).
    """
    subject = "⚠️ SENSOR FAILURE"
    body = f"""
⚠️ SENSOR FAILURE DETECTED

Total readings: {total}
Errors: {errors}
Error ratio: {ratio:.2f}
"""
    send_email(subject, body)


# ==========================================================
# ⚙️ SYSTEM SETTINGS
# ==========================================================
POLL_INTERVAL = 30        # seconds between readings
STARTUP_DELAY = 60       # wait before first read
RETRY_DELAY = 5          # retry delay after error

# Temperature thresholds
WARNING_TEMP = 28
CRITICAL_TEMP = 32
EMERGENCY_TEMP = 40

# Humidity threshold
HUMIDITY_ALERT = 65

# Time-based thresholds
WARNING_TIME = 600       # 10 minutes
CRITICAL_TIME = 180      # 3 minutes
EMERGENCY_INTERVAL = 180 # repeat every 3 min

# Sensor error monitoring
ERROR_MONITOR_DELAY = 30 # monitoring delay
MIN_READINGS = 10 # minimum readings for desicion 
ERROR_RATIO_THRESHOLD = 0.7 # Krit errors ratio
ERROR_ALERT_COOLDOWN = 300 # sending messages cooldown 


# ==========================================================
# 🧠 RUNTIME STATE VARIABLES
# ==========================================================
warning_start = None  #when warning started
critical_start = None # when critical started
last_emergency_email = 0 # last emergency email timestamp

error_count = 0 #number of errors
total_count = 0 #total readings 
last_error_alert = 0 # last error alert timestamp
start_time = time.time() # script start time


# ==========================================================
# 📡 SENSOR INITIALIZATION
# ==========================================================
dht = adafruit_dht.DHT22(board.D4) # initialiye DHT22 sensor on GPIO pin D4 (7-ER PIN)

log_temp("SYSTEM BOOT 🚀") 
time.sleep(STARTUP_DELAY) # time for sensor stabilization
log_temp("SYSTEM READY ✅")


# ==========================================================
# 🔁 MAIN LOOP
# ==========================================================
while True:
    try:
        # Read sensor values
        temp = dht.temperature
        hum = dht.humidity

        # Validate reading
        if temp is None or hum is None:
            raise ValueError("Invalid sensor data")

        total_count += 1
        now_ts = time.time()

        message = f"TEMP={temp:.1f}C HUM={hum:.1f}%"

        # ================= EMERGENCY =================
        if temp >= EMERGENCY_TEMP:
            if now_ts - last_emergency_email >= EMERGENCY_INTERVAL:
                send_alert(temp, hum, "EMERGENCY")
                log_temp(f"{message} ALERT=🚨")
                last_emergency_email = now_ts
            continue

        # ================= CRITICAL =================
        if temp >= CRITICAL_TEMP:
            if critical_start is None:
                critical_start = now_ts
            elif now_ts - critical_start >= CRITICAL_TIME:
                send_alert(temp, hum, "CRITICAL")
                log_temp(f"{message} ALERT=🔴")
                critical_start = None
        else:
            critical_start = None

        # ================= WARNING =================
        if WARNING_TEMP <= temp < CRITICAL_TEMP:
            if warning_start is None:
                warning_start = now_ts
            elif now_ts - warning_start >= WARNING_TIME:
                send_alert(temp, hum, "WARNING")
                log_temp(f"{message} ALERT=🟡")
                warning_start = None
        else:
            warning_start = None

        # ================= HUMIDITY =================
        if hum >= HUMIDITY_ALERT:
            send_alert(temp, hum, "HUMIDITY")
            log_temp(f"{message} ALERT=💧")

        # Log normal reading
        log_temp(message)

    except Exception as e:
        # ================= ERROR HANDLING =================
        log_error(f"SENSOR ERROR: {e}")

        error_count += 1
        total_count += 1
        now_ts = time.time()

        # Activate error monitoring after startup delay and enough data
        if (
            now_ts - start_time > ERROR_MONITOR_DELAY and
            total_count >= MIN_READINGS
        ):
            ratio = error_count / total_count

            # Trigger alert if error ratio too high
            if ratio >= ERROR_RATIO_THRESHOLD:
                if now_ts - last_error_alert > ERROR_ALERT_COOLDOWN:
                    send_error_alert(ratio, total_count, error_count)

                    log_temp(
                        f"SENSOR ERROR ALERT SENT ⚠️ (ratio={ratio:.2f})"
                    )

                    last_error_alert = now_ts

        time.sleep(RETRY_DELAY)

    # Wait until next cycle
    time.sleep(POLL_INTERVAL)
