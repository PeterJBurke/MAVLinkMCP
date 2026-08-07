# Throwaway ArduPilot SITL (ArduCopter) for local integration tests.
#
# Uses the official prebuilt SITL binary from firmware.ardupilot.org, pinned to
# a stable Copter release, plus the matching default parameter file from the
# ArduPilot repo at the same tag. No toolchain, no Gazebo - boots headless.
#
# Build:  docker build -t droneserver-sitl-arducopter:4.5.7 docker/ardupilot-sitl
# Run:    docker run -d --rm -p 127.0.0.1:5760:5760 -p 127.0.0.1:5762:5762 \
#             droneserver-sitl-arducopter:4.5.7
#
# The pytest fixture in tests/integration/conftest.py builds and runs this
# automatically with ephemeral host ports.

FROM ubuntu:22.04

ARG COPTER_VERSION=4.5.7

# Plain ADD + chmod (no --chmod) so the legacy builder works too, not only BuildKit.
ADD https://firmware.ardupilot.org/Copter/stable-${COPTER_VERSION}/SITL_x86_64_linux_gnu/arducopter /usr/local/bin/arducopter
ADD https://raw.githubusercontent.com/ArduPilot/ardupilot/Copter-${COPTER_VERSION}/Tools/autotest/default_params/copter.parm /sitl/copter.parm
COPY sitl.parm /sitl/sitl.parm
RUN chmod 755 /usr/local/bin/arducopter && chmod 644 /sitl/copter.parm /sitl/sitl.parm

WORKDIR /sitl

# Known home location: CMAC (ArduPilot autotest field): lat,lon,alt_amsl_m,heading_deg
ENV SITL_HOME="-35.363262,149.165237,584.09,353"
ENV SITL_SPEEDUP="1"

# serial0 -> tcp 5760 (for the droneserver / MavSDK). Passed explicitly WITHOUT
# the default ":wait" suffix so the simulation boots and the EKF converges even
# before any client connects.
# serial2 -> tcp 5762 (for the test harness readiness probe). ArduPilot
# serial-over-TCP accepts a single client per port, hence two ports.
# NOTE: the readiness probe uses SERIAL2, not SERIAL1 - the simulated ADS-B
# transceiver (ADSB_TYPE=1) claims SERIAL1, which would otherwise steal the
# probe's port.
EXPOSE 5760 5762

ENTRYPOINT ["/bin/sh", "-c", "exec /usr/local/bin/arducopter --model + --defaults /sitl/copter.parm,/sitl/sitl.parm -I0 --home ${SITL_HOME} --speedup ${SITL_SPEEDUP} --serial0 tcp:0 --serial2 tcp:2"]
