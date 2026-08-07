"""Server-side mission state machine.

This is the answer to "LLM sessions cap out at 5-10 minutes": the LLM submits
a mission and the **server** flies and monitors it. The client can disconnect
completely and reattach later - the mission keeps running, events keep
accumulating, and auto-actions fire server-side with no LLM in the loop.

    client: start_managed_mission(...)  -> mission_id, returns immediately
    (client disconnects entirely)
    server: uploads, arms, starts, monitors, reacts to events
    client: reconnects, get_mission_status(...) -> full state + event history

Restart recovery: the mission record is checkpointed to JSON after every
event. On startup :func:`resume_if_active` reloads it and reattaches the
monitor to a mission that is still flying. See docs/tool_groups.md for what
is and is not recoverable.
"""

import asyncio
import contextlib
import time

from droneserver.logging_setup import logger
from droneserver.missions.config import MissionSettings, get_mission_settings
from droneserver.missions.state import (
    MissionEvent,
    MissionRecord,
    MissionStore,
    Phase,
    can_transition,
    new_mission_id,
)
from droneserver.safety.audit import AuditRecord, new_call_id
from droneserver.safety.config import get_safety_settings
from droneserver.safety.geofence import Geofence, check_mission, check_position, parse_polygon


def _battery_fraction(reported: float) -> float | None:
    """Normalise MavSDK's battery reading to a 0-1 fraction.

    ``remaining_percent`` is documented as a fraction but ArduCopter reports a
    PERCENTAGE through mavsdk 3.0.1 (measured: 77.0 for a 77% battery). Without
    this normalisation every battery auto-action threshold is unreachable.
    """
    if reported is None:
        return None
    value = float(reported)
    if value < 0:
        return None
    if value > 1.0:  # reported as a percentage
        value = value / 100.0
    return round(min(value, 1.0), 3)


async def _first(stream, timeout_s: float):
    async def read():
        async for item in stream:
            return item
        raise TimeoutError("stream ended")

    return await asyncio.wait_for(read(), timeout=timeout_s)


