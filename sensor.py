# ==========================================================
# TEMPERATURE MONITORING SYSTEM (Raspberry Pi + DHT22)
# ==========================================================
# Features:
# - Periodic temperature & humidity monitoring
# - Multi-level alerts (WARNING / CRITICAL / EMERGENCY)
# - Per-day alert deduplication with escalation reset
# - Spike filter to ignore sudden single-reading jumps
# - Sensor failure detection via sliding error window
# - Email notifications with attached logs
# - Separate logging for data and errors
# - Daily log rotation to keep disk usage in check
# ==========================================================

import time
import board
import adafruit_dht
import smtplib
import os
from email.message import EmailMessage
from datetime import datetime
from collections import deque     # fixed-size queue; oldest item auto-drops when full
from dotenv import load_dotenv    # reads key=value pairs from a .env file into os.environ


# ==========================================================
# PATHS — all relative to the directory where this script lives
# ==========================================================

# __file__ is the path to this script; abspath resolves symlinks / relative refs;
# dirname strips the filename, leaving only the folder path.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENV_PATH  = os.path.join(BASE_DIR, ".env")          # email credentials file

LOG_BASE  = os.path.join(BASE_DIR, "logs")           # root log folder
TEMP_DIR  = os.path.join(LOG_BASE, "temperature")    # one file per day: YYYY-MM-DD_temperature.log
ERROR_DIR = os.path.join(LOG_BASE, "errors")         # one file per day: YYYY-MM-DD_error.log

# Create folders on first run; exist_ok=True prevents an error if they already exist
os.makedirs(TEMP_DIR,  exist_ok=True)
os.makedirs(ERROR_DIR, exist_ok=True)


# ==========================================================
# SETTINGS
# ==========================================================

# ── Timing ────────────────────────────────────────────────
SECONDS_PER_DAY = 86_400   # 60 s * 60 min * 24 h — used for cutoff calculations
POLL_INTERVAL   = 60       # seconds between normal sensor reads
STARTUP_DELAY   = 60       # seconds to wait after boot before first read (sensor warm-up)
RETRY_DELAY     = 5        # extra pause after a failed read before the next attempt

# ── Temperature thresholds (°C) ───────────────────────────
WARNING_TEMP   = 28        # sustained above this → WARNING alert
CRITICAL_TEMP  = 20        # sustained above this → CRITICAL alert
EMERGENCY_TEMP = 40        # immediately triggers EMERGENCY alert

# ── Humidity threshold (%) ────────────────────────────────
HUMIDITY_ALERT = 70        # single reading above this → HUMIDITY alert

# ── Sustained-condition timers ────────────────────────────
# Temperature must stay in the danger zone for this long before an alert fires.
# Prevents false alarms from brief spikes that pass the spike filter.
WARNING_TIME  = 600        # 10 minutes for WARNING
CRITICAL_TIME = 60        # 3 minutes for CRITICAL

# ── Spike filter ──────────────────────────────────────────
# A single reading that jumps more than these deltas from the previous
# accepted reading is treated as a sensor glitch and discarded.
SPIKE_TEMP_DELTA = 10      # °C — max plausible change between consecutive reads
SPIKE_HUM_DELTA  = 20      # %  — max plausible humidity change between consecutive reads

# ── Sensor reliability window ─────────────────────────────
# We keep a rolling window of the last N reads (True = error, False = ok).
# If the error ratio inside that window exceeds the threshold, we send a failure alert.
# Window fills at one read per minute, so ERROR_WINDOW_SIZE = 10 → ~10 min to fill.
# Alert fires only once the window is fully populated to avoid false alarms on startup.
ERROR_WINDOW_SIZE     = 10   # number of recent reads to analyse
ERROR_RATIO_THRESHOLD = 0.7  # 70 % errors → sensor is likely broken
ERROR_ALERT_COOLDOWN  = 300  # seconds between repeated sensor-failure emails

# ── Log retention ─────────────────────────────────────────
LOG_RETENTION_DAYS = 30      # log files older than this many days are deleted


# ==========================================================
# LOG ROTATION
# ==========================================================

def rotate_logs() -> None:
    """Delete log files older than LOG_RETENTION_DAYS from both log directories.

    Called once per calendar day from the main loop.
    Uses file modification time (mtime) to determine age.
    """
    # Compute a Unix timestamp for exactly LOG_RETENTION_DAYS ago
    cutoff = time.time() - LOG_RETENTION_DAYS * SECONDS_PER_DAY

    deleted = 0
    for directory in [TEMP_DIR, ERROR_DIR]:
        for fname in os.listdir(directory):
            fpath = os.path.join(directory, fname)
            # Skip sub-directories; only delete regular files older than the cutoff
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
                deleted += 1

    if deleted:
        # Only write to log when something was actually removed
        log_temp(f"LOG ROTATION: removed {deleted} old file(s)")


