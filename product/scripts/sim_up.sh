#!/usr/bin/env bash
# Start the SMR300 simulator stack Ripple runs against. Idempotent: running parts are kept.
# Logs: /tmp/ripple-{sim,navigation,keepout}.log. Stop with product/scripts/sim_down.sh.
# Readiness checks use rclpy directly (ros_wait.py); the ros2 CLI daemon is not relied on.
set -eo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="${RIPPLE_SIM_ROOT:-$HOME/Ripple}"
cd "$root"
source /opt/ros/humble/setup.bash
source install/setup.bash
wait_ros() { python3 "$here/ros_wait.py" "$@"; }

running() { pgrep -f -- "$1" >/dev/null; }
start() {
  local name=$1; shift
  setsid nohup bash -c "$*" >"/tmp/ripple-$name.log" 2>&1 </dev/null &
  echo "started $name (pid $!)"
}
step() { local what=$1; shift; if "$@"; then echo "ready: $what"; else echo "timed out waiting for $what" >&2; exit 1; fi; }

docker compose up -d --wait postgres >/dev/null && echo "ready: postgres"
running gzserver || start sim "bash scripts/start_sim.sh"
step "odometry" wait_ros --topic /diff_cont/odom --type nav_msgs/msg/Odometry --timeout 120
running bt_navigator || start navigation "bash scripts/start_navigation.sh"
running keepout_zone_publisher || start keepout \
  "source /opt/ros/humble/setup.bash && source install/setup.bash && exec ros2 run next_ros2ws_core keepout_zone_publisher"
step "Nav2 active" wait_ros --lifecycle /bt_navigator --timeout 180
step "keepout mask" wait_ros --topic /keepout_filter_mask --type nav_msgs/msg/OccupancyGrid --timeout 60
# The safety controller only accepts autonomous goals in zones mode.
python3 - <<'PY'
import time, rclpy
from std_msgs.msg import String
rclpy.init(); n = rclpy.create_node('ripple_mode'); pub = n.create_publisher(String, '/control_mode', 10)
for _ in range(10):
    pub.publish(String(data='zones')); rclpy.spin_once(n, timeout_sec=.1); time.sleep(.2)
n.destroy_node(); rclpy.shutdown()
PY
step "safety status in zones mode" wait_ros --service-contains /safety/status "Control Mode: zones" --timeout 30
echo "SMR300 simulator stack is up."
