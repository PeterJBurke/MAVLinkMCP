#!/bin/bash
set -ex
su - dronepilot -c "cd /home/dronepilot/ardupilot && bash Tools/environment_install/install-prereqs-ubuntu.sh -y"
su - dronepilot -c "cd /home/dronepilot/ardupilot && source ~/.profile && ./waf configure --board sitl && ./waf build --target bin/arducopter -j8"
echo BUILD_DONE
