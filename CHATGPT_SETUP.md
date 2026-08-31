# Driving DroneServer from an interactive chat client

This is one of the two supported ways to operate the server. A human reads every
reply and decides what to ask for next; the model calls the MCP tools. The other
way — the scripted harness that produced the paper's numbers, with no human in the
per-turn loop — is [`docs/reproduce.md`](docs/reproduce.md).

---

## What connects, and what does not

The server speaks MCP over HTTP/SSE and is reachable **only on the tailnet** (or
on loopback, when the client runs on the same host). The host has zero publicly
reachable ports. That constraint decides which clients work:

| Client | Works? | Why |
|---|---|---|
| Claude Desktop, or any MCP client running on a machine on the tailnet | **yes** | The client process itself resolves and reaches the tailnet address. |
| LM Studio on a tailnet machine | **yes** | See [LMSTUDIO_SETUP.md](LMSTUDIO_SETUP.md). |
| Any client you run yourself inside the tailnet | **yes** | Same reason. |
| A hosted web connector that fetches your server from the vendor's cloud (ChatGPT's Developer Mode connector, and equivalents) | **no** | It requires a publicly reachable HTTPS endpoint. This deployment does not have one and will not stand one up. |

### Historical note

Earlier versions of this repository documented exactly that hosted-connector
path, reached through a public `ngrok` tunnel, and that is how the v1 paper's
interactive figures were produced. **Those instructions have been removed.** A
public tunnel puts an inbound, internet-reachable endpoint in front of an
aircraft; the v2 deployment has zero public ports and uses no tunnel anywhere.
See [SECURITY.md](SECURITY.md).

If you want to fly using a commercial hosted model, use its **API** rather than
its web-chat connector — an outbound call from a client inside the tailnet, which
is what `scripts/run_llm_missions.py` does. That is the same reason this project
uses per-token API access rather than a chat subscription.

---

## Step 1 — configure the server

In `.env`:

```bash
# where the aircraft is
MAVLINK_ADDRESS=<your-drone-or-sitl-host>
MAVLINK_PORT=14540
MAVLINK_PROTOCOL=tcp          # tcp, udp, or serial

# where the MCP server listens
MCP_HOST=127.0.0.1            # or this host's tailnet address
MCP_PORT=8080
```

Bind `MCP_HOST` to loopback if the chat client runs on the same machine, or to
this host's **tailnet** address if it does not. Never bind it to a publicly
routable interface.

Set `SAFETY_API_KEYS` so clients must authenticate, and give the chat client a
`control`-scoped key. The format is documented in
[`docs/safety_review.md`](docs/safety_review.md) §6.

## Step 2 — start the server

```bash
cd ~/droneserver
./start_http_server.sh
```

Expected output ends with the server reporting the drone connection, GPS lock,
and that it is exposing its tools.

For a persistent deployment run it under systemd instead — see
[SERVICE_SETUP.md](SERVICE_SETUP.md).

## Step 3 — point the client at it

The MCP endpoint is:

```
http://<droneserver-tailnet-host>:8080/sse
```

or `http://127.0.0.1:8080/sse` from the same host. Add it as an HTTP/SSE MCP
server in your client's configuration, with the API key presented as
`DRONESERVER_API_KEY`. LM Studio's exact `mcp.json` shape is in
[LMSTUDIO_SETUP.md](LMSTUDIO_SETUP.md); Claude Desktop takes an equivalent entry
in its own config file.

## Step 4 — verify before flying anything

Ask for telemetry first — `get_position`, `get_battery`, `get_health` — and check
that the values match what the simulator or aircraft is actually doing. If a
read-only call fails, the flying calls will too, and you will find out at a worse
moment.

Then confirm the guard is live by asking for something it must refuse: a waypoint
outside the geofence, or `kill_motors` without a confirmation token. The first
should come back as a structured rejection naming the rule; the second should
come back as a confirmation token plus a plain statement of the consequence, and
the motors should not have stopped.

---

## Flying a mission conversationally

A worked example. Give the model the mission in one instruction and let it choose
the tools:

```
Arm the drone, take off to 50 metres, fly to 33.6458, -117.8426, and land.

After each monitor_flight, print the DISPLAY_TO_USER value.
Keep calling monitor_flight until mission_complete is true.
```

What the server does with that:

1. `arm_drone()` — motors armed.
2. `takeoff(50)` — **does not return** until the aircraft reaches 50 m, so the
   model cannot navigate at low altitude.
3. `go_to_location(...)` — returns immediately, registering the destination.
4. `monitor_flight()` — each call blocks for 30 s, checking arrival every second,
   then returns a progress line. Within 20 m of the destination it initiates the
   landing itself, waits for confirmed touchdown (ON_GROUND, not in_air,
   altitude < 2 m, held 3 s), and only then returns `mission_complete: true`.

The 30-second block is deliberate and the model cannot shorten it: a three-minute
flight then costs about six tool calls instead of forty, which keeps a chat client
under its tool-call ceiling.

`land()` called early is refused while the aircraft is far from its registered
destination — the landing gate — rather than putting the aircraft on the ground
somewhere arbitrary.

For long missions, prefer the managed-mission tools (`start_managed_mission`,
`get_mission_status`, `control_managed_mission`): mission state lives on the
server, so the mission survives the chat client disconnecting. See
[`docs/long_mission_demo.md`](docs/long_mission_demo.md).

---

## Troubleshooting

**Client cannot reach the server.** Check the tailnet first — `tailscale status`
on both ends, then `ping` the server's tailnet name. Then check the server is
listening on the address you think it is: `ss -tlnp | grep 8080`. A server bound
to `127.0.0.1` is invisible to every other machine, including tailnet peers; that
is the usual cause.

**Connects, but every call is refused.** Look at the rule id in the refusal. An
`authz.insufficient_scope` means the key is telemetry-scoped. A `geofence.*`
means the fence is configured for somewhere other than where the aircraft is —
recentre it on the site's real coordinates before flying.

**`kill_motors` or another critical tool "does nothing".** That is the
confirmation handshake working: re-issue the call quoting the exact token from
the first response, within its TTL. Tokens are single-use and bound to both the
tool and the arguments.

**Nothing appears in the log.** Every call, allowed or refused, is written to the
append-only audit log; if it is empty, the client is not reaching the server at
all. See [`docs/safety_review.md`](docs/safety_review.md) §7.

---

## Before you fly a real aircraft

Keep visual line of sight and a live manual RC override. Verify GPS lock and
battery before arming. Configure the geofence for the site you are actually at,
not the one in the example. The safety layer refuses commands; it does not make
an aircraft safe.
