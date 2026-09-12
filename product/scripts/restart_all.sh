#!/usr/bin/env bash
# Clean restart for a take: stop the agent, restart the simulator (robot back at spawn), start the agent.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
pid=$(pgrep -f "[r]ipple_agent.app" | head -1)
[ -n "$pid" ] && kill -TERM "$pid"
for _ in $(seq 1 25); do pgrep -f "[r]ipple_agent.app" >/dev/null || break; sleep 1; done
pkill -f "[r]osscope-observe" 2>/dev/null
bash "$here/sim_down.sh" >/dev/null 2>&1
sleep 3
bash "$here/sim_up.sh" || exit 1
cd "$root" && (setsid nohup bash product/scripts/ripple_agent.sh "$@" > /tmp/ripple-agent.log 2>&1 < /dev/null &)
for _ in $(seq 1 90); do curl -s --max-time 2 http://127.0.0.1:8060/api/state >/dev/null && break; sleep 1; done
curl -s http://127.0.0.1:8060/api/state | python3 -c "import json,sys; d=json.load(sys.stdin); print('agent:', d['status'], 'pose', d['pose'] and (d['pose']['x'], d['pose']['y']))"
