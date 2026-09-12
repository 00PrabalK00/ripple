#!/usr/bin/env bash
# Stop the SMR300 simulator stack started by sim_up.sh. PostgreSQL keeps running.
set -uo pipefail
for pattern in keepout_zone_publisher "navigation.launch.py" start_navigation.sh start_sim.sh \
               "launch_sim.launch.py" gzserver gzclient; do
  pkill -INT -f -- "$pattern" 2>/dev/null && echo "stopping $pattern"
done
sleep 5
for pattern in gzserver gzclient nav2_ bt_navigator controller_server planner_server amcl map_server \
               behavior_server safety_controller twist_mux scan_merger keepout_zone_publisher; do
  pkill -KILL -f -- "$pattern" 2>/dev/null && echo "killed leftover $pattern"
done
echo "SMR300 simulator stack stopped."
