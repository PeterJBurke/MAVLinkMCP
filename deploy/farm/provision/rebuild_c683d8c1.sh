#!/bin/bash
set -ex
date -u +"BUILD_START %Y-%m-%dT%H:%M:%SZ"
su - dronepilot -c "cd /home/dronepilot/ardupilot && source ~/.profile && ./waf configure --board sitl"
date -u +"CONFIGURE_DONE %Y-%m-%dT%H:%M:%SZ"
su - dronepilot -c "cd /home/dronepilot/ardupilot && source ~/.profile && ./waf build --target bin/arducopter -j8"
date -u +"BUILD_END %Y-%m-%dT%H:%M:%SZ"
echo BUILD_DONE