# ==========================================================
# CREDENTIALS
# ==========================================================

# Load EMAIL_SENDER / EMAIL_PASSWORD / EMAIL_RECEIVER from the .env file
load_dotenv(ENV_PATH)

EMAIL_SENDER   = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

# Fail immediately at startup rather than hours later on the first send attempt
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


# ==========================================================
# LOG FILE HELPERS
# ==========================================================

def get_temp_log() -> str:
    """Return the path to today's temperature log file (created on first write)."""
    return os.path.join(TEMP_DIR, f"{datetime.now().strftime('%Y-%m-%d')}_temperature.log")

def get_error_log() -> str:
    """Return the path to today's error log file (created on first write)."""
    return os.path.join(ERROR_DIR, f"{datetime.now().strftime('%Y-%m-%d')}_error.log")


def log_temp(message: str) -> None:
    """Append a timestamped line to today's temperature log."""
    with open(get_temp_log(), "a", encoding="utf-8") as f:
        f.write(f"{timestamp_str()} | {message}\n")

def log_error(message: str) -> None:
    """Append a timestamped line to today's error log."""
    with open(get_error_log(), "a", encoding="utf-8") as f:
        f.write(f"{timestamp_str()} | {message}\n")


# ==========================================================
# EMAIL
# ==========================================================

def send_email(subject: str, body: str, log_subject: str = "") -> None:
    """Send an alert email with today's log files attached.

    Attaches both the temperature log and the error log so the recipient
    has full context without needing to access the Pi directly.
    Errors are caught and written to the error log so a failed send
    never crashes the monitoring loop.
    """
    try:
        msg = EmailMessage()
        msg["From"]    = EMAIL_SENDER
        msg["To"]      = EMAIL_RECEIVER
        msg["Subject"] = subject
        msg.set_content(body)

        # Attach whichever log files already exist for today
        for file_path in [get_temp_log(), get_error_log()]:
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    msg.add_attachment(
                        f.read(),
                        maintype="application",
                        subtype="octet-stream",        # generic binary — client will offer download
                        filename=os.path.basename(file_path),
                    )

        # timeout=10 prevents the loop from hanging if Gmail is unreachable
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()                          # upgrade to encrypted connection
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

        log_temp(f"EMAIL SENT: {log_subject or subject}")

    except Exception as e:
        log_error(f"EMAIL ERROR: {e}")


# ==========================================================
# ALERT DEDUPLICATION
# ==========================================================
# Each temperature level (WARNING / CRITICAL / EMERGENCY) fires at most once
# per calendar day.  The exception is escalation: if the threat level rises,
# the higher level always fires and the clock resets for lower levels.
#
# Humidity is tracked independently — it has no escalation relationship
# with temperature and also fires at most once per day.
#
# State variables use date strings ("YYYY-MM-DD") so a simple string
# comparison is enough to detect a new calendar day.

_LEVEL_ORDER = ["WARNING", "CRITICAL", "EMERGENCY"]  # index = numeric priority

_last_sent_level: str | None = None  # highest temperature level sent today; None = none yet
_last_sent_date:  str        = ""    # date of the most recent temperature alert send
_humidity_sent_date: str     = ""    # date of the most recent humidity alert send


def _today() -> str:
    """Return today's date as 'YYYY-MM-DD'."""
    return datetime.now().strftime("%Y-%m-%d")


def _should_send_temp(level: str) -> bool:
    """Return True if a temperature alert at this level should be sent.

    Allowed when:
      1. It is a new calendar day (state is reset).
      2. No alert has been sent today yet.
      3. The requested level is strictly higher than the last one sent today (escalation).
    """
    global _last_sent_level, _last_sent_date

    today = _today()

    # Detect a new calendar day and reset state
    if _last_sent_date != today:
        _last_sent_level = None
        _last_sent_date  = today

    if _last_sent_level is None:
        return True  # nothing sent today yet

    # Compare numeric priorities: higher index = more severe
    return _LEVEL_ORDER.index(level) > _LEVEL_ORDER.index(_last_sent_level)


def _mark_temp_sent(level: str) -> None:
    """Record that a temperature alert at this level was sent today."""
    global _last_sent_level, _last_sent_date
    _last_sent_date  = _today()
    _last_sent_level = level


def _should_send_humidity() -> bool:
    """Return True if no humidity alert has been sent today."""
    global _humidity_sent_date
    today = _today()
    if _humidity_sent_date != today:
        _humidity_sent_date = ""   # clear stale date on a new day
    return _humidity_sent_date != today


