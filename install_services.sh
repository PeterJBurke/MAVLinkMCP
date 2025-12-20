#!/bin/bash
# install_services.sh - Install MAVLink MCP and ngrok as systemd services

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

# Check if ngrok is installed
if ! command -v ngrok &> /dev/null; then
    echo "⚠️  WARNING: ngrok is not installed or not in PATH"
    echo "   Please install ngrok first:"
    echo "   curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null"
    echo "   echo \"deb https://ngrok-agent.s3.amazonaws.com buster main\" | sudo tee /etc/apt/sources.list.d/ngrok.list"
    echo "   sudo apt update && sudo apt install ngrok"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if ngrok is authenticated
if [ ! -f "$HOME/.config/ngrok/ngrok.yml" ]; then
    echo "⚠️  WARNING: ngrok is not authenticated"
    echo "   Please authenticate ngrok first:"
    echo "   ngrok config add-authtoken YOUR_AUTHTOKEN"
    echo "   Get your authtoken from: https://dashboard.ngrok.com/get-started/your-authtoken"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Update service files with correct paths
echo "📝 Updating service files with installation directory..."
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$INSTALL_DIR|g" droneserver.service
sed -i "s|ExecStart=.*start_http_server.sh|ExecStart=$INSTALL_DIR/start_http_server.sh|g" droneserver.service

# Copy service files to systemd directory
echo "📋 Copying service files to /etc/systemd/system/..."
cp droneserver.service /etc/systemd/system/
cp ngrok.service /etc/systemd/system/

# Set correct permissions
chmod 644 /etc/systemd/system/droneserver.service
chmod 644 /etc/systemd/system/ngrok.service

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
echo "1. Enable services to start on boot:"
echo "   sudo systemctl enable droneserver"
echo "   sudo systemctl enable ngrok"
echo ""
echo "2. Start the services:"
echo "   sudo systemctl start droneserver"
echo "   sudo systemctl start ngrok"
echo ""
echo "3. Check service status:"
echo "   sudo systemctl status droneserver"
echo "   sudo systemctl status ngrok"
echo ""
echo "4. View logs:"
echo "   sudo journalctl -u droneserver -f"
echo "   sudo journalctl -u ngrok -f"
echo ""
echo "5. Get ngrok URL:"
echo "   curl -s http://localhost:4040/api/tunnels | grep -o 'https://[^\"]*ngrok[^\"]*'"
echo ""
echo "============================================================"
echo ""

