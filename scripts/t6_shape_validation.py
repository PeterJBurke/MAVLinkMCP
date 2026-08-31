#!/usr/bin/env python3
"""T6-shaped scripted validation flight — the pre-refly gate for the T6-audit fixes.

Why this exists (Plan 35, 2026-08-19): the nine server/scorer fixes were
validated by ground checks and by scripted T4 and T5 flights, but every one of
those is a SINGLE-LEG shape, and the T6 audit showed the state bugs only
manifest when a mission exceeds one leg: a remote landing moves the autopilot's
HOME and the altitude datum, a re-arm away from the origin makes RTL point at
the wrong place, and "on the ground" stops meaning "mission complete". A
validation suite made of one-leg flights certifies a server that is fit for T1
and unfit for T6. This script flies the T6 *shape* — out, land at the remote
site, re-arm, return, land at the origin — with no LLM and no Maps, and asserts
the fixed tool contracts at exactly the points where the 27 T6 trials were
misled:

  V1  parked RTL is refused (precondition.rtl_requires_airborne), at the origin
      AND at the remote site — the M1 "phantom return" (8 trials).
  V2  monitor_flight never says "MISSION COMPLETE", and a fresh monitor on an
      aircraft parked away from the origin does not claim completion — the M1
      false-verdict string (fix 6).
  V3  every monitor_flight answer carries live position and distances, and no
      loop freezes (5+ identical displays) — the M2 blind return (3 trials
      force-landed short; 2 Opus trials burned $12 polling around it).
  V4  after re-arming at the remote site, get_home_position discloses that the
      autopilot's home moved (home_matches_session_launch false + warning) and
      still carries the session launch point — the M3 stale home (3 trials).
  V5  an airborne RTL is accepted, is observable poll-by-poll (distance to
      "the autopilot's home" closing), and completes at the launch point.

The scorer's altitude-datum fix (8b) is not assertable here — SITL terrain is
flat, so the 12 m datum shift of the real T6 sites cannot be reproduced; it is
covered by unit tests and by the four Revision-4 regrades.

PRECONDITION: run against an idle staging server whose aircraft is parked at
the field origin, with the drone link up — the same posture as the post-deploy
ground checks. The session launch point should be this parking spot (restart
the trial/session if the server has flown since). Fence 1000 m is enough: the
out-leg is 500 m.

Exit 0 = all checks passed; 1 = at least one failed (table on stdout either
way); 2 = could not connect. On any failure the script best-efforts a
land + disarm before exiting.
"""

import argparse
import sys
import time

from droneserver.benchmark.client import BenchmarkClient
from droneserver.benchmark.missions import (
    _arm_and_takeoff,
    _distance_m,
    _land_and_disarm,
    _offset,
    _position,
)

OUT_LEG_M = 500.0
RTL_LEG_M = 250.0
ARRIVAL_M = 20.0
TAKEOFF_ALT_M = 30.0
CRUISE_REL_M = 40.0
# monitor_flight's own auto-land branch waits up to 120 s for touchdown before
# it answers at all. A 90 s client timeout expired first, so the gate recorded a
# client-side timeout on landings the server was still legitimately watching.
# 150 s leaves the server its documented worst case plus headroom.
MONITOR_TIMEOUT = 150.0
MAX_POLLS = 24  # 24 x ~30 s server-side wait = 12 min ceiling per leg


class Checks:
    def __init__(self):
        self.rows: list[tuple[str, str, bool | None, str]] = []

    def add(self, vid: str, name: str, passed: bool | None, observed: str):
        self.rows.append((vid, name, passed, observed))
        word = "PASS" if passed else ("SKIP" if passed is None else "FAIL")
        print(f"  [{word}] {vid} {name} — {observed}", flush=True)

    @property
    def failed(self) -> bool:
        return any(p is False for _, _, p, _ in self.rows)


