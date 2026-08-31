#!/bin/bash
# install_services.sh - Install the MAVLink MCP server as a systemd service

set -e  # Exit on error

echo "============================================================"
echo "MAVLink MCP Server - Service Installation"
echo "============================================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ ERROR: This script must be run as root"
    echo "   Please run: sudo ./install_services.sh"
    exit 1
fi

# Detect installation directory
INSTALL_DIR="${INSTALL_DIR:-$(pwd)}"
echo "📁 Installation directory: $INSTALL_DIR"
echo ""

# Update service files with correct paths
echo "📝 Updating service files with installation directory..."
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$INSTALL_DIR|g" droneserver.service
sed -i "s|ExecStart=.*start_http_server.sh|ExecStart=$INSTALL_DIR/start_http_server.sh|g" droneserver.service

# Copy service files to systemd directory
echo "📋 Copying service files to /etc/systemd/system/..."
cp droneserver.service /etc/systemd/system/

# Set correct permissions
chmod 644 /etc/systemd/system/droneserver.service

# Make start script executable
chmod +x "$INSTALL_DIR/start_http_server.sh"

# Reload systemd daemon
echo "🔄 Reloading systemd daemon..."
systemctl daemon-reload

echo ""
echo "============================================================"
echo "✅ Services installed successfully!"
echo "============================================================"
echo ""
echo "📋 Next steps:"
echo ""
echo "1. Enable the service to start on boot:"
echo "   sudo systemctl enable droneserver"
echo ""
echo "2. Start the service:"
echo "   sudo systemctl start droneserver"
echo ""
echo "3. Check service status:"
echo "   sudo systemctl status droneserver"
echo ""
echo "4. View logs:"
echo "   sudo journalctl -u droneserver -f"
echo ""
echo "============================================================"
echo ""

