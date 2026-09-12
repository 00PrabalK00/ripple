#!/usr/bin/env bash
set -eo pipefail
ripple_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source /opt/ros/humble/setup.bash
source "$ripple_root/install/setup.bash"
exec ros2 launch "$ripple_root/scripts/navigation.launch.py"
