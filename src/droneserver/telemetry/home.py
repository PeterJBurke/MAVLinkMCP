"""Reading the vehicle's home position across firmwares.

ArduPilot does not stream ``HOME_POSITION`` (MAVLink message 242) unsolicited:
it emits it only when asked - either by ``MAV_CMD_GET_HOME_POSITION`` or once
per interval after a ``SET_MESSAGE_INTERVAL`` request. MAVSDK's
``telemetry.home()`` is a *passive* subscription: it waits for a message that,
on ArduPilot, never arrives on its own. The symptom is a ``get_home_position``
that times out on a vehicle whose home is perfectly well set (verified over a
wire tap: the same aircraft answers ``MAV_CMD_GET_HOME_POSITION`` immediately),
and - because the benchmark suite reads home during preflight - a whole mission
run aborting before it flies anything.

The safety layer already asked for this stream, but only on the first
*state-dependent* tool call (takeoff, arm, goto...). A read-only tool such as
``get_home_position`` never triggered it, so on a freshly started server the
very first thing the benchmark does was also the one thing guaranteed to fail.

``read_home`` fixes it at the read instead of relying on some earlier call
having warmed the link: probe the subscription briefly, and if it yields
nothing, ask the autopilot to publish the topic and read again. PX4 streams
home by default, so there the probe succeeds and no request is ever sent.
"""

import asyncio

#: Total budget for a home read, absent an explicit one.
DEFAULT_TIMEOUT_S = 10.0
#: How long to wait on the passive subscription before asking for the stream.
#: Short on purpose - if the topic is already streaming the first sample lands
#: within a second, and if it is not, waiting longer changes nothing.
PROBE_TIMEOUT_S = 2.5
#: Rate requested from the autopilot when the passive read comes up empty.
REQUEST_RATE_HZ = 1.0
#: Bound on the rate request itself, so a firmware that never answers it
#: cannot consume the caller's whole budget.
REQUEST_TIMEOUT_S = 3.0


async def _first(stream, timeout_s: float):
    async def read():
        async for item in stream:
            return item
        raise TimeoutError("stream ended without an item")

    return await asyncio.wait_for(read(), timeout=timeout_s)


async def requested_home_stream(drone) -> bool:
    """Ask the autopilot to publish the home topic. True if it accepted.

    Best effort: MAVSDK raises when the firmware denies the rate, and a denial
    is not an error worth propagating - the caller retries the read either way.
    """
    try:
        await asyncio.wait_for(drone.telemetry.set_rate_home(REQUEST_RATE_HZ), timeout=REQUEST_TIMEOUT_S)
        return True
    except Exception:
        return False


async def read_home(drone, timeout_s: float = DEFAULT_TIMEOUT_S):
    """Return the vehicle's home position, requesting the stream if needed.

    Raises ``TimeoutError`` if home cannot be read even after the request -
    which, unlike the passive read, is genuine evidence that the vehicle has
    no home rather than evidence that it was never asked.
    """
    probe_s = min(PROBE_TIMEOUT_S, timeout_s)
    try:
        return await _first(drone.telemetry.home(), probe_s)
    except Exception:
        pass
    await requested_home_stream(drone)
    return await _first(drone.telemetry.home(), timeout_s)
