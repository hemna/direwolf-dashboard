#!/bin/bash
# Install Direwolf Dashboard as a systemd service
#
# Assumes the project is cloned to ~/direwolf-dashboard with a .venv created.
# Run: sudo bash contrib/install.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_FILE="$SCRIPT_DIR/direwolf-dashboard.service"

echo "Installing Direwolf Dashboard service..."

# Check if running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (use sudo)"
    exit 1
fi

# Verify the venv exists
if [ ! -f "$PROJECT_DIR/.venv/bin/direwolf-dashboard" ]; then
    echo "Error: .venv/bin/direwolf-dashboard not found in $PROJECT_DIR"
    echo ""
    echo "Set up the virtual environment first:"
    echo "  cd $PROJECT_DIR"
    echo "  uv venv"
    echo "  uv pip install -e ."
    exit 1
fi

# Copy service file
cp "$SERVICE_FILE" /etc/systemd/system/direwolf-dashboard.service
echo "  Copied service file to /etc/systemd/system/"

# Reload systemd
systemctl daemon-reload
echo "  Reloaded systemd"

# Enable and start
systemctl enable direwolf-dashboard
systemctl start direwolf-dashboard

echo "  Service enabled and started"
echo ""
echo "Status:"
systemctl status direwolf-dashboard --no-pager
