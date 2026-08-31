# Connecting LM Studio to MAVLink MCP Server

Complete guide to control your drone using natural language through LM Studio.

---

## Prerequisites

✅ **You must have:**
1. LM Studio installed ([Download here](https://lmstudio.ai/))
2. A local LLM model downloaded in LM Studio (Qwen, Llama 3.1+, or Mistral recommended for tool calling)
3. The MAVLink MCP server running and reachable over the tailnet (or on the same machine as LM Studio)

---

## Server URL

Your MCP server SSE endpoint:

```
http://<droneserver-tailnet-host>:8080/sse
```

If LM Studio runs on the same machine as the MCP server, use:

```
http://127.0.0.1:8080/sse
```

⚠️ **Note:** This server is not exposed to the public internet. LM Studio must be able to reach the host over Tailscale (or on loopback if it's the same machine).

---

## Step 1: Open LM Studio

1. Launch **LM Studio** on your computer
2. Load a model that supports tool/function calling (e.g., Qwen, Llama 3.1+, Mistral)

---

## Step 2: Open the mcp.json Editor

LM Studio configures MCP servers through a JSON file, not a graphical UI.

1. Click the **Integrations** icon (puzzle piece 🧩) at the bottom of the chat input area
2. Click the **Install** dropdown button
3. Select **"Edit mcp.json"**

This opens the `mcp.json` configuration file in an editor.

---

## Step 3: Add the Drone Server Configuration

In the `mcp.json` file, add your drone server configuration.

### If the file is empty or has `{}`:

Replace the contents with:

```json
{
  "mcpServers": {
    "droneserver": {
      "url": "http://<droneserver-tailnet-host>:8080/sse"
    }
  }
}
```

### If there are existing servers:

Add the `droneserver` entry inside the `mcpServers` object:

```json
{
  "mcpServers": {
    "existing-server": {
      "command": "...",
      "args": ["..."]
    },
    "droneserver": {
      "url": "http://<droneserver-tailnet-host>:8080/sse"
    }
  }
}
```

### Understanding the JSON Syntax

- **`{ }`** = Object (contains key-value pairs)
- **`"key": "value"`** = Key-value pair (strings use double quotes)
- **`,`** = Separates items (NO comma after the last item!)
- **`mcpServers`** = Container for all your MCP server configurations
- **`droneserver`** = Name/identifier for this server (you can change this)
- **`url`** = The SSE endpoint URL of your MCP server

⚠️ **Replace `<droneserver-tailnet-host>`** with the actual Tailscale address (or hostname) of the machine running the MCP server, or use `127.0.0.1` if it's the same machine as LM Studio.

---

## Step 4: Save and Enable

1. **Save** the `mcp.json` file (Cmd+S / Ctrl+S)
2. Close and **restart LM Studio** to load the new configuration
3. Go back to **Integrations** and toggle **droneserver** to **ON**

---

## Step 5: Verify Connection

Once enabled, LM Studio should connect and discover the available tools:

- `get_telemetry` - Get current drone position and status
- `arm` - Arm the drone motors
- `disarm` - Disarm the drone motors
- `takeoff` - Take off to specified altitude
- `land` - Land the drone
- `goto_position` - Fly to GPS coordinates
- `set_flight_mode` - Change flight mode
- And more...

You should see these tools listed when you click the Integrations icon.

---

## Step 6: Start Chatting!

Open a new chat and try these example prompts:

### Check Drone Status
```
Check if the drone is connected and show me its current position
```

### Arm and Takeoff
```
Arm the drone and take off to 10 meters
```

### Get Telemetry
```
What's the drone's current altitude and battery level?
```

### Land
```
Land the drone safely
```

---

## Complete mcp.json Example

Here's a complete example with the drone server:

```json
{
  "mcpServers": {
    "droneserver": {
      "url": "http://<droneserver-tailnet-host>:8080/sse"
    }
  }
}
```

---

## Troubleshooting

### "Invalid JSON" Error

Common JSON mistakes:
- Missing quotes around strings: `url` should be `"url"`
- Trailing comma after last item: `"url": "..."` ~~`,`~~ (remove the comma)
- Mismatched brackets: Every `{` needs a `}`

Use a JSON validator like [jsonlint.com](https://jsonlint.com) to check your syntax.

### Connection Failed

1. **Verify the URL is correct** - Check the server's Tailscale address (`tailscale status` on the server host)
2. **Confirm you're on the tailnet** - LM Studio's machine must be connected to the same Tailscale network
3. **Check the endpoint** - URL should end with `/sse`
4. **Verify server is running** - The MCP server must be active on the remote machine

### Tools Not Appearing

1. **Restart LM Studio** after saving `mcp.json`
2. **Toggle the server ON** in the Integrations panel
3. **Start a new chat** - Tools may not appear in existing conversations
4. **Check for errors** in the LM Studio console/logs

### Commands Not Executing

1. **Use a capable model** - Some models don't support tool calling well
2. **Check server logs** for error messages
3. **Verify GPS lock** - Many drone commands require GPS

---

## Notes

- The server's Tailscale address is stable across restarts, but confirm it with `tailscale status` if you move the server to a different host.
- Models with strong function calling support (Qwen, Mistral, Llama 3.1+) work best.
- Keep the server toggle **enabled** in Integrations for tools to be available.

---

## Support

- 📖 [Main README](README.md)
- 📊 [Status & Roadmap](STATUS.md)
- 🐛 [Report Issues](https://github.com/PeterJBurke/droneserver/issues)

---

**Happy Flying with LM Studio! 🚁🤖**