def _mark_humidity_sent() -> None:
    global _humidity_sent_date
    _humidity_sent_date = _today()


# ==========================================================
# ALERT SENDERS
# ==========================================================

def _build_alert_body(temp: float, hum: float, subject: str) -> str:
    """Format a standard alert email body."""
    return (
        f"{subject}\n\n"
        f"Temperature : {temp:.1f} C\n"
        f"Humidity    : {hum:.1f} %\n\n"
        f"Time        : {timestamp_str()}\n"
    )


def send_alert(temp: float, hum: float, level: str) -> None:
    """Send a temperature or humidity alert, subject to per-day deduplication.

    Humidity uses its own independent gate.
    Temperature levels share a single gate with escalation logic.
    Suppressed alerts are recorded in the temperature log for traceability.
    """
    subjects = {
        "EMERGENCY": "🚨 EMERGENCY: OVERHEAT",
        "CRITICAL":  "🔴 CRITICAL TEMPERATURE",
        "WARNING":   "🟡 WARNING TEMPERATURE",
        "HUMIDITY":  "💧 HIGH HUMIDITY",
    }
    # Plain versions used in log files — no emoji to keep logs ASCII-safe
    plain_subjects = {
        "EMERGENCY": "EMERGENCY: OVERHEAT",
        "CRITICAL":  "CRITICAL TEMPERATURE",
        "WARNING":   "WARNING TEMPERATURE",
        "HUMIDITY":  "HIGH HUMIDITY",
    }
    subject       = subjects.get(level, f"ALERT: {level}")
    log_subject   = plain_subjects.get(level, f"ALERT: {level}")

    if level == "HUMIDITY":
        if _should_send_humidity():
            send_email(subject, _build_alert_body(temp, hum, subject), log_subject)
            _mark_humidity_sent()
        else:
            log_temp("ALERT SUPPRESSED (humidity already sent today)")
        return

    # All temperature levels go through the same escalation-aware gate
    if _should_send_temp(level):
        send_email(subject, _build_alert_body(temp, hum, subject), log_subject)
        _mark_temp_sent(level)
    else:
        log_temp(f"ALERT SUPPRESSED (already sent at >= {level} today)")


def send_error_alert(ratio: float, window_size: int, error_count: int) -> None:
    """Send a sensor reliability alert. Log files are attached via send_email."""
    subject = "⚠️ SENSOR FAILURE"
    body = (
        f"SENSOR FAILURE DETECTED\n\n"
        f"Window size    : {window_size}\n"   # number of reads currently in the window
        f"Errors         : {error_count}\n"   # how many of those were errors / spikes
        f"Error ratio    : {ratio:.2f}\n\n"   # errors / window_size
        f"Time           : {timestamp_str()}\n"
    )
    send_email(subject, body)


# ==========================================================
# RUNTIME STATE
# ==========================================================

# Timers for sustained-condition detection (filled with time.time() on first trigger)
warning_start  = None  # set when temp first enters the WARNING zone
critical_start = None  # set when temp first enters the CRITICAL zone

# Spike filter — remember the last reading that passed validation
last_good_temp = None  # last accepted temperature (°C)
last_good_hum  = None  # last accepted humidity (%)

# Sliding error window — deque auto-discards oldest entry when maxlen is reached
reading_window   = deque(maxlen=ERROR_WINDOW_SIZE)  # True = error/spike, False = ok

last_error_alert   = 0.0          # Unix timestamp of the last sensor-failure email (0 = never)
last_rotation_date = ""            # date string of the last log rotation ("" = not yet run today)
start_time         = time.time()   # Unix timestamp of when the script started


# ==========================================================
# SENSOR INIT
# ==========================================================

dht = adafruit_dht.DHT22(board.D4)  # DHT22 connected to GPIO pin 4

log_temp("SYSTEM BOOT")
time.sleep(STARTUP_DELAY)   # allow the sensor to stabilise before first read
log_temp("SYSTEM READY")


