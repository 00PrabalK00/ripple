#!/usr/bin/env bash
set -eo pipefail
ripple_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source /opt/ros/humble/setup.bash
source "$ripple_root/install/setup.bash"
export GAZEBO_MODEL_DATABASE_URI=""
export GAZEBO_MODEL_PATH="/opt/ros/humble/share/bcr_bot/models${GAZEBO_MODEL_PATH:+:$GAZEBO_MODEL_PATH}"
exec ros2 launch my_bot launch_sim.launch.py use_sim_time:=true \
  world:="$ripple_root/smr300l_gazebo_ros2control/worlds/small_warehouse.world"
