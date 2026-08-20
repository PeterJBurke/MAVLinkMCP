"""Reading ``landed_state`` and ``in_air`` when the autopilot stops sending them.

**The subscription that dies quietly.** ``landed_state`` rides MAVLink's
EXTENDED_SYS_STATE, which ArduPilot does not stream unsolicited. MAVSDK asks
for it once - a single SET_MESSAGE_INTERVAL request, sent when something first
subscribes (this server sends it from
:meth:`droneserver.safety.state.StateTracker._ensure_rates`, once per
connection). A request is a MAVLink message like any other: if it is lost, or
if the autopilot reboots, or a link drops and comes back, nothing re-sends it.
The subscription is then a passive wait for a message that will never arrive,
and it is *indistinguishable from a vehicle that never leaves the ground*.

Both lanes that showed it on 2026-08-19 looked like different bugs. The topics
that carry POSITION kept arriving the whole time, so the link was plainly
alive; the aircraft's own ground evidence - the evidence FIX 10, 11 and 12 all
moved TO, precisely because no arming can spoil it - simply stopped, and the
consumers waited on it. ``tools/action._telemetry_now`` waited without any
bound at all.

This module is the same shape as :mod:`droneserver.telemetry.home`, which
solved it for HOME_POSITION: probe the subscription briefly, and if it says
nothing, ask for the stream and read again.

**The one rule that keeps it honest.** A dead link is silent on every topic,
and a silent link must never be dressed up as a topic that needs re-requesting.
So before any re-request, another stream has to be demonstrably live: if
``position`` is not arriving either, this reports "no reading" exactly as
before and sends nothing. Re-requesting a rate from an autopilot that is not
there would achieve nothing except to hide, in the logs, the fact that the
whole link is down. Every re-request that IS sent is logged, with a count, so
a lane that has been limping along re-requesting all flight is visible after
the fact rather than silently healthy-looking.
"""

from __future__ import annotations

import asyncio
import time

from droneserver.logging_setup import logger

#: How long to wait on the passive subscription before calling it silent. Both
#: topics stream at 2 Hz when they stream at all, so a topic that is alive
#: answers many times over inside this.
PROBE_TIMEOUT_S = 2.5
#: Bound on the witness read that decides "this topic" from "everything".
WITNESS_TIMEOUT_S = 2.0
#: Rate asked for when a topic has gone silent.
REQUEST_RATE_HZ = 2.0
#: Bound on the rate request itself, so a firmware that never answers one
#: cannot consume the caller's whole budget.
REQUEST_TIMEOUT_S = 3.0
#: Smallest gap between two re-requests of the same topic. A request that is
#: going to work works within a second or two; repeating it every poll would
#: put a SET_MESSAGE_INTERVAL burst on the link at exactly the moment the link
#: is already struggling.
REREQUEST_COOLDOWN_S = 20.0
#: Total budget for one read when the caller does not name one.
DEFAULT_TIMEOUT_S = 8.0

#: The MAVSDK setter that asks for each topic.
_SET_RATE = {
    "landed_state": "set_rate_landed_state",
    "in_air": "set_rate_in_air",
}

#: When each topic was last re-requested (monotonic), and how many times it has
#: been re-requested in this process. The count is deliberately readable: a
#: flight that needed twenty re-requests was not a healthy flight.
_last_request_at: dict[str, float] = {}
rerequests: dict[str, int] = {}


def reset_rerequests() -> None:
    """Forget the re-request history (a fresh link, or a test)."""
    _last_request_at.clear()
    rerequests.clear()


async def _first(stream, timeout_s: float):
    async def read():
        async for item in stream:
            return item
        raise TimeoutError("stream ended without an item")

    return await asyncio.wait_for(read(), timeout=timeout_s)


async def _read(drone, topic: str, timeout_s: float):
    """One item of ``topic``. Raises whatever the stream raises."""
    return await _first(getattr(drone.telemetry, topic)(), timeout_s)


