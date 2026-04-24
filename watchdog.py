# ==========================================================
# 🛡️ WATCHDOG SERVICE (Raspberry Pi Monitoring)
# ==========================================================
# Purpose:
# - Monitor sensor script activity via log updates
# - Detect hangs or crashes
# - Restart systemd service if needed
# - Send email alerts with watchdog logs attached
#
# Logic:
# - Check latest temperature log file
# - If file is missing → restart + alert
# - If file not updated for X seconds → restart + alert
# - Otherwise → log "OK"
# ==========================================================

import os
import time
import smtplib
from email.message import EmailMessage
from datetime import datetime
from dotenv import load_dotenv


# ==========================================================
# 📁 PATH CONFIGURATION
# ==========================================================
BASE_DIR = "/home/pi/Desktop/temp_sensor_project"
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Directory where sensor logs are stored
LOG_DIR = os.path.join(BASE_DIR, "logs/temperature")

# Watchdog own log directory
WATCHDOG_DIR = os.path.join(BASE_DIR, "logs/watchdog")

# Ensure watchdog log folder exists
os.makedirs(WATCHDOG_DIR, exist_ok=True)


# ==========================================================
# ⚙️ SERVICE CONFIGURATION
# ==========================================================
# Name of systemd service running the sensor script
SERVICE_NAME = "dht.service"

# How often watchdog checks the system (seconds)
CHECK_INTERVAL = 60

# Max allowed time without log update (seconds)
TIMEOUT = 180


# ==========================================================
# 🔐 LOAD ENVIRONMENT VARIABLES
# ==========================================================
load_dotenv(ENV_PATH)

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")


# ==========================================================
# 🕒 TIME HELPER
# ==========================================================
def now():
    """Return formatted timestamp"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==========================================================
# 📄 LOG FILE HANDLING
# ==========================================================
def get_watchdog_log():
    """Return today's watchdog log file path"""
    return os.path.join(
        WATCHDOG_DIR,
        f"{datetime.now().strftime('%Y-%m-%d')}_watchdog.log"
    )


# ==========================================================
# 📝 LOGGING
# ==========================================================
def log_watchdog(message):
    """Write watchdog activity to log file"""
    with open(get_watchdog_log(), "a") as f:
        f.write(f"{now()} | {message}\n")


# ==========================================================
# 📧 EMAIL SENDER (WITH ATTACHMENT)
# ==========================================================
def send_email(subject, body):
    """
    Send watchdog alert email.
    Attaches current watchdog log file.
    """
    try:
        msg = EmailMessage()
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        msg["Subject"] = subject
        msg.set_content(body)

        # Attach watchdog log
        log_file = get_watchdog_log()
        if os.path.exists(log_file):
            with open(log_file, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype="application",
                    subtype="octet-stream",
                    filename=os.path.basename(log_file)
                )

        # Send email
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

        # Log success
        log_watchdog(f"EMAIL SENT: {subject}")

    except Exception as e:
        # Log failure
        log_watchdog(f"EMAIL ERROR ❌: {e}")


# ==========================================================
# 🔍 HELPER: FIND LATEST SENSOR LOG
# ==========================================================
def get_latest_log():
    """
    Get the most recent temperature log file.
    Used to check if sensor script is alive.
    """
    try:
        files = [f for f in os.listdir(LOG_DIR) if f.endswith(".log")]

        if not files:
            return None

        # Sort by name (date-based filenames)
        files.sort(reverse=True)

        return os.path.join(LOG_DIR, files[0])

    except Exception as e:
        log_watchdog(f"ERROR reading log directory ❌: {e}")
        return None


# ==========================================================
# 🔁 MAIN WATCHDOG LOOP
# ==========================================================
while True:
    try:
        log_file = get_latest_log()

        # ================= MISSING LOG =================
        if not log_file or not os.path.exists(log_file):
            send_email(
                "🛡️ WATCHDOG ALERT: log missing",
                "Temperature log file not found. Restarting service."
            )

            log_watchdog("RESTART: log missing ⚠️")
            os.system(f"systemctl restart {SERVICE_NAME}")
            time.sleep(CHECK_INTERVAL)
            continue

        # ================= CHECK LAST UPDATE =================
        last_update = os.path.getmtime(log_file)
        time_since_update = time.time() - last_update

        # ================= TIMEOUT DETECTED =================
        if time_since_update > TIMEOUT:
            send_email(
                "🛡️ WATCHDOG ALERT: no updates",
                f"No log updates for {int(time_since_update)} seconds. Restarting service."
            )

            log_watchdog(f"RESTART: timeout {int(time_since_update)}s ⚠️")
            os.system(f"systemctl restart {SERVICE_NAME}")

        else:
            # ================= SYSTEM OK =================
            log_watchdog("STATUS: OK ✅")

    except Exception as e:
        # Catch any unexpected watchdog failure
        log_watchdog(f"CRITICAL ERROR ❌: {e}")

    # Wait until next check
    time.sleep(CHECK_INTERVAL)