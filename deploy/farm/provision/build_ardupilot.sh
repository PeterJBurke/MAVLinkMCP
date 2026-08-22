#!/bin/bash
set -ex
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git
cd /home/dronepilot
if [ ! -d ardupilot ]; then
  sudo -u dronepilot git clone https://github.com/ArduPilot/ardupilot.git
fi
cd ardupilot
sudo -u dronepilot git fetch --tags origin
sudo -u dronepilot git checkout Copter-4.5.7
sudo -u dronepilot git submodule update --init --recursive
Tools/environment_install/install-prereqs-ubuntu.sh -y
chown -R dronepilot:dronepilot /home/dronepilot/ardupilot
su - dronepilot -c "cd /home/dronepilot/ardupilot && source ~/.profile && ./waf configure --board sitl && ./waf build --target bin/arducopter -j8"
echo BUILD_DONE
