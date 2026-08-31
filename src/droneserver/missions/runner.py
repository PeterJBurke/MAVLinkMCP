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
from droneserver.telemetry import ground_stream
from droneserver.telemetry.ground import SETTLED_RATE_M_S, ground_evidence, height_above_launch_m

#: A height at or below which the aircraft counts as down, used ONLY when the
#: autopilot itself will not say (see :func:`MissionRunner._ground_state`). It is
#: measured against the mission's own launch elevation, never against the
#: autopilot's relative datum, which moves to wherever the aircraft last armed.
GROUND_HEIGHT_M = 2.0


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
        # One-shot: True once we have commanded the post-mission descent, so we
        # do not re-issue RTL on every poll after the mission items finish.
        self._descent_commanded = False

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
        self._descent_commanded = False
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
        # Make sure home is known BEFORE building the fence. A radius fence
        # with no home silently permits every waypoint - the same gap the tool
        # path fixed as geofence.home_unknown, which was still open here, in
        # the component that flies without anyone watching.
        try:
            from droneserver.safety.middleware import LAYER

            await LAYER.state_tracker.refresh(drone, 0.0)
        except Exception:
            logger.exception("mission: could not refresh state before fence validation")

        fence = self._fence()
        if fence.max_radius_m > 0 and fence.home is None:
            record.error = (
                "a radius geofence is configured but the drone's home position is not known, "
                "so the mission cannot be checked against it"
            )
            self.emit("error", record.error, s, rule="geofence.home_unknown")
            self.set_phase(Phase.FAILED, s, reason="geofence.home_unknown")
            return

        violations = check_mission(fence, waypoints)
        if violations:
            idx, first = violations[0]
            record.error = f"mission item {idx} violates the geofence: {first.detail}"
            self.emit("error", record.error, s, item=idx, rule=first.rule)
            self.set_phase(Phase.FAILED, s, reason="geofence")
            return

        # ---- the datum this mission measures heights against ----
        # Read while the aircraft is still parked, so it is the elevation of the
        # ground it is standing on. The autopilot's own relative-altitude datum
        # cannot serve: it is re-zeroed at every arm, so a mission flown after
        # one that armed somewhere else inherits that offset in every reading.
        # Best effort - a mission with no launch elevation falls back to the
        # relative reading exactly as before.
        await self._record_launch_datum(drone)

        # ---- upload ----
        self.set_phase(Phase.UPLOADING, s)
        items = _mission_items(build_raw_items, waypoints, record.takeoff_altitude_m, record.return_to_launch)
        if not await self._upload(drone, items, s):
            return
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
        # relative_altitude_m is the RIGHT datum for this wait, and the only
        # right one: set_takeoff_altitude/takeoff express the target relative to
        # the point the aircraft just armed at, which is exactly the datum the
        # autopilot re-zeroed on that arm. Command and measurement share a datum
        # here; measuring the climb against the mission's launch elevation would
        # compare it against ground the aircraft is not standing on. Deliberate.
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
        # Baseline the progress counter BEFORE starting: "current item is not
        # zero" is not progress. PX4 reports current=1 (the takeoff item) from
        # the moment a mission is uploaded, so progress means moving past THIS.
        await self._baseline(drone, record)

        if not await self._start_mission(drone, s):
            record.error = "mission start was refused by the autopilot"
            self.emit("error", record.error, s)
            await self._descend_and_fail(drone, s, "start")
            return
        self.set_phase(Phase.RUNNING, s)

        # ---- confirm the autopilot ACTUALLY entered mission execution ----
        # start_mission() returning success is NOT evidence that the mission is
        # running. Measured on PX4 v1.16.2 (2026-08-19, llmuavpx4): PX4 answers
        # the DO_SET_MODE with COMMAND_ACK result=ACCEPTED and *then* refuses the
        # transition with STATUSTEXT severity CRITICAL "Switching to Mission is
        # currently not available"; MavSDK reads the first ACK and reports
        # success. The vehicle stays in HOLD over its launch point.
        if not await self._confirm_running(drone, s):
            self.emit(
                "info",
                "the autopilot did not enter mission execution - retrying without the "
                "seq-0 home placeholder (PX4 refuses that layout in flight)",
                s,
                **record.progress_evidence(),
            )
            retry = _mission_items(
                build_raw_items,
                waypoints,
                record.takeoff_altitude_m,
                record.return_to_launch,
                home_placeholder=False,
            )
            confirmed = False
            if await self._upload(drone, retry, s):
                await self._baseline(drone, record)
                if await self._start_mission(drone, s):
                    confirmed = await self._confirm_running(drone, s)
            if not confirmed:
                record.error = (
                    "the autopilot accepted the start command but never entered mission "
                    "execution: the flight mode never became MISSION and no mission item "
                    "was reached. The mission did NOT fly."
                )
                self.emit("error", record.error, s, rule="mission.start_unconfirmed", **record.progress_evidence())
                await self._descend_and_fail(drone, s, "start_unconfirmed")
                return

        await self.monitor(drone, s)

    # ------------------------------------------------------------- ground truth

    async def _record_launch_datum(self, drone) -> None:
        """Stamp the elevation of the point this mission starts from. Once.

        Never raises and never blocks the mission: a launch elevation is what
        makes the height fallback datum-free, and its absence only returns that
        fallback to the autopilot's relative reading.
        """
        if self.record is None or self.record.launch_amsl_m is not None:
            return
        with contextlib.suppress(Exception):
            position = await _first(drone.telemetry.position(), 5.0)
            self.record.launch_amsl_m = round(float(position.absolute_altitude_m), 2)

    def _height_above_launch(self, record: MissionRecord) -> float | None:
        """How high the aircraft is above where THIS mission started."""
        position = record.last_position or {}
        return height_above_launch_m(
            record.launch_amsl_m,
            position.get("absolute_altitude_m"),
            position.get("relative_altitude_m"),
        )

    def _ground_state(self, record: MissionRecord) -> bool | None:
        """``True`` on the ground, ``False`` in the air, ``None`` when nothing says.

        The autopilot's own landed evidence first, because no arming anywhere
        moves it. A height - measured against this mission's launch elevation,
        not the autopilot's movable datum - is consulted only when the firmware
        did not answer at all.

        The third answer is the point of this function. "Unknown" must not mean
        landed to the completion check (which would report a flight that is
        still in the air as finished) and must not mean safe to
        :meth:`_descend_and_fail` (which would walk away from an aircraft
        loitering armed).

        :func:`droneserver.telemetry.ground.settled_on_ground` is not called
        directly here because it requires a ``landed_state``, and this runner
        must keep working on a firmware that only publishes ``in_air``. Its
        vertical-rate veto is applied all the same: an autopilot claiming the
        ground while still falling is describing a landing in progress.
        """
        grounded = ground_evidence(record.last_landed_state, record.last_in_air)
        rate = record.last_vertical_speed_m_s
        if grounded is True and rate is not None and abs(rate) > SETTLED_RATE_M_S:
            return False
        if grounded is not None:
            return grounded
        height = self._height_above_launch(record)
        if height is None:
            return None
        return height <= GROUND_HEIGHT_M

    # ------------------------------------------------------------- start/upload

    async def _upload(self, drone, items: list, s: MissionSettings) -> bool:
        assert self.record is not None
        try:
            await asyncio.wait_for(drone.mission_raw.upload_mission(items), timeout=60)
        except Exception as e:
            self.record.error = f"mission upload failed: {e}"
            self.emit("error", self.record.error, s)
            if self.record.phase_enum is Phase.UPLOADING:
                self.set_phase(Phase.FAILED, s, reason="upload")
            return False
        self.record.total_items = len(items)
        return True

    async def _start_mission(self, drone, s: MissionSettings) -> bool:
        for _ in range(3):
            try:
                await asyncio.wait_for(drone.mission_raw.start_mission(), timeout=20)
                return True
            except Exception as e:
                logger.warning(f"mission: start_mission retry after {e}")
                await asyncio.sleep(3)
        return False

    async def _baseline(self, drone, record: MissionRecord) -> None:
        """Freeze the "before" side of the progress evidence.

        Both halves need a baseline taken at the moment of the start, not a
        constant: PX4 reports ``mission_progress.current = 1`` (the takeoff
        item) from the moment the mission is uploaded, so "current is not zero"
        proves nothing, and the aircraft is already at altitude over the launch
        point, so "distance from home" is not the right origin either.
        """
        record.baseline_item = 0
        with contextlib.suppress(Exception):
            progress = await _first(drone.mission_raw.mission_progress(), 2.0)
            if progress.total > 0:
                record.baseline_item = int(progress.current)
        record.items_reached = record.baseline_item
        record.max_distance_from_start_m = 0.0
        record.mission_mode_confirmed = False
        record.start_position = None
        with contextlib.suppress(Exception):
            position = await _first(drone.telemetry.position(), 5.0)
            record.start_position = {
                "latitude_deg": round(position.latitude_deg, 7),
                "longitude_deg": round(position.longitude_deg, 7),
                # Descriptive only: "how far has it flown" is computed from the
                # two coordinates above, and nothing compares this altitude to
                # anything. Recorded as the autopilot reported it, with the
                # absolute reading beside it so a reader can re-derive a height
                # against launch_amsl_m if the datum turns out to have moved.
                "relative_altitude_m": round(position.relative_altitude_m, 1),
                "absolute_altitude_m": round(position.absolute_altitude_m, 1),
            }

    async def _confirm_running(self, drone, s: MissionSettings) -> bool:
        """Wait for positive evidence that the mission is really executing.

        Either signal is proof, and both are firmware-independent: the vehicle
        reports MISSION flight mode (ArduCopter AUTO and PX4 AUTO.MISSION both
        map to it in MavSDK - measured on ArduCopter 4.5.7 and PX4 v1.16.2), or
        the mission demonstrably progressed. Absence of both is treated as a
        refused start, never as a running mission.
        """
        assert self.record is not None
        record = self.record
        deadline = time.monotonic() + s.start_confirm_timeout_s
        while time.monotonic() < deadline and not self._abort_requested:
            await self._sample(drone, record, s)
            if record.mission_mode_confirmed or record.progressed(s.progress_distance_m):
                self.emit("info", "mission execution confirmed", s, **record.progress_evidence())
                return True
            await asyncio.sleep(s.poll_interval_s)
        return False

    async def _descend_and_fail(self, drone, s: MissionSettings, reason: str) -> None:
        """Bring the aircraft down, then FAIL. Never leave it loitering armed.

        The failure paths that reach here all happen after the GUIDED takeoff,
        so the vehicle is at altitude. A stale or missing position must not be
        read as "on the ground and therefore fine" - re-read it first.

        The test is deliberately inverted: descend UNLESS there is positive
        evidence the aircraft is already down. It used to be
        ``relative_altitude_m > 2.0``, which fails open in the direction that
        matters - a mission flown after one that armed on higher ground reads a
        negative offset, an aircraft genuinely 4 m up reads under 2, and the
        descent that should have brought it home is skipped. (The same offset
        with the other sign spends a descent command on a parked aircraft.)
        Unknown is now "not on the ground", so the aircraft comes down.
        """
        assert self.record is not None
        if self.record.last_position is None:
            await self._sample(drone, self.record, s)
        if self._ground_state(self.record) is not True:
            await self._do_action(s.mission_complete_action, drone, s, reason=reason)
        self.set_phase(Phase.FAILED, s, reason=reason)

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
        airborne_since: float | None = None
        #: Set when the no-progress watchdog brought the aircraft down: the
        #: flight then ends in FAILED, never in COMPLETED.
        stalled = False

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
                    await self._auto_action(s.link_loss_action, drone, s, "link loss", "link_loss")
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
                        await self._auto_action(
                            "land", drone, s, f"critical battery {fraction * 100:.0f}%", "critical_battery"
                        )
                    elif fraction <= s.low_battery_threshold and s.low_battery_action != "none":
                        await self._auto_action(
                            s.low_battery_action,
                            drone,
                            s,
                            f"low battery {fraction * 100:.0f}%",
                            "low_battery",
                        )

            # ---- geofence breach ----
            if s.auto_actions_enabled and s.geofence_breach_action != "none" and record.last_position:
                fence = self._fence()
                # A ceiling is a height above the ground the aircraft started
                # from, which is what _height_above_launch measures. Handing the
                # fence the autopilot's relative reading instead let a moved
                # datum both hide a real breach and invent one: the same +4.1 m
                # offset is 4 m of ceiling silently spent, or 4 m of margin that
                # was never there. Falls back to the relative reading when this
                # mission has no launch elevation.
                violation = check_position(
                    fence,
                    record.last_position.get("latitude_deg"),
                    record.last_position.get("longitude_deg"),
                    self._height_above_launch(record),
                )
                if violation is not None:
                    await self._auto_action(
                        s.geofence_breach_action,
                        drone,
                        s,
                        f"geofence breach: {violation.detail}",
                        "geofence_breach",
                    )

            # ---- completion ----
            # The definitive completion signal is firmware-agnostic: we were
            # airborne and the vehicle is now disarmed on the ground. It says
            # the FLIGHT is over; it does not say the MISSION was flown, so it
            # only reads as COMPLETED with the progress evidence behind it.
            #
            # Both halves used to read ``relative_altitude_m`` against 2 m. That
            # datum is re-zeroed wherever the aircraft last ARMED, so a mission
            # flown after one that armed elsewhere carries a constant offset in
            # it (+4.1 m measured across 8 SITL lanes, 2026-08-19): the airborne
            # latch fires on a parked aircraft, and the completion never fires
            # at all because the height never comes back under the threshold.
            # Both now ask the autopilot, and fall back to a height above THIS
            # mission's launch elevation only when it will not answer.
            grounded = self._ground_state(record)
            if record.last_armed and grounded is False:
                was_airborne = True
                airborne_since = airborne_since or time.monotonic()

            if was_airborne and not record.last_armed and grounded is True:
                if stalled or not record.mission_mode_confirmed or not record.progressed(s.progress_distance_m):
                    record.error = record.error or (
                        "the aircraft landed without flying the mission: no mission item "
                        "past the one it started on was reached and it never left the "
                        "point it started from"
                    )
                    self.emit("error", record.error, s, rule="mission.no_progress", **record.progress_evidence())
                    self.set_phase(Phase.FAILED, s, reason="landed without flying the mission")
                else:
                    self.emit("info", "mission flown", s, **record.progress_evidence())
                    self.set_phase(Phase.COMPLETED, s, reason="landed and disarmed")
                return

            # Getting *to* that signal differs by firmware. ArduPilot missions
            # self-terminate with a land+disarm, so RUNNING -> disarmed happens
            # on its own. PX4 instead loiters (HOLD) armed at the final waypoint
            # forever, so the disarm never arrives and the mission would time out
            # in RUNNING. When the mission items are finished we therefore command
            # the descent ourselves - once - so both firmwares converge on the
            # "landed and disarmed" completion above.
            if (
                was_airborne
                and record.phase_enum is Phase.RUNNING
                and not self._descent_commanded
                and self._mission_items_done(record, s)
            ):
                self._descent_commanded = True
                self.emit(
                    "info", "mission items complete - commanding return-to-launch", s, **record.progress_evidence()
                )
                await self._do_action(s.mission_complete_action, drone, s, reason="mission items complete")

            # ---- no-progress watchdog ----
            # Fail-closed only means something if it is bounded: a mission that
            # is never going to progress must come down rather than loiter armed
            # until someone notices. This fires ONLY on a mission that has not
            # progressed at all since it started, so a long leg or a long
            # waypoint hold - both of which have progressed already - is safe.
            if (
                s.no_progress_timeout_s > 0
                and was_airborne
                and not stalled
                and not self._descent_commanded
                and record.phase_enum is Phase.RUNNING
                and not record.progressed(s.progress_distance_m)
                and airborne_since is not None
                and (time.monotonic() - airborne_since) > s.no_progress_timeout_s
            ):
                stalled = True
                self._descent_commanded = True
                record.error = (
                    f"the mission made no progress in {s.no_progress_timeout_s:.0f} s: no mission "
                    "item past the one it started on was reached and the aircraft did not leave "
                    "the point it started from"
                )
                self.emit("error", record.error, s, rule="mission.stalled", **record.progress_evidence())
                await self._do_action(s.mission_complete_action, drone, s, reason="mission made no progress")

        return

    async def _sample(self, drone, record: MissionRecord, s: MissionSettings) -> bool:
        """Read one telemetry sample. False when the link looks down."""
        ok = False
        try:
            position = await _first(drone.telemetry.position(), 5.0)
            record.last_position = {
                "latitude_deg": round(position.latitude_deg, 7),
                "longitude_deg": round(position.longitude_deg, 7),
                # The autopilot's own reading, kept for continuity: measured
                # from wherever it last armed, so it can be metres off.
                "relative_altitude_m": round(position.relative_altitude_m, 1),
                "absolute_altitude_m": round(position.absolute_altitude_m, 1),
            }
            # The same height against a datum that cannot move under the
            # aircraft. Reported so a reader can see the two disagree.
            height = height_above_launch_m(
                record.launch_amsl_m,
                record.last_position["absolute_altitude_m"],
                record.last_position["relative_altitude_m"],
            )
            record.last_position["height_above_launch_m"] = None if height is None else round(height, 1)
            ok = True
        except Exception:
            pass
        # The autopilot's own landed evidence. Cleared first, so a poll the
        # firmware did not answer reports "unknown" instead of leaving the
        # previous poll's word standing - a stale "ON_GROUND" would be exactly
        # the wrong thing to complete a mission on. Bounded tighter than the
        # readings above because these are extras on some firmwares: a topic
        # that is never published costs a short wait, not the poll.
        record.last_landed_state = None
        record.last_in_air = None
        record.last_vertical_speed_m_s = None
        # Through the re-requesting reader (FIX 15): ArduPilot publishes
        # landed_state only on request, and a lost request would otherwise
        # retire this mission's completion evidence for the rest of the flight -
        # the exact evidence FIX 11 moved this runner TO.
        # ``ok`` is this poll's own evidence that the link is alive, which is
        # what lets a silent ground topic be re-requested without a re-requester
        # ever having to mistake a dead link for a lost message.
        with contextlib.suppress(Exception):
            record.last_landed_state = await ground_stream.read_landed_state(drone, 2.0, link_live=ok)
        with contextlib.suppress(Exception):
            record.last_in_air = await ground_stream.read_in_air(drone, 2.0, link_live=ok)
        with contextlib.suppress(Exception):
            velocity = await _first(drone.telemetry.velocity_ned(), 2.0)
            # NED: down is positive, so a climb is a negative down rate.
            record.last_vertical_speed_m_s = round(-float(velocity.down_m_s), 2)
        with contextlib.suppress(Exception):
            battery = await _first(drone.telemetry.battery(), 5.0)
            record.last_battery = {
                "voltage_v": round(battery.voltage_v, 2),
                "remaining_fraction": _battery_fraction(battery.remaining_percent),
            }
            ok = True
        with contextlib.suppress(Exception):
            record.last_flight_mode = str(await _first(drone.telemetry.flight_mode(), 5.0))
        # MavSDK maps ArduCopter AUTO and PX4 AUTO.MISSION to the same
        # FlightMode.MISSION, so this one latch is the firmware-independent
        # proof that the autopilot really took the mission (measured on
        # ArduCopter 4.5.7 and PX4 v1.16.2).
        if (record.last_flight_mode or "").upper() == "MISSION":
            record.mission_mode_confirmed = True
        with contextlib.suppress(Exception):
            record.last_armed = bool(await _first(drone.telemetry.armed(), 5.0))
        with contextlib.suppress(Exception):
            progress = await _first(drone.mission_raw.mission_progress(), 2.0)
            if progress.total > 0:
                record.current_item = progress.current
                record.items_reached = max(record.items_reached, int(progress.current))
                record.total_items = max(record.total_items, progress.total)
        if record.start_position and record.last_position:
            with contextlib.suppress(Exception):
                from droneserver.geo import haversine_distance

                moved = haversine_distance(
                    record.start_position["latitude_deg"],
                    record.start_position["longitude_deg"],
                    record.last_position["latitude_deg"],
                    record.last_position["longitude_deg"],
                )
                record.max_distance_from_start_m = max(record.max_distance_from_start_m, moved)
        self._checkpoint(s)
        return ok

    def _mission_items_done(self, record: MissionRecord, s: MissionSettings) -> bool:
        """True only when the vehicle has DEMONSTRABLY flown the mission items.

        This gates the post-mission descent, and the descent is what the client
        is told the mission finished by, so it must never be derivable from a
        signal that is already true at item 0. It was: on 2026-08-12/13 PX4
        refused the mission mode switch and loitered in ``HOLD`` over the launch
        point, the old third signal read ``HOLD`` as "PX4 finished its mission",
        and 33 of PX4's 44 T4 trials reported "mission items complete" about
        seven seconds after start at 0% progress with the aircraft still on its
        launch point. The models were told the mission had finished, and most of
        them believed it.

        So the gate is evidence, and the order matters:

        1. the autopilot was actually SEEN executing the mission
           (``mission_mode_confirmed``) - without that nothing else counts;
        2. the mission actually PROGRESSED - an item past the one it started on
           was reached, or the vehicle left the point it started from;
        3. only then does "every item is accounted for" (``items_reached >=
           total_items``, which ArduCopter reaches - measured: ``reached item
           6/6``) or "left mission execution for HOLD" (PX4's "Mission finished,
           loitering") mean the items are done.

        ``mission_raw.is_mission_finished()`` is deliberately not consulted: it
        does not exist on the MissionRaw plugin in MavSDK 3.0.1 (only on the
        ``mission`` plugin), so the old call raised AttributeError inside a
        suppress() on every poll and was never a signal on either firmware.
        """
        if not record.mission_mode_confirmed:
            return False
        if not record.progressed(s.progress_distance_m):
            return False
        if record.total_items > 0 and record.items_reached >= record.total_items:
            return True
        return (record.last_flight_mode or "").upper() == "HOLD"

    # ------------------------------------------------------------- actions

    async def _auto_action(self, action: str, drone, s: MissionSettings, reason: str, trigger: str) -> None:
        """Fire an auto-action once per TRIGGER.

        The dedup key is the trigger id ("low_battery"), never the formatted
        reason: the reason embeds the live battery percentage, so keying on it
        re-fired RTL on every poll (measured: 17 RTL commands in one flight).
        """
        assert self.record is not None
        if any(a.get("trigger") == trigger for a in self.record.auto_actions_fired):
            return  # already fired for this condition
        self.record.auto_actions_fired.append(
            {"action": action, "trigger": trigger, "reason": reason, "ts": time.time()}
        )
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


