#!/bin/bash
# Setup script for FIDE Rating Calculator (Python backend)
# Run this on the server after initial deployment

set -e

APP_DIR="/opt/fide-app"
BACKEND_DIR="$APP_DIR/backend"

echo "=== Setting up FIDE Rating Calculator ==="

# Create venv if not exists
if [ ! -d "$APP_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$APP_DIR/venv"
fi

# Install dependencies
echo "Installing dependencies..."
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# Install systemd service
echo "Installing systemd service..."
cp "$APP_DIR/fide-app.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable fide-app
systemctl restart fide-app

# Check status
echo "=== Service Status ==="
systemctl status fide-app --no-pager
echo ""
echo "=== Testing health endpoint ==="
sleep 2
curl -s http://127.0.0.1:8200/api/health || echo "⚠️ Health check failed"

echo ""
echo "✅ Setup complete"