async def link_is_live(drone, timeout_s: float = WITNESS_TIMEOUT_S) -> bool:
    """Is ANY telemetry still arriving? The witness that a dead link is dead.

    ``position`` is the witness because it is the one topic every firmware
    streams unasked, at rate, on both stacks this server supports - so its
    silence is about the link, not about a request that was lost.
    """
    try:
        await _read(drone, "position", timeout_s)
        return True
    except Exception:
        return False


async def request_stream(drone, topic: str) -> bool:
    """Ask the autopilot to publish ``topic``. ``True`` if it accepted.

    Best effort: MAVSDK raises when a firmware denies the rate, and a denial is
    not worth propagating - the caller re-reads either way.
    """
    setter = _SET_RATE.get(topic)
    if setter is None:
        return False
    try:
        await asyncio.wait_for(getattr(drone.telemetry, setter)(REQUEST_RATE_HZ), timeout=REQUEST_TIMEOUT_S)
        return True
    except Exception as e:
        logger.warning("the autopilot did not accept a rate request for %s: %s: %s", topic, type(e).__name__, e)
        return False


async def read_topic(drone, topic: str, timeout_s: float = DEFAULT_TIMEOUT_S, link_live: bool | None = None):
    """One reading of ``topic``, re-requesting the stream if it has gone silent.

    ``link_live`` is the caller's own evidence that telemetry is still arriving
    - most callers have just read position at the same instant, and passing
    that saves this module reading a witness of its own. ``None`` means "I have
    no evidence", and the witness is read here.

    Returns ``None`` when there is no reading to be had - a genuinely dead
    link, a firmware that does not publish the topic, or a request the
    autopilot ignored. ``None`` is the honest third answer everywhere in
    :mod:`droneserver.telemetry.ground`, and it is never a guess about where
    the aircraft is.
    """
    probe_s = min(PROBE_TIMEOUT_S, timeout_s)
    try:
        return await _read(drone, topic, probe_s)
    except Exception:
        pass

    # Silent. Asking again immediately would put a SET_MESSAGE_INTERVAL burst
    # on a link that is already struggling, and would re-probe a firmware that
    # simply does not publish this topic on every single poll, so a topic that
    # has been asked for recently is left alone until the cooldown is up.
    now = time.monotonic()
    if (now - _last_request_at.get(topic, float("-inf"))) < REREQUEST_COOLDOWN_S:
        return None

    if not (link_live if link_live is not None else await link_is_live(drone, min(WITNESS_TIMEOUT_S, timeout_s))):
        logger.warning(
            "%s is silent - and so is the rest of the telemetry, so this is the LINK, not the topic. "
            "No rate request sent.",
            topic,
        )
        return None

    _last_request_at[topic] = now
    rerequests[topic] = rerequests.get(topic, 0) + 1
    logger.warning(
        "%s has stopped arriving while the rest of the telemetry still is: the autopilot's one-shot stream "
        "request for it was lost. Re-requesting at %.1f Hz (re-request #%d for this topic).",
        topic,
        REQUEST_RATE_HZ,
        rerequests[topic],
    )
    if not await request_stream(drone, topic):
        return None
    try:
        return await _read(drone, topic, max(timeout_s - probe_s, probe_s))
    except Exception:
        return None


async def read_landed_state(drone, timeout_s: float = DEFAULT_TIMEOUT_S, link_live: bool | None = None) -> str | None:
    """``landed_state`` as an upper-case name (``ON_GROUND``...), or ``None``."""
    reading = await read_topic(drone, "landed_state", timeout_s, link_live)
    return None if reading is None else str(reading).rsplit(".", 1)[-1].upper()


async def read_in_air(drone, timeout_s: float = DEFAULT_TIMEOUT_S, link_live: bool | None = None) -> bool | None:
    """``in_air``, or ``None`` if the topic will not answer."""
    reading = await read_topic(drone, "in_air", timeout_s, link_live)
    return None if reading is None else bool(reading)


async def read_ground_topics(
    drone, timeout_s: float = DEFAULT_TIMEOUT_S, link_live: bool | None = None
) -> tuple[str | None, bool | None]:
    """``(landed_state, in_air)`` - the pair every ground decision is made on."""
    return (
        await read_landed_state(drone, timeout_s, link_live),
        await read_in_air(drone, timeout_s, link_live),
    )
