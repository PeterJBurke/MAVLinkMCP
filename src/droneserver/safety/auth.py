"""Per-client API keys and scopes.

Keys are configured out-of-band (``SAFETY_API_KEYS`` env var / .env) as::

    client_id:key:scope,client_id:key:scope

Scopes (ordered):

- ``telemetry`` - READ_ONLY tools only
- ``control``   - READ_ONLY + NORMAL + CRITICAL (with confirmation) + EMERGENCY
- ``admin``     - everything control can do; reserved for future
                  configuration-changing endpoints

Keys are NEVER logged: the audit record stores the ``client_id`` and a short
key fingerprint, never the key itself, and :class:`SafetySettings` overrides
``__repr__`` so a traceback cannot leak them.
"""

import hashlib
import hmac
from dataclasses import dataclass

from droneserver.safety.config import SafetySettings
from droneserver.safety.tiers import Tier

SCOPE_ORDER = {"telemetry": 0, "control": 1, "admin": 2}

#: Minimum scope required per tier.
TIER_MIN_SCOPE: dict[Tier, str] = {
    Tier.READ_ONLY: "telemetry",
    Tier.NORMAL: "control",
    Tier.CRITICAL: "control",
    Tier.EMERGENCY: "control",
}


@dataclass(frozen=True)
class Client:
    client_id: str
    scope: str
    authenticated: bool
    #: Short hash of the presented key - safe to log; the key itself never is.
    key_fingerprint: str = ""

    def can(self, tier: Tier) -> bool:
        needed = TIER_MIN_SCOPE.get(tier, "admin")
        return SCOPE_ORDER.get(self.scope, -1) >= SCOPE_ORDER[needed]


ANONYMOUS = Client(client_id="anonymous", scope="telemetry", authenticated=False)


def key_fingerprint_of(key: str) -> str:
    """Short, non-reversible fingerprint of a key (for audit records)."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def parse_api_keys(spec: str) -> dict[str, tuple[str, str]]:
    """Parse the key spec into ``{key: (client_id, scope)}``."""
    registry: dict[str, tuple[str, str]] = {}
    for entry in (e.strip() for e in (spec or "").split(",") if e.strip()):
        parts = entry.split(":")
        if len(parts) != 3:
            raise ValueError("SAFETY_API_KEYS entries must be 'client_id:key:scope'")
        client_id, key, scope = (p.strip() for p in parts)
        if scope not in SCOPE_ORDER:
            raise ValueError(f"unknown scope {scope!r} for client {client_id!r}; use one of {sorted(SCOPE_ORDER)}")
        if not client_id or not key:
            raise ValueError("SAFETY_API_KEYS entries need a non-empty client_id and key")
        registry[key] = (client_id, scope)
    return registry


def authenticate(presented_key: str | None, s: SafetySettings) -> Client:
    """Resolve a presented key to a client.

    Unauthenticated behavior follows ``unauthenticated_scope``:
    ``telemetry`` (default, read-only), ``control`` (open server - only for
    an isolated bench), or ``reject`` (scope "none", every tier denied).
    """
    if not s.auth_enabled:
        return Client("unauthenticated", "admin", authenticated=False)

    registry = parse_api_keys(s.api_keys)
    if presented_key:
        # constant-time compare against each configured key
        for key, (client_id, scope) in registry.items():
            if hmac.compare_digest(key, presented_key):
                return Client(client_id, scope, True, key_fingerprint_of(key))

    if s.unauthenticated_scope == "reject":
        return Client("anonymous", "none", authenticated=False)
    scope = s.unauthenticated_scope if s.unauthenticated_scope in SCOPE_ORDER else "telemetry"
    return Client("anonymous", scope, authenticated=False)


def authorization_failed_result(client: Client, tool: str, tier: Tier) -> dict:
    needed = TIER_MIN_SCOPE.get(tier, "admin")
    return {
        "status": "rejected",
        "error": (f"{tool} requires '{needed}' scope; this client ({client.client_id}) has '{client.scope}'."),
        "rule": "authz.insufficient_scope",
        "remedy": (
            "Use an API key with sufficient scope. Telemetry-scope clients can read state "
            "(get_position, get_battery, ...) but cannot command the drone."
        ),
        "safety_layer": "droneserver.safety",
    }
