#!/bin/bash
# Install Direwolf Dashboard as a systemd service
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/direwolf-dashboard.service"

echo "Installing Direwolf Dashboard service..."

# Check if running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (use sudo)"
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
