# Connecting LM Studio to MAVLink MCP Server

Complete guide to control your drone using natural language through LM Studio.

---

## Prerequisites

✅ **You must have:**
1. LM Studio installed ([Download here](https://lmstudio.ai/))
2. A local LLM model downloaded in LM Studio (Qwen, Llama 3.1+, or Mistral recommended for tool calling)
3. The MAVLink MCP server running and exposed via ngrok
4. The ngrok URL from your `.env` file (see `NGROK_URL` variable)

---

## Server URL

Your MCP server SSE endpoint (from `.env`):

```
https://YOUR_NGROK_URL.ngrok-free.app/sse
```

⚠️ **Note:** Get the actual URL from your `.env` file (`NGROK_URL` variable). Do not share this URL publicly.

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
      "url": "https://YOUR_NGROK_URL.ngrok-free.app/sse"
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
      "url": "https://YOUR_NGROK_URL.ngrok-free.app/sse"
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
- **`url`** = The SSE endpoint URL of your remote MCP server

⚠️ **Replace `YOUR_NGROK_URL`** with the actual subdomain from your `.env` file.

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
      "url": "https://YOUR_NGROK_URL.ngrok-free.app/sse"
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

1. **Verify the URL is correct** - Check your `.env` file for the `NGROK_URL` value
2. **Ensure HTTPS** - The URL must start with `https://`
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

- The ngrok URL may change if the server restarts. Update your `mcp.json` accordingly.
- Models with strong function calling support (Qwen, Mistral, Llama 3.1+) work best.
- Keep the server toggle **enabled** in Integrations for tools to be available.

---

## Support

- 📖 [Main README](README.md)
- 📊 [Status & Roadmap](STATUS.md)
- 🐛 [Report Issues](https://github.com/PeterJBurke/droneserver/issues)

---

**Happy Flying with LM Studio! 🚁🤖**
