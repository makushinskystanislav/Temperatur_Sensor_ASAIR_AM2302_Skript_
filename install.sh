#!/bin/bash
# ==========================================================
# INSTALL SCRIPT — Temperature Monitoring System
# ==========================================================
# Run this once after cloning the repository:
#   chmod +x install.sh
#   ./install.sh
# ==========================================================

set -e  # exit immediately if any command returns a non-zero exit code
        # prevents the script from continuing after a failed step

# BASH_SOURCE[0] — path to this script file
# dirname        — strips the filename, leaving only the folder
# cd + pwd       — resolves symlinks and relative paths to an absolute path
# Result: SCRIPT_DIR always points to the folder where install.sh lives,
# regardless of where you call it from
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "=========================================="
echo " Temperature Monitoring System — Install"
echo "=========================================="
echo ""

# ==========================================================
# STEP 1 — Virtual environment
# ==========================================================
# A virtual environment isolates Python packages for this project
# so they don't conflict with system-wide packages.
# We only create it if it doesn't already exist.
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "[1/6] Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"   # creates venv/ folder with its own pip and python
else
    echo "[1/6] Virtual environment already exists — skipping."
fi

# ==========================================================
# STEP 2 — Install dependencies from vendor/
# ==========================================================
# --no-index        : do NOT reach out to PyPI — use only local files
# --find-links      : look for .whl package files in the vendor/ folder
# -r requirements   : install exactly the packages listed in requirements.txt
# --quiet           : suppress verbose pip output
echo "[2/6] Installing dependencies from vendor/..."
"$SCRIPT_DIR/venv/bin/pip" install \
    --no-index \
    --find-links="$SCRIPT_DIR/vendor" \
    -r "$SCRIPT_DIR/requirements.txt" \
    --quiet

echo "      Dependencies installed."

# ==========================================================
# STEP 3 — Create .env file
# ==========================================================
# .env stores email credentials (sender, password, receiver).
# It is NOT committed to git for security reasons.
# We create an empty template if the file doesn't exist yet.
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "[3/6] Creating empty .env file..."

    # cat > file << 'EOF' ... EOF — writes everything between EOF markers into the file
    cat > "$SCRIPT_DIR/.env" << 'EOF'
EMAIL_SENDER=""
EMAIL_PASSWORD=""
EMAIL_RECEIVER=""
EOF

    echo "      .env created. Fill in your credentials before starting the service."
else
    echo "[3/6] .env already exists — skipping."   # don't overwrite existing credentials
fi

# ==========================================================
# STEP 4 — Install systemd services
# ==========================================================
# systemd is the Linux service manager on Raspberry Pi OS.
# Service files (.service) describe how to start, restart, and enable a program.
# They must be placed in /etc/systemd/system/ to be recognised by systemd.
echo "[4/6] Installing systemd services..."

for SERVICE in dht.service watchdog.service; do
    SERVICE_SRC="$SCRIPT_DIR/services/$SERVICE"   # source file in our repo

    # sed replaces the hardcoded project path in the service file
    # with the actual path where this project was cloned.
    # This way the service works regardless of clone location.
    # s|old|new|g — substitute all occurrences of old with new
    sudo sed "s|/home/pi/Desktop/Temperatur_Sensor_ASAIR_AM2302_Skript_|$SCRIPT_DIR|g" \
        "$SERVICE_SRC" > /tmp/$SERVICE             # write result to a temp file

    sudo cp /tmp/$SERVICE "/etc/systemd/system/$SERVICE"   # copy to systemd folder
    echo "      Installed $SERVICE"
done

sudo systemctl daemon-reload                          # tell systemd to re-read service files
sudo systemctl enable dht.service watchdog.service    # enable = start automatically on boot
echo "      Services enabled (will start on every boot)."

# ==========================================================
# STEP 5 — Check .env credentials before starting
# ==========================================================
# grep   — finds the line containing the variable name
# cut    — extracts the value after the = sign
# -z     — true if the string is empty
# We check all three variables — if any is empty, we warn and stop.
echo "[5/6] Checking .env credentials..."

EMAIL_SENDER=$(grep  "EMAIL_SENDER"   "$SCRIPT_DIR/.env" | cut -d '=' -f2)
EMAIL_PASSWORD=$(grep "EMAIL_PASSWORD" "$SCRIPT_DIR/.env" | cut -d '=' -f2)
EMAIL_RECEIVER=$(grep "EMAIL_RECEIVER" "$SCRIPT_DIR/.env" | cut -d '=' -f2)

if [ -z "$EMAIL_SENDER" ] || [ -z "$EMAIL_PASSWORD" ] || [ -z "$EMAIL_RECEIVER" ]; then
    echo ""
    echo "  WARNING: .env credentials are missing."
    echo "  Fill in your email details:"
    echo "  nano $SCRIPT_DIR/.env"
    echo ""
    echo "  Then start the services manually:"
    echo "  sudo systemctl start dht.service watchdog.service"
    echo ""
    exit 0   # exit cleanly — not an error, just needs manual follow-up
fi

# ==========================================================
# STEP 6 — Start services
# ==========================================================
# start = launch the service right now (enable only affects next boot)
echo "[6/6] Starting services..."
sudo systemctl start dht.service watchdog.service

echo ""
echo "=========================================="
echo " Installation complete!"
echo "=========================================="
echo ""

# --no-pager — print status inline without opening a scrollable viewer
sudo systemctl status dht.service --no-pager
echo ""
sudo systemctl status watchdog.service --no-pager
echo ""
