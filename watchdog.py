# ==========================================================
# WATCHDOG SERVICE (Raspberry Pi Monitoring)
# ==========================================================
# Purpose:
# - Monitor sensor script activity via log file updates
# - Detect hangs or crashes
# - Restart the systemd service if needed
# - Send email alerts with the watchdog log attached
#
# Logic:
# - Every CHECK_INTERVAL seconds, find the latest temperature log
# - If the log file is missing            → restart + alert
# - If not updated for more than TIMEOUT  → restart + alert
# - Otherwise                             → log STATUS: OK
# ==========================================================

import os
import time
import smtplib
import subprocess                      # replaces os.system — lets us check restart exit code
from email.message import EmailMessage
from datetime import datetime
from dotenv import load_dotenv         # reads credentials from .env; never commit that file


# ==========================================================
# PATHS — derived from this script's own location so the
# watchdog works from any directory, same as the sensor script
# ==========================================================

# Resolve the folder that contains this file (symlink-safe)
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))

# .env lives next to both scripts in the same project folder
ENV_PATH     = os.path.join(BASE_DIR, ".env")

# Must match the TEMP_DIR used in temp_monitor.py
LOG_DIR      = os.path.join(BASE_DIR, "logs", "temperature")

# Watchdog writes its own daily log here
WATCHDOG_DIR = os.path.join(BASE_DIR, "logs", "watchdog")

# Create the watchdog log folder if it does not exist yet
os.makedirs(WATCHDOG_DIR, exist_ok=True)


# ==========================================================
# SETTINGS
# ==========================================================

SECONDS_PER_DAY = 86_400   # 60 s * 60 min * 24 h

# systemd service name that runs the sensor script
SERVICE_NAME = "dht.service"

# How often the watchdog checks the sensor log (seconds)
CHECK_INTERVAL = 60

# Maximum allowed silence from the sensor log before a restart (seconds)
TIMEOUT = 300

# Watchdog log retention — delete files older than this many days
LOG_RETENTION_DAYS = 30


# ==========================================================
# CREDENTIALS
# ==========================================================

# Load EMAIL_SENDER / EMAIL_PASSWORD / EMAIL_RECEIVER from .env
# The .env file must NOT be committed to git — add it to .gitignore
load_dotenv(ENV_PATH)

EMAIL_SENDER   = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

# Fail at startup with a clear message rather than silently on first send
if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    raise ValueError(
        "Missing email credentials. "
        "Make sure EMAIL_SENDER, EMAIL_PASSWORD and EMAIL_RECEIVER "
        f"are set in {ENV_PATH}"
    )


# ==========================================================
# TIME HELPER
# ==========================================================

def timestamp_str() -> str:
    """Return the current date and time as 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _today() -> str:
    """Return today's date as 'YYYY-MM-DD'. Used for log filenames and day resets."""
    return datetime.now().strftime("%Y-%m-%d")


# ==========================================================
# LOG FILE HELPERS
# ==========================================================

def get_watchdog_log() -> str:
    """Return the path to today's watchdog log (created on first write)."""
    return os.path.join(WATCHDOG_DIR, f"{_today()}_watchdog.log")

def log_watchdog(message: str) -> None:
    """Append a timestamped, ASCII-safe line to today's watchdog log."""
    with open(get_watchdog_log(), "a", encoding="utf-8") as f:
        f.write(f"{timestamp_str()} | {message}\n")


# ==========================================================
# LOG ROTATION
# ==========================================================