class MissionRunner:
    """Owns the current mission, its background task, and its checkpoint."""

    def __init__(self) -> None:
        self.record: MissionRecord | None = None
        self._task: asyncio.Task | None = None
        self._store: MissionStore | None = None
        self._pause_requested = False
        self._abort_requested = False
        self._resume_requested = False

    # ------------------------------------------------------------- plumbing

    def store(self, s: MissionSettings) -> MissionStore:
        from droneserver.config import get_settings

        path = s.state_path or str(get_settings().flight_log_dir / "mission_state.json")
        if self._store is None or str(self._store.path) != path:
            self._store = MissionStore(path)
        return self._store

    def _checkpoint(self, s: MissionSettings) -> None:
        if self.record is not None:
            try:
                self.store(s).save(self.record)
            except Exception:
                logger.exception("mission: failed to checkpoint state")

    def _audit(self, event: MissionEvent) -> None:
        """Mission events also land in the append-only audit log."""
        safety = get_safety_settings()
        if not safety.audit_enabled:
            return
        try:
            from droneserver.safety.middleware import LAYER

            LAYER.audit_log(safety).write(
                AuditRecord(
                    call_id=new_call_id(),
                    client_id="mission_runner",
                    authenticated=False,
                    key_fp="",
                    model=None,
                    tool=f"mission.{event.kind}",
                    tier="normal",
                    args={"mission_id": self.record.mission_id if self.record else None, **event.data},
                    verdict="event",
                    outcome_status=event.message,
                )
            )
        except Exception:
            logger.exception("mission: failed to audit event")

    def emit(self, kind: str, message: str, s: MissionSettings, **data) -> None:
        if self.record is None:
            return
        event = MissionEvent.make(kind, message, **data)
        self.record.add_event(event, s.max_events)
        logger.info(f"mission[{self.record.mission_id}] {kind}: {message}")
        self._audit(event)
        self._checkpoint(s)

    def set_phase(self, target: Phase, s: MissionSettings, reason: str = "") -> bool:
        if self.record is None:
            return False
        current = self.record.phase_enum
        if current is target:
            return True
        if not can_transition(current, target):
            logger.error(f"mission: refused illegal transition {current.value} -> {target.value}")
            return False
        self.record.phase = target.value
        if target is Phase.RUNNING and self.record.started_at is None:
            self.record.started_at = time.time()
        if target in (Phase.COMPLETED, Phase.FAILED, Phase.ABORTED):
            self.record.finished_at = time.time()
        self.emit(
            "phase_change",
            f"{current.value} -> {target.value}{f' ({reason})' if reason else ''}",
            s,
            from_phase=current.value,
            to_phase=target.value,
            reason=reason,
        )
        return True

    # ------------------------------------------------------------- fence

    def _fence(self) -> Geofence:
        safety = get_safety_settings()
        if not safety.geofence_enabled:
            return Geofence(max_altitude_m=0.0, max_radius_m=0.0)
        try:
            polygon = parse_polygon(safety.geofence_polygon)
        except ValueError:
            polygon = ()
        home = None
        with contextlib.suppress(Exception):
            from droneserver.safety.middleware import LAYER

            home = LAYER.state_tracker.state.home
        return Geofence(
            polygon=polygon,
            max_altitude_m=safety.geofence_max_altitude_m,
            max_radius_m=safety.geofence_max_radius_m,
            home=home,
        )

    # ------------------------------------------------------------- lifecycle

    def start(
        self, drone, waypoints: list, takeoff_altitude_m: float, return_to_launch: bool, source: str = "waypoints"
    ) -> MissionRecord:
        """Create the mission record and launch the background task."""
        s = get_mission_settings()
        record = MissionRecord(
            mission_id=new_mission_id(),
            waypoint_count=len(waypoints),
            takeoff_altitude_m=takeoff_altitude_m,
            return_to_launch=return_to_launch,
            source=source,
        )
        self.record = record
        self._pause_requested = self._abort_requested = self._resume_requested = False
        self.emit(
            "info",
            f"mission submitted with {len(waypoints)} waypoint(s)",
            s,
            waypoints=len(waypoints),
            takeoff_altitude_m=takeoff_altitude_m,
        )
        self._task = asyncio.get_running_loop().create_task(self._run(drone, waypoints, s))
        return record

    async def _run(self, drone, waypoints: list, s: MissionSettings) -> None:
        try:
            await self._execute(drone, waypoints, s)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("mission: unhandled error")
            if self.record is not None:
                self.record.error = str(e)
                self.set_phase(Phase.FAILED, s, reason=f"unhandled error: {e}")

    async def _execute(self, drone, waypoints: list, s: MissionSettings) -> None:
        from droneserver.mission_plans import build_raw_items

        assert self.record is not None
        record = self.record

        # ---- validate (server-side fence, before anything is uploaded) ----
        self.set_phase(Phase.VALIDATING, s)
        fence = self._fence()
        violations = check_mission(fence, waypoints)
        if violations:
            idx, first = violations[0]
            record.error = f"mission item {idx} violates the geofence: {first.detail}"
            self.emit("error", record.error, s, item=idx, rule=first.rule)
            self.set_phase(Phase.FAILED, s, reason="geofence")
            return

        # ---- upload ----
        self.set_phase(Phase.UPLOADING, s)
        items = _mission_items(build_raw_items, waypoints, record.takeoff_altitude_m, record.return_to_launch)
        try:
            await asyncio.wait_for(drone.mission_raw.upload_mission(items), timeout=60)
        except Exception as e:
            record.error = f"mission upload failed: {e}"
            self.emit("error", record.error, s)
            self.set_phase(Phase.FAILED, s, reason="upload")
            return
        record.total_items = len(items)
        self.emit("info", f"uploaded {len(items)} mission items", s, items=len(items))

        # ---- arm ----
        self.set_phase(Phase.ARMING, s)
        deadline = time.monotonic() + s.arm_timeout_s
        armed = False
        while time.monotonic() < deadline and not self._abort_requested:
            try:
                await asyncio.wait_for(drone.action.arm(), timeout=10)
                armed = True
                break
            except Exception:
                await asyncio.sleep(3)
        if self._abort_requested:
            self.set_phase(Phase.ABORTED, s, reason="aborted before arming")
            return
        if not armed:
            record.error = "could not arm within the timeout (prearm checks failing?)"
            self.emit("error", record.error, s)
            self.set_phase(Phase.FAILED, s, reason="arming")
            return
        self.emit("info", "armed", s)

        # ---- GUIDED takeoff, THEN switch to the AUTO mission ----
        # ArduCopter will not lift off in AUTO without RC throttle input: the
        # vehicle sits armed in MISSION mode at 0 m forever. Taking off in
        # GUIDED first and only then starting the mission is what a GCS does,
        # and it is firmware-agnostic (PX4 accepts it too). Measured on
        # ArduCopter 4.5.7 SITL - see docs/tool_groups.md.
        try:
            await asyncio.wait_for(drone.action.set_takeoff_altitude(record.takeoff_altitude_m), timeout=10)
            await asyncio.wait_for(drone.action.takeoff(), timeout=15)
        except Exception as e:
            record.error = f"takeoff failed: {e}"
            self.emit("error", record.error, s)
            self.set_phase(Phase.FAILED, s, reason="takeoff")
            return
        self.emit("info", f"climbing to {record.takeoff_altitude_m:.0f} m before starting the mission", s)
        climb_deadline = time.monotonic() + 90
        reached = 0.0
        while time.monotonic() < climb_deadline and not self._abort_requested:
            await asyncio.sleep(2)
            try:
                position = await _first(drone.telemetry.position(), 5.0)
                reached = position.relative_altitude_m
            except Exception:
                continue
            if reached >= record.takeoff_altitude_m - 1.5:
                break
        self.emit("info", f"reached {reached:.1f} m", s, altitude_m=round(reached, 1))
        if self._abort_requested:
            await self._do_action("land", drone, s, reason="operator abort")
            self.set_phase(Phase.ABORTED, s, reason="aborted during takeoff")
            return

        # ---- start ----
        started = False
        for _ in range(3):
            try:
                await asyncio.wait_for(drone.mission_raw.start_mission(), timeout=20)
                started = True
                break
            except Exception as e:
                logger.warning(f"mission: start_mission retry after {e}")
                await asyncio.sleep(3)
        if not started:
            record.error = "mission start was refused by the autopilot"
            self.emit("error", record.error, s)
            self.set_phase(Phase.FAILED, s, reason="start")
            return
        self.set_phase(Phase.RUNNING, s)

        await self.monitor(drone, s)

    # ------------------------------------------------------------- monitor

    async def monitor(self, drone, s: MissionSettings) -> None:
        """Poll the vehicle until the mission ends. Safe to call on resume."""
        assert self.record is not None
        record = self.record
        last_item = record.current_item
        last_mode = record.last_flight_mode
        battery_marks: set[int] = set()
        link_lost_at: float | None = None
        # Completion on ArduPilot cannot rely on is_mission_finished() or on
        # mission_progress (both advisory - measured). The definitive signal is
        # "we were airborne and now the vehicle is disarmed on the ground".
        was_airborne = False

        while record.active:
            await asyncio.sleep(s.poll_interval_s)

            if self._abort_requested:
                await self._do_action("land", drone, s, reason="operator abort")
                self.set_phase(Phase.ABORTED, s, reason="operator abort")
                return
            if self._pause_requested and record.phase_enum is Phase.RUNNING:
                self._pause_requested = False
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(drone.mission_raw.pause_mission(), timeout=10)
                self.set_phase(Phase.PAUSED, s, reason="operator pause")
            if self._resume_requested and record.phase_enum is Phase.PAUSED:
                self._resume_requested = False
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(drone.mission_raw.start_mission(), timeout=10)
                self.set_phase(Phase.RUNNING, s, reason="operator resume")

            telemetry_ok = await self._sample(drone, record, s)

            # ---- link loss ----
            if not telemetry_ok:
                link_lost_at = link_lost_at or time.monotonic()
                if (
                    s.auto_actions_enabled
                    and s.link_loss_action != "none"
                    and (time.monotonic() - link_lost_at) > s.link_loss_grace_s
                ):
                    await self._auto_action(s.link_loss_action, drone, s, "link loss")
                    link_lost_at = None
                continue
            link_lost_at = None

            # ---- waypoint progress ----
            if record.current_item != last_item:
                self.emit(
                    "waypoint",
                    f"reached item {record.current_item}/{record.total_items}",
                    s,
                    current=record.current_item,
                    total=record.total_items,
                )
                last_item = record.current_item

            # ---- flight-mode change ----
            if record.last_flight_mode != last_mode:
                self.emit("mode", f"flight mode -> {record.last_flight_mode}", s, mode=record.last_flight_mode)
                last_mode = record.last_flight_mode

            # ---- battery thresholds ----
            fraction = (record.last_battery or {}).get("remaining_fraction")
            if fraction is not None and fraction > 0:
                for mark in (50, 30, 25, 20, 15, 10):
                    if fraction * 100 <= mark and mark not in battery_marks:
                        battery_marks.add(mark)
                        self.emit("battery", f"battery at {fraction * 100:.0f}%", s, percent=round(fraction * 100, 1))
                if s.auto_actions_enabled:
                    if fraction <= s.critical_battery_threshold:
                        await self._auto_action("land", drone, s, f"critical battery {fraction * 100:.0f}%")
                    elif fraction <= s.low_battery_threshold and s.low_battery_action != "none":
                        await self._auto_action(s.low_battery_action, drone, s, f"low battery {fraction * 100:.0f}%")

            # ---- geofence breach ----
            if s.auto_actions_enabled and s.geofence_breach_action != "none" and record.last_position:
                fence = self._fence()
                violation = check_position(
                    fence,
                    record.last_position.get("latitude_deg"),
                    record.last_position.get("longitude_deg"),
                    record.last_position.get("relative_altitude_m"),
                )
                if violation is not None:
                    await self._auto_action(s.geofence_breach_action, drone, s, f"geofence breach: {violation.detail}")

            # ---- completion ----
            altitude = (record.last_position or {}).get("relative_altitude_m") or 0.0
            if record.last_armed and altitude > 2.0:
                was_airborne = True

            if was_airborne and not record.last_armed and altitude <= 2.0:
                self.set_phase(Phase.COMPLETED, s, reason="landed and disarmed")
                return

            if was_airborne and record.phase_enum is Phase.RUNNING and await self._mission_items_done(drone):
                self.set_phase(Phase.LANDING, s, reason="mission items complete, descending")

        return

    async def _sample(self, drone, record: MissionRecord, s: MissionSettings) -> bool:
        """Read one telemetry sample. False when the link looks down."""
        ok = False
        try:
            position = await _first(drone.telemetry.position(), 5.0)
            record.last_position = {
                "latitude_deg": round(position.latitude_deg, 7),
                "longitude_deg": round(position.longitude_deg, 7),
                "relative_altitude_m": round(position.relative_altitude_m, 1),
                "absolute_altitude_m": round(position.absolute_altitude_m, 1),
            }
            ok = True
        except Exception:
            pass
        with contextlib.suppress(Exception):
            battery = await _first(drone.telemetry.battery(), 5.0)
            record.last_battery = {
                "voltage_v": round(battery.voltage_v, 2),
                "remaining_fraction": _battery_fraction(battery.remaining_percent),
            }
            ok = True
        with contextlib.suppress(Exception):
            record.last_flight_mode = str(await _first(drone.telemetry.flight_mode(), 5.0))
        with contextlib.suppress(Exception):
            record.last_armed = bool(await _first(drone.telemetry.armed(), 5.0))
        with contextlib.suppress(Exception):
            progress = await _first(drone.mission_raw.mission_progress(), 2.0)
            if progress.total > 0:
                record.current_item = progress.current
                record.total_items = max(record.total_items, progress.total)
        self._checkpoint(s)
        return ok

    async def _mission_items_done(self, drone) -> bool:
        """Advisory on ArduPilot - used only to move RUNNING -> LANDING, never
        to declare the mission complete."""
        with contextlib.suppress(Exception):
            return bool(await asyncio.wait_for(drone.mission_raw.is_mission_finished(), timeout=5))
        return False

    # ------------------------------------------------------------- actions

    async def _auto_action(self, action: str, drone, s: MissionSettings, reason: str) -> None:
        assert self.record is not None
        if any(a.get("action") == action and a.get("reason") == reason for a in self.record.auto_actions_fired):
            return  # already fired for this exact reason
        self.record.auto_actions_fired.append({"action": action, "reason": reason, "ts": time.time()})
        self.emit("auto_action", f"auto-action '{action}' fired: {reason}", s, action=action, reason=reason)
        await self._do_action(action, drone, s, reason)

    async def _do_action(self, action: str, drone, s: MissionSettings, reason: str) -> None:
        try:
            if action == "rtl":
                await asyncio.wait_for(drone.action.return_to_launch(), timeout=15)
                self.set_phase(Phase.RETURNING, s, reason=reason)
            elif action == "land":
                await asyncio.wait_for(drone.action.land(), timeout=15)
                self.set_phase(Phase.LANDING, s, reason=reason)
            elif action == "hold":
                await asyncio.wait_for(drone.mission_raw.pause_mission(), timeout=15)
                self.set_phase(Phase.PAUSED, s, reason=reason)
        except Exception as e:
            self.emit("error", f"auto-action '{action}' failed: {e}", s, action=action)

    # ------------------------------------------------------------- control

    def request_pause(self) -> None:
        self._pause_requested = True

    def request_resume(self) -> None:
        self._resume_requested = True

    def request_abort(self) -> None:
        self._abort_requested = True

    async def shutdown(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    # ------------------------------------------------------------- resume

    def resume_if_active(self, drone) -> MissionRecord | None:
        """Reload the checkpoint and reattach monitoring if a mission is live.

        Called at server startup. The vehicle keeps flying its uploaded mission
        regardless of whether this server is running - what we recover is the
        *monitoring and auto-actions*, not the flight itself.
        """
        s = get_mission_settings()
        record = self.store(s).load()
        if record is None or not record.active:
            self.record = record
            return record
        record.resumed_after_restart = True
        self.record = record
        self.emit("info", f"server restarted - resumed monitoring in phase '{record.phase}'", s, phase=record.phase)
        self._task = asyncio.get_running_loop().create_task(self._resume_monitor(drone, s))
        return record

    async def _resume_monitor(self, drone, s: MissionSettings) -> None:
        try:
            await self.monitor(drone, s)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("mission: resumed monitor failed")
            if self.record is not None:
                self.record.error = str(e)
                self.set_phase(Phase.FAILED, s, reason=f"resume error: {e}")


def _mission_items(build_raw_items, waypoints: list, takeoff_altitude_m: float, rtl: bool) -> list:
    """Build raw mission items in the layout ArduPilot expects.

    seq 0 must be a HOME placeholder with ``current=0``; the first real item
    (the takeoff) carries ``current=1``. This is the layout QGroundControl
    produces, and MavSDK/ArduPilot reject or refuse to start anything else
    (measured: takeoff-at-seq-0 uploads but ``start_mission`` returns UNKNOWN).
    """
    MAV_CMD_NAV_WAYPOINT = 16
    MAV_CMD_NAV_TAKEOFF = 22
    MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
    FRAME_GLOBAL_REL_ALT = 3
    FRAME_GLOBAL_INT = 5
    FRAME_MISSION = 2

    first = waypoints[0]
    first_lat = float(first.get("latitude_deg", first.get("lat", 0.0)))
    first_lon = float(first.get("longitude_deg", first.get("lon", 0.0)))

    dicts = [
        # seq 0: HOME placeholder (content is ignored by the autopilot, which
        # substitutes its own home; the slot must exist).
        {
            "seq": 0,
            "frame": FRAME_GLOBAL_INT,
            "command": MAV_CMD_NAV_WAYPOINT,
            "current": 0,
            "autocontinue": 1,
            "x": int(round(first_lat * 1e7)),
            "y": int(round(first_lon * 1e7)),
            "z": 0.0,
        },
        # seq 1: takeoff - the first real item, so it carries current=1.
        {
            "seq": 1,
            "frame": FRAME_GLOBAL_REL_ALT,
            "command": MAV_CMD_NAV_TAKEOFF,
            "current": 1,
            "autocontinue": 1,
            "x": int(round(first_lat * 1e7)),
            "y": int(round(first_lon * 1e7)),
            "z": float(takeoff_altitude_m),
        },
    ]
    for wp in waypoints:
        lat = float(wp.get("latitude_deg", wp.get("lat", 0.0)))
        lon = float(wp.get("longitude_deg", wp.get("lon", 0.0)))
        alt = float(wp.get("altitude_m", wp.get("relative_altitude_m", wp.get("alt", takeoff_altitude_m))))
        hold = float(wp.get("hold_s", 0.0))
        dicts.append(
            {
                "seq": len(dicts),
                "frame": FRAME_GLOBAL_REL_ALT,
                "command": MAV_CMD_NAV_WAYPOINT,
                "current": 0,
                "autocontinue": 1,
                "param1": hold,
                "x": int(round(lat * 1e7)),
                "y": int(round(lon * 1e7)),
                "z": alt,
            }
        )
    if rtl:
        dicts.append(
            {
                "seq": len(dicts),
                "frame": FRAME_MISSION,
                "command": MAV_CMD_NAV_RETURN_TO_LAUNCH,
                "current": 0,
                "autocontinue": 1,
                "x": 0,
                "y": 0,
                "z": 0.0,
            }
        )
    return build_raw_items(dicts, 0, force_first_current=False)


#: Process-wide runner (one server, one drone), like the global connector.
RUNNER = MissionRunner()
