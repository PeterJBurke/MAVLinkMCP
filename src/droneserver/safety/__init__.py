"""Safety & security layer (Plan 01 Phase 3).

Reviewer entry point: ``docs/safety_review.md``.

Modules
-------
``config``            SafetySettings - every limit and per-component switch
``tiers``             criticality table (read_only / normal / critical / emergency)
``validation``        parameter bounds, state preconditions, rate limiting
``geofence``          server-side fence (pure geometry), independent of firmware
``tokens``            single-use confirmation tokens for critical tools
``auth``              API keys and scopes
``audit``             append-only JSONL log (also the latency instrumentation)
``state``             cached vehicle-state snapshot used by preconditions
``middleware``        the pipeline; wraps every tool at registration
``offboard_watchdog`` stale-setpoint auto-brake (built in Phase 2)
"""

from droneserver.safety.middleware import LAYER, guard
from droneserver.safety.offboard_watchdog import OffboardWatchdog
from droneserver.safety.tiers import TOOL_TIERS, Tier, effective_tier

__all__ = ["LAYER", "OffboardWatchdog", "TOOL_TIERS", "Tier", "effective_tier", "guard"]