def rotate_watchdog_logs() -> None:
    """Delete watchdog log files older than LOG_RETENTION_DAYS.

    Called once per calendar day from the main loop, same pattern
    as the sensor script's rotate_logs().
    """
    cutoff  = time.time() - LOG_RETENTION_DAYS * SECONDS_PER_DAY
    deleted = 0
    for fname in os.listdir(WATCHDOG_DIR):
        fpath = os.path.join(WATCHDOG_DIR, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            os.remove(fpath)
            deleted += 1
    if deleted:
        log_watchdog(f"LOG ROTATION: removed {deleted} old file(s)")


# ==========================================================
# EMAIL
# ==========================================================

def send_email(subject: str, body: str) -> None:
    """Send a watchdog alert email with today's watchdog log attached.

    timeout=10 prevents the loop from hanging if Gmail is unreachable.
    Errors are caught and written to the watchdog log — a failed send
    never crashes the watchdog itself.
    """
    try:
        msg = EmailMessage()
        msg["From"]    = EMAIL_SENDER
        msg["To"]      = EMAIL_RECEIVER
        msg["Subject"] = subject
        msg.set_content(body)

        # Attach today's watchdog log so the recipient has full context
        log_file = get_watchdog_log()
        if os.path.exists(log_file):
            with open(log_file, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype="application",
                    subtype="octet-stream",
                    filename=os.path.basename(log_file),
                )

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()                           # upgrade to encrypted connection
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

        log_watchdog(f"EMAIL SENT: {subject}")

    except Exception as e:
        log_watchdog(f"EMAIL ERROR: {e}")


# ==========================================================
# SENSOR LOG DISCOVERY
# ==========================================================

def get_latest_sensor_log() -> str | None:
    """Return the path to the most recent temperature log file, or None.

    Log filenames are date-based (YYYY-MM-DD_temperature.log) so
    sorting lexicographically gives chronological order.
    """
    try:
        files = [f for f in os.listdir(LOG_DIR) if f.endswith(".log")]
        if not files:
            return None
        files.sort(reverse=True)                       # latest date first
        return os.path.join(LOG_DIR, files[0])
    except Exception as e:
        log_watchdog(f"ERROR reading sensor log directory: {e}")
        return None


# ==========================================================
# SERVICE RESTART
# ==========================================================

def restart_service(reason: str) -> None:
    """Restart the systemd service and log the outcome.

    Uses subprocess.run() instead of os.system() so we can capture
    the return code and log whether the restart actually succeeded.
    """
    log_watchdog(f"RESTART TRIGGERED: {reason}")
    result = subprocess.run(
        ["systemctl", "restart", SERVICE_NAME],
        capture_output=True,   # capture stdout and stderr
        text=True,             # decode output as text
    )
    if result.returncode == 0:
        log_watchdog(f"RESTART OK: {SERVICE_NAME}")
    else:
        # Log the error output from systemctl to help with debugging
        log_watchdog(f"RESTART FAILED (code {result.returncode}): {result.stderr.strip()}")


# ==========================================================
# RUNTIME STATE
# ==========================================================

last_rotation_date       = ""   # date of last log rotation; "" triggers rotation on first cycle
# Per-alert type dedup: once the date matches _today() that alert is suppressed for the rest of the day
_alert_missing_date      = ""   # date "log missing" alert was last sent
_alert_timeout_date      = ""   # date "sensor timeout" alert was last sent


# ==========================================================
# MAIN LOOP
# ==========================================================
try:
    log_watchdog("WATCHDOG BOOT")

    while True:

        # ── Daily log rotation ───────────────────────────────────
        # Runs once per calendar day; "" on startup guarantees it runs immediately.
        if _today() != last_rotation_date:
            rotate_watchdog_logs()
            last_rotation_date = _today()

        try:
            log_file = get_latest_sensor_log()

            # ── Missing log ──────────────────────────────────────
            # No log file at all — sensor script has never written or the
            # folder was wiped. Restart immediately.
            if not log_file or not os.path.exists(log_file):
                restart_service("sensor log file missing")

                # Send at most once per calendar day
                if _alert_missing_date != _today():
                    send_email(
                        "WATCHDOG ALERT: log missing",
                        f"Temperature log file not found.\n"
                        f"Service restarted.\n\n"
                        f"Time: {timestamp_str()}"
                    )
                    _alert_missing_date = _today()
                else:
                    log_watchdog("ALERT SUPPRESSED: log missing alert already sent today")

                time.sleep(CHECK_INTERVAL)
                continue

            # ── Timeout check ────────────────────────────────────
            # Log file exists but has not been updated recently —
            # sensor script is alive on disk but stuck or hung.
            last_update       = os.path.getmtime(log_file)
            time_since_update = time.time() - last_update

            if time_since_update > TIMEOUT:
                restart_service(f"no log update for {int(time_since_update)}s")

                # Send at most once per calendar day
                if _alert_timeout_date != _today():
                    send_email(
                        "WATCHDOG ALERT: sensor timeout",
                        f"No log updates for {int(time_since_update)} seconds.\n"
                        f"Service restarted.\n\n"
                        f"Time: {timestamp_str()}"
                    )
                    _alert_timeout_date = _today()
                else:
                    log_watchdog("ALERT SUPPRESSED: timeout alert already sent today")

            else:
                # Everything looks healthy — sensor wrote to the log recently
                log_watchdog(
                    f"STATUS: OK "
                    f"(last update {int(time_since_update)}s ago)"
                )

        except (KeyboardInterrupt, SystemExit):
            raise   # bubble up to the outer handler for graceful shutdown

        except Exception as e:
            # Catch unexpected watchdog errors without crashing the loop
            log_watchdog(f"WATCHDOG ERROR: {e}")

        time.sleep(CHECK_INTERVAL)

except (KeyboardInterrupt, SystemExit):
    log_watchdog("WATCHDOG SHUTDOWN")