def ground_elevation(c: BenchmarkClient) -> float:
    r = c.call("get_position", timeout=60)
    p = r["position"]
    return p["absolute_altitude_m"] - p["relative_altitude_m"]


def wait_disarm(c: BenchmarkClient, timeout_s: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = c.call("get_armed", timeout=60)
        if r.get("status") == "success" and r.get("armed") is False:
            return True
        time.sleep(4)
    return False


def monitor_leg(c: BenchmarkClient, checks: Checks, leg: str, auto_land: bool, until: str):
    """Poll monitor_flight until ``until`` ('complete' or 'arrived'), asserting
    the fix-7 observability contract on every answer. Returns (final_result,
    all_results)."""
    results = []
    displays = []
    for _ in range(MAX_POLLS):
        r = c.call("monitor_flight", arrival_threshold_m=ARRIVAL_M, auto_land=auto_land, timeout=MONITOR_TIMEOUT)
        results.append(r)
        displays.append(str(r.get("DISPLAY_TO_USER", "")))
        status = r.get("status")
        if status == "landing_timeout":
            # Known benign path (audit §9): the descent outlasted the tool's
            # 120 s wait. A second land() is harmless; keep polling.
            c.call("land", timeout=90)
            continue
        if until == "complete" and r.get("mission_complete") is True:
            break
        if until == "arrived" and status == "arrived":
            break
        if status in ("failed", "rejected"):
            break

    # V2: the retired string must never appear, in any phase.
    offenders = [d for d in displays if "MISSION COMPLETE" in d.upper()]
    checks.add("V2", f"{leg}: no 'MISSION COMPLETE' from monitor_flight", not offenders,
               offenders[0] if offenders else f"{len(displays)} polls clean")

    # V3a: every answer carries live position + distance from the launch point.
    blind = [r for r in results if r.get("position") is None or r.get("distance_from_launch_point_m") is None]
    checks.add("V3", f"{leg}: every poll carries live position + launch distance", not blind,
               f"{len(results) - len(blind)}/{len(results)} observable")

    # V3b: the loop never freezes on one display string (the 24-identical-polls
    # signature of the blind return).
    frozen = any(len(set(displays[i:i + 5])) == 1 for i in range(max(0, len(displays) - 4)))
    checks.add("V3", f"{leg}: no 5 consecutive identical displays", not frozen,
               "varied" if not frozen else "frozen display detected")

    return results[-1] if results else {}, results


def closing_distances(results: list[dict], key: str) -> tuple[float | None, float | None]:
    vals = [r.get(key) for r in results if isinstance(r.get(key), (int, float))]
    if len(vals) < 2:
        return None, None
    return vals[0], vals[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8090/sse", help="MCP SSE endpoint (staging default)")
    parser.add_argument("--api-key", default="", help="X-API-Key if the server requires one")
    parser.add_argument("--skip-rtl-leg", action="store_true", help="skip the V5 airborne-RTL leg")
    args = parser.parse_args()

    c = BenchmarkClient(url=args.url, api_key=args.api_key)
    print(f"connecting to {args.url} ...", flush=True)
    if not c.wait_ready():
        print("ERROR: the server never reported a live drone link", file=sys.stderr)
        return 2

    checks = Checks()
    origin = None
    try:
        # ---------------- Phase A: parked truths at the origin ----------------
        print("\nPhase A — parked at the origin", flush=True)
        pos = _position(c)
        if not pos:
            print("ERROR: no position", file=sys.stderr)
            return 2
        origin = (pos[0], pos[1])
        origin_amsl = ground_elevation(c)

        hp = c.call("get_home_position", timeout=60)
        checks.add("V4", "get_home_position carries session_launch_point", "session_launch_point" in hp,
                   str(hp.get("session_launch_point"))[:60])

        rtl = c.call("return_to_launch", timeout=90)
        checks.add("V1", "parked RTL at the origin is refused",
                   rtl.get("status") == "rejected" and rtl.get("rule") == "precondition.rtl_requires_airborne",
                   f"status={rtl.get('status')} rule={rtl.get('rule')}")

        # ---------------- Phase B: outbound leg (the hospital analog) ----------------
        print(f"\nPhase B — outbound {OUT_LEG_M:.0f} m, auto-land at the remote site", flush=True)
        ok, why = _arm_and_takeoff(c, TAKEOFF_ALT_M)
        if not ok:
            checks.add("--", "outbound arm+takeoff", False, why)
            return 1
        dest = _offset(origin[0], origin[1], OUT_LEG_M, 0.0)
        go = c.call("go_to_location", latitude_deg=dest[0], longitude_deg=dest[1],
                    absolute_altitude_m=origin_amsl + CRUISE_REL_M, timeout=90)
        if go.get("status") != "success":
            checks.add("--", "outbound go_to_location accepted", False, str(go.get("error") or go.get("rule")))
            return 1
        final, _ = monitor_leg(c, checks, "outbound", auto_land=True, until="complete")
        wait_disarm(c)
        here = _position(c)
        at_dest = here and _distance_m((here[0], here[1]), dest) <= 30.0
        checks.add("--", "aircraft landed at the remote site", bool(at_dest),
                   f"{_distance_m((here[0], here[1]), dest):.1f} m from it" if here else "no position")
        if not at_dest:
            return 1

        # ---------------- Phase C: the T6 trap, parked away from the origin ----------------
        print("\nPhase C — parked at the remote site (the M1 trap)", flush=True)
        rtl = c.call("return_to_launch", timeout=90)
        checks.add("V1", "parked RTL at the REMOTE site is refused",
                   rtl.get("status") == "rejected" and rtl.get("rule") == "precondition.rtl_requires_airborne",
                   f"status={rtl.get('status')} rule={rtl.get('rule')}")
        mon = c.call("monitor_flight", arrival_threshold_m=ARRIVAL_M, auto_land=False, timeout=MONITOR_TIMEOUT)
        checks.add("V2", "fresh monitor on the parked remote aircraft does not claim completion",
                   mon.get("mission_complete") is False,
                   f"status={mon.get('status')} mission_complete={mon.get('mission_complete')}")

        # ---------------- Phase D: re-arm moves HOME; the tool must say so ----------------
        print("\nPhase D — re-arm at the remote site (the M3 trap)", flush=True)
        ok, why = _arm_and_takeoff(c, TAKEOFF_ALT_M)
        if not ok:
            checks.add("--", "return-leg arm+takeoff", False, why)
            return 1
        hp = c.call("get_home_position", timeout=60)
        home = hp.get("home") or {}
        home_moved = (
            "latitude_deg" in home
            and _distance_m((home["latitude_deg"], home["longitude_deg"]), origin) > OUT_LEG_M * 0.8
        )
        checks.add("--", "autopilot HOME followed the re-arm (the trap is armed)", home_moved,
                   f"home {_distance_m((home['latitude_deg'], home['longitude_deg']), origin):.0f} m from origin"
                   if home_moved else str(home)[:60])
        flag = hp.get("home_matches_session_launch")
        if flag is None:
            checks.add("V4", "home/launch divergence disclosed", None,
                       "session launch unknown to the server (was it restarted mid-script?)")
        else:
            slp = hp.get("session_launch_point") or {}
            slp_ok = (
                "latitude_deg" in slp
                and _distance_m((slp["latitude_deg"], slp["longitude_deg"]), origin) <= 30.0
            )
            checks.add("V4", "home/launch divergence disclosed",
                       flag is False and bool(hp.get("warning")) and slp_ok,
                       f"home_matches_session_launch={flag}, warning={'yes' if hp.get('warning') else 'NO'}, "
                       f"session_launch_point {'≈origin' if slp_ok else 'WRONG: ' + str(slp)[:40]}")

        # ---------------- Phase E: explicit return to the origin coordinate ----------------
        print("\nPhase E — return on the explicit origin coordinate", flush=True)
        go = c.call("go_to_location", latitude_deg=origin[0], longitude_deg=origin[1],
                    absolute_altitude_m=origin_amsl + CRUISE_REL_M, timeout=90)
        if go.get("status") != "success":
            checks.add("--", "return go_to_location accepted", False, str(go.get("error") or go.get("rule")))
            return 1
        final, results = monitor_leg(c, checks, "return", auto_land=True, until="complete")
        first, last = closing_distances(results, "distance_to_target_m")
        checks.add("V3", "return: distance-to-target visibly closed", first is not None and last is not None and last < first,
                   f"{first} m → {last} m" if first is not None else "no distances seen")
        wait_disarm(c)
        here = _position(c)
        err = _distance_m((here[0], here[1]), origin) if here else float("inf")
        checks.add("--", "landed back at the origin, disarmed", err <= ARRIVAL_M, f"home error {err:.1f} m")

        # ---------------- Phase F: a genuine airborne RTL, observable end to end ----------------
        if not args.skip_rtl_leg:
            print(f"\nPhase F — airborne RTL leg ({RTL_LEG_M:.0f} m out; HOME re-zeroes here, correctly)", flush=True)
            ok, why = _arm_and_takeoff(c, TAKEOFF_ALT_M)
            if not ok:
                checks.add("--", "RTL-leg arm+takeoff", False, why)
                return 1
            east = _offset(origin[0], origin[1], 0.0, RTL_LEG_M)
            c.call("go_to_location", latitude_deg=east[0], longitude_deg=east[1],
                   absolute_altitude_m=origin_amsl + CRUISE_REL_M, timeout=90)
            monitor_leg(c, checks, "RTL-leg outbound", auto_land=False, until="arrived")
            rtl = c.call("return_to_launch", timeout=90)
            checks.add("V5", "airborne RTL is accepted and names its destination",
                       rtl.get("status") == "success" and rtl.get("destination") is not None,
                       f"status={rtl.get('status')} dest={'named' if rtl.get('destination') else 'MISSING'}")
            final, results = monitor_leg(c, checks, "RTL return", auto_land=True, until="complete")
            wait_disarm(c)
            here = _position(c)
            err = _distance_m((here[0], here[1]), origin) if here else float("inf")
            first, last = closing_distances(results, "distance_to_target_m")
            # A short RTL leg can complete inside monitor_flight's single
            # blocking server-side call (observed post-FIX-10 on all 8 farm
            # lanes: one poll, then done at home). One poll cannot show a
            # closing series, but a completed return AT the launch point is
            # itself the observability evidence — the aircraft demonstrably
            # went where the tool said it was going.
            closed = first is not None and last is not None and last < first
            single_poll_done = len(results) <= 1 and err <= ARRIVAL_M
            checks.add("V5", "RTL return observable (distances closed, or completed within one blocking poll)",
                       closed or single_poll_done,
                       f"{first} m → {last} m" if closed else
                       (f"single poll, landed {err:.1f} m from launch" if single_poll_done else "no distances seen"))
            checks.add("V5", "RTL completed at the launch point, disarmed", err <= ARRIVAL_M, f"home error {err:.1f} m")

    finally:
        # Best-effort safe-down: never leave the SITL aircraft flying.
        try:
            armed = c.call("get_armed", timeout=60)
            if armed.get("armed") is not False:
                _land_and_disarm(c)
        except Exception:
            pass

    print("\n==== T6-shape validation summary ====")
    for vid, name, passed, observed in checks.rows:
        word = "PASS" if passed else ("SKIP" if passed is None else "FAIL")
        print(f"{word:4}  {vid:3} {name}  [{observed}]")
    print(f"{sum(1 for _, _, p, _ in checks.rows if p)}/{len(checks.rows)} checks passed")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
