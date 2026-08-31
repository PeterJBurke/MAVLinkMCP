# Service Installation Guide

This guide will help you set up the MAVLink MCP Server as a systemd service that runs automatically on boot and restarts on failure.

## 🎯 Overview

Running as a systemd service provides:
- ✅ **Automatic startup** on system boot
- ✅ **Auto-restart** on crashes or failures
- ✅ **Centralized logging** with `journalctl`
- ✅ **Easy management** with `systemctl` commands
- ✅ **Production-ready** deployment

## 🔒 Reaching the Server

This server is not exposed to the public internet. It binds to the host's
Tailscale (tailnet) address, or to `127.0.0.1` when the client runs on the
same machine. The host itself should have zero public ports: ufw
default-deny, plus `DOCKER-USER` firewall rules if you're running anything
in Docker (published container ports otherwise bypass ufw). Clients — LM
Studio, other MCP clients, your own tooling — reach the server over the
tailnet, not over the public internet.

---

## 📋 Prerequisites

### Configure Your .env File

Make sure your `.env` file is properly configured:

```bash
cd ~/droneserver
cp .env.example .env
nano .env  # Edit with your drone connection details and MCP_HOST
```

Set `MCP_HOST` to the host's Tailscale address (or leave it at the
loopback default if clients run on the same machine).

---

## 🚀 Installation

### Quick Install (Recommended)

```bash
cd ~/droneserver
sudo ./install_services.sh
```

The script will:
1. ✅ Copy the service file to `/etc/systemd/system/`
2. ✅ Set correct permissions
3. ✅ Reload systemd daemon

---

## 🎮 Service Management

### Enable Service (Start on Boot)

```bash
sudo systemctl enable droneserver
```

### Start Service

```bash
sudo systemctl start droneserver
```

### Stop Service

```bash
sudo systemctl stop droneserver
```

### Restart Service

```bash
sudo systemctl restart droneserver
```

### Check Status

```bash
sudo systemctl status droneserver

# Quick status check
sudo systemctl is-active droneserver
```

### Disable Service (Prevent Auto-Start)

```bash
sudo systemctl disable droneserver
```

---

## 📊 Viewing Logs

### Real-Time Logs (Follow Mode)

```bash
sudo journalctl -u droneserver -f
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

### Can't Reach the Server Over the Tailnet

```bash
# Confirm Tailscale is up and note the host's tailnet address
tailscale status

# Confirm MCP_HOST in .env matches the tailnet address (or 127.0.0.1
# for same-machine clients)
cat ~/droneserver/.env | grep MCP_HOST

# Confirm the port isn't blocked by ufw
sudo ufw status
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

No need to reinstall the service unless the service file itself changed.

---

## 🛠️ Advanced Configuration

### Customize Service File

The service file is located at:
- `/etc/systemd/system/droneserver.service`

After editing:
```bash
sudo systemctl daemon-reload
sudo systemctl restart droneserver
```

### Change MCP Server Port

Edit your `.env` file:
```bash
nano ~/droneserver/.env
# Change MCP_PORT=8080 to your desired port
```

Then restart the service:
```bash
sudo systemctl restart droneserver
```

### Run as Non-Root User

Edit the service file and change:
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
sudo systemctl restart droneserver
```

---

## 📦 Uninstalling the Service

### Stop and Disable the Service

```bash
sudo systemctl stop droneserver
sudo systemctl disable droneserver
```

### Remove the Service File

```bash
sudo rm /etc/systemd/system/droneserver.service
sudo systemctl daemon-reload
```

---

## ✅ Verification Checklist

After installation, verify everything works:

- [ ] Service is enabled: `sudo systemctl is-enabled droneserver`
- [ ] Service is running: `sudo systemctl is-active droneserver`
- [ ] MCP server logs show "Drone is READY": `sudo journalctl -u droneserver -n 50`
- [ ] Server is reachable over the tailnet: `curl http://<droneserver-tailnet-host>:8080/sse`
- [ ] Host has zero public ports: check `ufw status` (and `DOCKER-USER` rules if using Docker)

---

## 🎯 Quick Reference

```bash
# Installation
sudo ./install_services.sh

# Enable and start
sudo systemctl enable droneserver
sudo systemctl start droneserver

# Check status
sudo systemctl status droneserver

# View logs
sudo journalctl -u droneserver -f

# Restart after code update
git pull origin main
sudo systemctl restart droneserver

# Stop service
sudo systemctl stop droneserver
```

---

## 📚 Additional Resources

- [systemd Service Documentation](https://www.freedesktop.org/software/systemd/man/systemd.service.html)
- [journalctl Manual](https://www.freedesktop.org/software/systemd/man/journalctl.html)

---

**Need help?** Check the [main README](README.md) or [interactive chat-client guide](CHATGPT_SETUP.md).
