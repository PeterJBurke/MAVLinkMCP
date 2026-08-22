# llmuavfarm rebuild recipe (recovered 2026-08-22, pre-deletion)

These files are the orchestration and provisioning layer of the 10-lane SITL
farm (`llmuavfarm`, Linode Dedicated 8 vCPU/16 GB) that flew the Revision-4
re-fly round on 2026-08-19/20. They were recovered from the live box before it
was deleted; none of them had ever been committed. Provenance and the full
inventory: `LLMUAV/Research/FARM-PRESERVATION-AUDIT_2026-08-22.md` (paper repo).

Layout:
- `provision/` - the five /root build scripts as found (ArduPilot builds, lane
  env generation, the R0 readiness gate driver).
- `systemd/` - the five per-lane unit templates (`sitl@`, `sitl-px4@`,
  `droneserver-lane@`, `mavlink-relay-lane@`, `px4-mavbridge-lane@`) plus the
  per-instance drop-ins for lanes 7-9 exactly as deployed.
- `scripts/` - the 24 run-orchestration scripts that drove the round (they
  lived untracked in `scripts/` on the farm's checkout, plus `rtl_diag.py`).

Known gaps, documented rather than papered over (audit section 3):
- Steps done BY HAND with no script: the `dronepilot` user, all three venvs,
  the PX4 v1.16.2 build (survives only as a build log), the `/opt/sitl` lane
  directory layout, the PX4 lane env files, and installing/enabling the units.
  The recovered shell histories in the farm archive
  (`/root/farm-archive/` on llmuavdev) are the best record of those steps.
- `rebuild_c683d8c1.sh` builds ArduPilot but never performs the
  `git checkout c683d8c1` it names - do the checkout manually first.
- `sitl@.service` cites `file:///home/dronepilot/CreateSITLenv/README.md`,
  a path that never existed on the farm (copy-pasted from the single-lane box).
- The 11 `/etc/droneserver/lane*.env` files are NOT here (they carry the lane
  safety-layer API keys); they live in the farm archive on llmuavdev, and
  `provision/gen_lane_envs.sh` regenerates the ArduPilot-lane ones.

The exact firmware binaries flown (`arducopter-c683d8c1`, `arducopter-4.5.7`)
are preserved in the farm archive, not in git.
