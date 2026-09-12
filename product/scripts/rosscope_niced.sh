#!/usr/bin/env bash
# RosScope's collector shells out to the ros2 CLI. Run it at idle CPU and I/O priority so
# Nav2 and AMCL keep their real-time headroom (unniced, it delayed map->odom enough to abort goals).
exec nice -n 19 ionice -c3 "${RIPPLE_ROSSCOPE_BIN:-$HOME/Ripple/build/rosscope-observe}" "$@"
