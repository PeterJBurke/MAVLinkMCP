# droneserver.safety (scaffold)

Placeholder package for the Phase 3 safety & security layer. Planned contents:

- `validation.py` - command validation middleware: parameter bounds (max altitude,
  speed limits), state preconditions (no goto before takeoff), rate limiting.
- `geofence.py` - server-side geofence enforcement (independent of the firmware
  fence): reject/clip commands outside a configured polygon + altitude ceiling.
- `tiers.py` - criticality tiers (read-only / normal / critical) with explicit
  confirmation-token round-trip for critical tools (kill, disarm-in-air,
  RTL-override).
- `auth.py` - per-client API keys/tokens with scoped permissions.
- `audit.py` - append-only JSONL audit log of every tool call (timestamp, client,
  args, validation result, MAVLink outcome, latency).

Nothing here is wired up yet; tools currently call MavSDK directly as in v1.
