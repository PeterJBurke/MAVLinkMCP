# Security

## Deployment posture

DroneServer is built to be run on a private overlay network, not on the public
internet. The reference deployment:

- **Zero publicly reachable ports.** The host runs a default-deny firewall. Where
  containers are involved it also carries `DOCKER-USER` rules, because published
  container ports bypass `ufw`. The posture is verified by scanning from outside
  the network, not by reading the firewall config.
- **Tailnet-only reachability.** The MCP server, the vehicle links, and the
  development infrastructure all sit on a WireGuard/Tailscale overlay. Clients
  reach the server at its tailnet address, or on loopback when the client runs
  on the same host.
- **No public tunnel.** ngrok or any equivalent tunnel is not part of this
  deployment. Every LLM is reached by *outbound* API calls made from a client
  inside the tailnet, so the server never needs an inbound public endpoint.
- **Scoped API keys.** Clients authenticate with a key carrying a scope —
  `telemetry` (read only), `control` (fly it), or `admin`. Configured via
  `SAFETY_API_KEYS`; see [`docs/safety_review.md`](docs/safety_review.md) §6.

If you bind the server to a publicly routable interface, you are operating
outside its threat model: anyone who reaches the port and holds a `control`-scoped
key can fly the aircraft. `MCP_HOST` defaults wide so that a tailnet address can
be bound; restricting exposure is the deployment's job, and it is not optional.

## The commanding model is untrusted

The safety layer treats the LLM issuing tool calls as an adversary: it may be
confused, may have been steered by injected text in its own context, or may
invent a justification for a dangerous action. Criticality tiers, single-use
confirmation tokens, an independent server-side geofence, parameter bounds,
state preconditions, rate limits, and an append-only audit log are all enforced
server-side, in front of the tool body, and the model cannot disable them. The
guard fails closed: if a check raises, the command is refused.

This is documented in full in [`docs/safety_review.md`](docs/safety_review.md),
and the adversarial cases it is tested against are in
[`docs/adversarial_results.md`](docs/adversarial_results.md).

The safety layer does not make an aircraft safe. It refuses commands. Physical
safety remains the operator's responsibility.

## Reporting a vulnerability

Please report privately rather than opening a public issue:

- **Preferred:** open a private security advisory at
  <https://github.com/PeterJBurke/droneserver/security/advisories/new>.
- If that is unavailable to you, contact the repository owner
  ([@PeterJBurke](https://github.com/PeterJBurke)) through GitHub.

Useful things to include: the version or commit, how the server was configured
(especially which `SAFETY_*` switches were in force), and what an attacker gains.
Reports that concern a safety-layer bypass — anything that reaches the aircraft
without passing the guard, or that defeats a confirmation token, geofence, or
scope check — are the highest priority.

This is a research project maintained by a small academic group; please allow
reasonable time for a response before disclosing publicly.

## Supported versions

The `v2.x` line is the supported one. `v1.x` predates the safety layer entirely
and its documentation instructed users to expose the server through a public
tunnel; it should not be deployed.