def _mission_items(
    build_raw_items,
    waypoints: list,
    takeoff_altitude_m: float,
    rtl: bool,
    home_placeholder: bool = True,
) -> list:
    """Build raw mission items in the layout ArduPilot expects.

    seq 0 must be a HOME placeholder with ``current=0``; the first real item
    (the takeoff) carries ``current=1``. This is the layout QGroundControl
    produces, and MavSDK/ArduPilot reject or refuse to start anything else
    (measured: takeoff-at-seq-0 uploads but ``start_mission`` returns UNKNOWN).

    ``home_placeholder=False`` drops seq 0 and makes the takeoff the first item.
    That is the fallback layout for a firmware that does NOT reserve seq 0 for
    home. PX4 does not: it reads the placeholder as a real waypoint, and while
    the vehicle is in the air it then refuses the mission entirely -
    "Switching to Mission is currently not available", COMMAND_ACK notwithstanding.
    Measured on PX4 v1.16.2, llmuavpx4, 2026-08-19, four flights::

        on the ground, placeholder present                 -> MISSION accepted
        airborne, placeholder z=0 AMSL                     -> DENIED
        airborne, placeholder z=home AMSL                  -> DENIED
        airborne, placeholder present, current set past it -> DENIED
        airborne, NO placeholder, takeoff at seq 0         -> MISSION accepted
        airborne, NO placeholder, no takeoff item          -> MISSION accepted

    so it is the placeholder's existence in flight, not its altitude and not the
    takeoff item, that PX4 objects to. The runner only reaches for this layout
    after the ArduPilot-shaped one failed to start; ArduPilot never gets here.
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

    dicts = []
    if home_placeholder:
        # seq 0: HOME placeholder (content is ignored by the autopilot, which
        # substitutes its own home; the slot must exist).
        dicts.append(
            {
                "seq": 0,
                "frame": FRAME_GLOBAL_INT,
                "command": MAV_CMD_NAV_WAYPOINT,
                "current": 0,
                "autocontinue": 1,
                "x": int(round(first_lat * 1e7)),
                "y": int(round(first_lon * 1e7)),
                "z": 0.0,
            }
        )
    # takeoff - the first real item, so it carries current=1.
    dicts.append(
        {
            "seq": len(dicts),
            "frame": FRAME_GLOBAL_REL_ALT,
            "command": MAV_CMD_NAV_TAKEOFF,
            "current": 1,
            "autocontinue": 1,
            "x": int(round(first_lat * 1e7)),
            "y": int(round(first_lon * 1e7)),
            "z": float(takeoff_altitude_m),
        }
    )
    for wp in waypoints:
        lat = float(wp.get("latitude_deg", wp.get("lat", 0.0)))
        lon = float(wp.get("longitude_deg", wp.get("lon", 0.0)))
        # Not a telemetry reading and not a datum question: this is the caller's
        # requested waypoint altitude, and it is flown in FRAME_GLOBAL_REL_ALT,
        # so the autopilot's own relative datum is the frame it is expressed in
        # by construction. Nothing to correct here.
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