# ==========================================================
# MAIN LOOP
# ==========================================================
try:
    while True:

        # ── Daily log rotation ───────────────────────────────────
        # Checked every cycle; executes only when the calendar date has changed.
        # On the very first iteration last_rotation_date="" so it always runs once at startup.
        if _today() != last_rotation_date:
            rotate_logs()
            last_rotation_date = _today()
            uptime_days = (time.time() - start_time) / SECONDS_PER_DAY
            log_temp(f"UPTIME: {uptime_days:.1f} day(s) since last restart")

        try:
            temp = dht.temperature   # float (°C) or None on a bad read
            hum  = dht.humidity      # float (%) or None on a bad read

            # None from the sensor is not a Python exception — raise one explicitly
            # so it falls through to the except block and is counted as an error
            if temp is None or hum is None:
                raise ValueError("Sensor returned None")

            # ── Spike filter ─────────────────────────────────────
            # Compare against the last accepted reading.
            # A jump larger than the configured delta is almost certainly
            # a sensor glitch, not a real temperature change.
            if last_good_temp is not None:
                temp_jump = abs(temp - last_good_temp)
                hum_jump  = abs(hum  - last_good_hum)
                if temp_jump > SPIKE_TEMP_DELTA or hum_jump > SPIKE_HUM_DELTA:
                    log_temp(
                        f"SPIKE IGNORED: TEMP={temp:.1f}C (+/-{temp_jump:.1f}) "
                        f"HUM={hum:.1f}% (+/-{hum_jump:.1f})"
                    )
                    reading_window.append(True)   # count spike as an error in the window
                    continue                       # skip all alert logic for this read

            # Reading passed the spike filter — update the last known good values
            last_good_temp = temp
            last_good_hum  = hum

            reading_window.append(False)   # successful, non-spike read
            now_ts  = time.time()
            reading = f"TEMP={temp:.1f}C HUM={hum:.1f}%"

            # ── EMERGENCY ────────────────────────────────────────
            # No timer — react immediately; dedup gate still applies (once per day)
            if temp >= EMERGENCY_TEMP:
                send_alert(temp, hum, "EMERGENCY")
                log_temp(f"{reading} ALERT=EMERGENCY")
                time.sleep(POLL_INTERVAL)
                continue   # skip lower-level checks; situation is already at maximum

            # ── CRITICAL ─────────────────────────────────────────
            # Start a timer on first entry; fire once the condition persists long enough
            if temp >= CRITICAL_TEMP:
                if critical_start is None:
                    critical_start = now_ts          # mark when the zone was entered
                elif now_ts - critical_start >= CRITICAL_TIME:
                    send_alert(temp, hum, "CRITICAL")
                    log_temp(f"{reading} ALERT=CRITICAL")
                    critical_start = now_ts          # reset so timer runs again tomorrow
            else:
                critical_start = None                # left the zone — reset the timer

            # ── WARNING ──────────────────────────────────────────
            # Only active below CRITICAL_TEMP; escalation resets the timer naturally
            # because warning_start is cleared when temp >= CRITICAL_TEMP (else branch above
            # does not run, but this else will set warning_start = None).
            if WARNING_TEMP <= temp < CRITICAL_TEMP:
                if warning_start is None:
                    warning_start = now_ts
                elif now_ts - warning_start >= WARNING_TIME:
                    send_alert(temp, hum, "WARNING")
                    log_temp(f"{reading} ALERT=WARNING")
                    warning_start = now_ts           # reset so timer runs again tomorrow
            else:
                warning_start = None                 # left the zone — reset the timer

            # ── HUMIDITY ─────────────────────────────────────────
            # No sustained-condition timer; dedup gate fires once per day
            if hum >= HUMIDITY_ALERT:
                send_alert(temp, hum, "HUMIDITY")
                log_temp(f"{reading} ALERT=HUMIDITY")

            # Always log the raw reading regardless of alert status
            log_temp(reading)

        except (KeyboardInterrupt, SystemExit):
            raise   # let the outer handler catch these for graceful shutdown

        except Exception as e:
            log_error(f"SENSOR ERROR: {e}")

            reading_window.append(True)   # count this failure in the error window
            now_ts = time.time()

            # Only analyse the window once it is fully populated (ERROR_WINDOW_SIZE reads).
            # This prevents false failure alerts on startup when the window is nearly empty.
            if len(reading_window) >= ERROR_WINDOW_SIZE:
                ratio = sum(reading_window) / len(reading_window)
                if ratio >= ERROR_RATIO_THRESHOLD:
                    # Cooldown prevents repeated emails while the sensor stays broken
                    if now_ts - last_error_alert > ERROR_ALERT_COOLDOWN:
                        send_error_alert(ratio, len(reading_window), sum(reading_window))
                        log_temp(f"SENSOR ERROR ALERT SENT (ratio={ratio:.2f}, window={len(reading_window)})")
                        last_error_alert = now_ts

            time.sleep(RETRY_DELAY)   # brief pause before the next attempt

        finally:
            # Runs after every iteration — success or failure — to keep a steady cadence.
            # Combined with RETRY_DELAY, a failed read waits RETRY_DELAY + POLL_INTERVAL.
            time.sleep(POLL_INTERVAL)

except (KeyboardInterrupt, SystemExit):
    log_temp("SYSTEM SHUTDOWN")
    dht.exit()   # release the GPIO pin cleanly so the next run won't find it locked
