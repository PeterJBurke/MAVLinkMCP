# Service Installation Guide

This guide will help you set up the MAVLink MCP Server and ngrok as systemd services that run automatically on boot and restart on failure.

## 🎯 Overview

Running as systemd services provides:
- ✅ **Automatic startup** on system boot
- ✅ **Auto-restart** on crashes or failures
- ✅ **Centralized logging** with `journalctl`
- ✅ **Easy management** with `systemctl` commands
- ✅ **Production-ready** deployment

---

## 📋 Prerequisites

### 1. Install ngrok

```bash
# Add ngrok repository
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list

# Install ngrok
sudo apt update
sudo apt install ngrok
```

### 2. Authenticate ngrok

Get your authtoken from: https://dashboard.ngrok.com/get-started/your-authtoken

```bash
ngrok config add-authtoken YOUR_AUTHTOKEN
```

### 3. Configure Your .env File

Make sure your `.env` file is properly configured:

```bash
cd ~/droneserver
cp .env.example .env
nano .env  # Edit with your drone connection details
```

---

## 🚀 Installation

### Quick Install (Recommended)

```bash
cd ~/droneserver
sudo ./install_services.sh
```

The script will:
1. ✅ Check for ngrok installation
2. ✅ Check for ngrok authentication
3. ✅ Copy service files to `/etc/systemd/system/`
4. ✅ Set correct permissions
5. ✅ Reload systemd daemon

---

## 🎮 Service Management

### Enable Services (Start on Boot)

```bash
sudo systemctl enable droneserver
sudo systemctl enable ngrok
```

### Start Services

```bash
sudo systemctl start droneserver
sudo systemctl start ngrok
```

### Stop Services

```bash
sudo systemctl stop droneserver
sudo systemctl stop ngrok
```

### Restart Services

```bash
sudo systemctl restart droneserver
sudo systemctl restart ngrok
```

### Check Status

```bash
# Check both services
sudo systemctl status droneserver
sudo systemctl status ngrok

# Quick status check
sudo systemctl is-active droneserver
sudo systemctl is-active ngrok
```

### Disable Services (Prevent Auto-Start)

```bash
sudo systemctl disable droneserver
sudo systemctl disable ngrok
```

---

## 📊 Viewing Logs

### Real-Time Logs (Follow Mode)

```bash
# MCP Server logs
sudo journalctl -u droneserver -f

# ngrok logs
sudo journalctl -u ngrok -f

# Both services together
sudo journalctl -u droneserver -u ngrok -f
```

### Recent Logs

```bash
# Last 100 lines
sudo journalctl -u droneserver -n 100

# Last hour
sudo journalctl -u droneserver --since "1 hour ago"

# Today's logs
sudo journalctl -u droneserver --since today
```

### Filtered Logs

```bash
# Only errors
sudo journalctl -u droneserver -p err

# Search for specific text
sudo journalctl -u droneserver | grep "GPS LOCK"
```

---

## 🔗 Getting Your ngrok URL

### Option 1: From ngrok API

```bash
curl -s http://localhost:4040/api/tunnels | grep -o 'https://[^"]*ngrok[^"]*'
```

### Option 2: From ngrok Logs

```bash
sudo journalctl -u ngrok -n 50 | grep "Forwarding"
```

### Option 3: Open ngrok Web Interface

Open in browser: http://localhost:4040

---

## 🔧 Troubleshooting

### Service Won't Start

```bash
# Check detailed status
sudo systemctl status droneserver -l

# Check logs for errors
sudo journalctl -u droneserver -n 50
```

### Permission Errors

```bash
# Make sure scripts are executable
cd ~/droneserver
chmod +x start_http_server.sh
sudo systemctl restart droneserver
```

### Port Already in Use

```bash
# Check what's using port 8080
sudo netstat -tulpn | grep 8080

# Kill the process if needed
sudo pkill -f droneserver_http
sudo systemctl restart droneserver
```

### ngrok Authentication Failed

```bash
# Re-authenticate
ngrok config add-authtoken YOUR_AUTHTOKEN

# Restart ngrok service
sudo systemctl restart ngrok
```

### Drone Connection Issues

```bash
# Check if drone is reachable
ping YOUR_DRONE_IP

# Check if port is open
telnet YOUR_DRONE_IP YOUR_DRONE_PORT

# Verify .env configuration
cat ~/droneserver/.env

# Check MCP server logs
sudo journalctl -u droneserver -f
```

---

## 🔄 Updating the Server

When you pull new code from GitHub:

```bash
cd ~/droneserver
git pull origin main

# Restart the service to load new code
sudo systemctl restart droneserver
```

No need to reinstall the service unless the service files themselves changed.

---

## 🛠️ Advanced Configuration

### Customize Service Files

Service files are located at:
- `/etc/systemd/system/droneserver.service`
- `/etc/systemd/system/ngrok.service`

After editing:
```bash
sudo systemctl daemon-reload
sudo systemctl restart droneserver
sudo systemctl restart ngrok
```

### Change MCP Server Port

Edit your `.env` file:
```bash
nano ~/droneserver/.env
# Change MCP_PORT=8080 to your desired port
```

Then update ngrok service:
```bash
sudo nano /etc/systemd/system/ngrok.service
# Change: ExecStart=/usr/local/bin/ngrok http YOUR_NEW_PORT
sudo systemctl daemon-reload
sudo systemctl restart ngrok
```

### Run as Non-Root User

Edit both service files and change:
```ini
User=root
```
to:
```ini
User=your_username
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl restart droneserver ngrok
```

---

## 📦 Uninstalling Services

### Stop and Disable Services

```bash
sudo systemctl stop droneserver ngrok
sudo systemctl disable droneserver ngrok
```

### Remove Service Files

```bash
sudo rm /etc/systemd/system/droneserver.service
sudo rm /etc/systemd/system/ngrok.service
sudo systemctl daemon-reload
```

---

## ✅ Verification Checklist

After installation, verify everything works:

- [ ] Services are enabled: `sudo systemctl is-enabled droneserver ngrok`
- [ ] Services are running: `sudo systemctl is-active droneserver ngrok`
- [ ] MCP server logs show "Drone is READY": `sudo journalctl -u droneserver -n 50`
- [ ] ngrok shows "Forwarding" URL: `sudo journalctl -u ngrok -n 20`
- [ ] Can get ngrok URL: `curl http://localhost:4040/api/tunnels`
- [ ] ChatGPT can connect to ngrok URL

---

## 🎯 Quick Reference

```bash
# Installation
sudo ./install_services.sh

# Enable and start
sudo systemctl enable droneserver ngrok
sudo systemctl start droneserver ngrok

# Check status
sudo systemctl status droneserver ngrok

# View logs
sudo journalctl -u droneserver -f

# Get ngrok URL
curl -s http://localhost:4040/api/tunnels | grep -o 'https://[^"]*ngrok[^"]*'

# Restart after code update
git pull origin main
sudo systemctl restart droneserver

# Stop services
sudo systemctl stop droneserver ngrok
```

---

## 📚 Additional Resources

- [systemd Service Documentation](https://www.freedesktop.org/software/systemd/man/systemd.service.html)
- [ngrok Documentation](https://ngrok.com/docs)
- [journalctl Manual](https://www.freedesktop.org/software/systemd/man/journalctl.html)

---

**Need help?** Check the [main README](README.md) or [ChatGPT Setup Guide](CHATGPT_SETUP.md).

