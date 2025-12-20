# Updating Your Live Server

Quick reference guide for updating the MAVLink MCP server running on your production server.

---

## 🔄 Standard Update Process

### 1. Stop the Running Server

**If running manually:**
```bash
# Press Ctrl+C in the terminal where start_http_server.sh is running
# Or if you can't find it:
pkill -f "droneserver_http.py"
```

**If running as a service:**
```bash
sudo systemctl stop droneserver
```

### 2. Pull Latest Code

```bash
cd ~/droneserver
git pull origin main
```

### 3. Update Dependencies (if needed)

```bash
uv sync
```

### 4. Restart the Server

**If running manually:**
```bash
./start_http_server.sh
```

**If running as a service:**
```bash
sudo systemctl start droneserver
```

---

## 🚀 Upgrade to systemd Services (Recommended)

If you're currently running the server manually, upgrade to systemd services for:
- ✅ Auto-start on boot
- ✅ Auto-restart on failure
- ✅ Centralized logging

### Installation

```bash
cd ~/droneserver

# Stop any manually running servers
pkill -f "droneserver_http.py"

# Install services
sudo ./install_services.sh

# Enable and start
sudo systemctl enable droneserver ngrok
sudo systemctl start droneserver ngrok

# Check status
sudo systemctl status droneserver ngrok
```

### Get Your New ngrok URL

```bash
curl -s http://localhost:4040/api/tunnels | grep -o 'https://[^"]*ngrok[^"]*'
```

Update this URL in ChatGPT Developer Mode.

---

## 📋 Quick Commands

### Check if Server is Running

```bash
# Manual mode
ps aux | grep droneserver_http

# Service mode
sudo systemctl status droneserver
```

### View Logs

```bash
# Manual mode (if running in terminal)
# Check the terminal output

# Service mode
sudo journalctl -u droneserver -f
```

### Restart After Update

```bash
# Manual mode
pkill -f "droneserver_http.py"
./start_http_server.sh

# Service mode
sudo systemctl restart droneserver
```

### Check Connection to Drone

```bash
# From server logs
sudo journalctl -u droneserver -n 50 | grep "Connected to drone"

# Test drone reachability
ping YOUR_DRONE_IP
telnet YOUR_DRONE_IP YOUR_DRONE_PORT
```

---

## 🔧 Troubleshooting Updates

### Git Pull Conflicts

**Problem:** `error: Your local changes would be overwritten by merge`

**Solution:**
```bash
# Save your local changes
git stash

# Pull updates
git pull origin main

# Restore your changes (if needed)
git stash pop
```

### uv.lock Conflicts

**Problem:** `error: Your local changes to uv.lock would be overwritten`

**Solution:**
```bash
# Discard lock file changes and pull
git checkout -- uv.lock
git pull origin main

# Regenerate dependencies
uv sync
```

### Service Won't Start After Update

**Check logs:**
```bash
sudo journalctl -u droneserver -n 50
```

**Common fixes:**
```bash
# Reload systemd after service file changes
sudo systemctl daemon-reload

# Restart the service
sudo systemctl restart droneserver

# Check permissions
cd ~/droneserver
chmod +x start_http_server.sh
```

### ngrok URL Changed

After system restart, ngrok generates a new URL.

**Get new URL:**
```bash
curl -s http://localhost:4040/api/tunnels | grep -o 'https://[^"]*ngrok[^"]*'
```

**Update in ChatGPT:**
1. Go to ChatGPT Developer Mode
2. Edit your MAVLink connector
3. Update the Server URL with new ngrok URL
4. Save changes

---

## 📊 Health Check After Update

Run these commands to verify everything is working:

```bash
# 1. Check service status
sudo systemctl status droneserver ngrok

# 2. Verify server is listening
sudo netstat -tulpn | grep 8080

# 3. Check drone connection
sudo journalctl -u droneserver -n 50 | grep -E "Connected to drone|GPS LOCK|READY"

# 4. Get ngrok URL
curl -s http://localhost:4040/api/tunnels | grep -o 'https://[^"]*ngrok[^"]*'

# 5. Test ngrok endpoint
curl https://YOUR_NGROK_URL.ngrok-free.app/sse
# Should return: "Method Not Allowed" (this is expected for SSE endpoint)
```

If all checks pass, test in ChatGPT!

---

## 🎯 Update Checklist

- [ ] Stop running server/service
- [ ] Pull latest code: `git pull origin main`
- [ ] Update dependencies: `uv sync`
- [ ] Restart server/service
- [ ] Verify drone connection in logs
- [ ] Get updated ngrok URL (if using ngrok service)
- [ ] Update URL in ChatGPT (if ngrok URL changed)
- [ ] Test with simple command in ChatGPT

---

## 📚 Related Documentation

- [SERVICE_SETUP.md](SERVICE_SETUP.md) - systemd service deployment
- [CHATGPT_SETUP.md](CHATGPT_SETUP.md) - ChatGPT integration guide
- [STATUS.md](STATUS.md) - Current features & roadmap
- [README.md](README.md) - Main documentation

