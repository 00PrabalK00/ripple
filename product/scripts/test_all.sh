#!/usr/bin/env bash
# Ripple's test suite, in tiers.
#   test_all.sh unit   offline unit and integration tests, with coverage (no ROS graph, no network)
#   test_all.sh tui    the setup wizard's dialogs, driven with keypresses in a pseudo-terminal
#   test_all.sh live   against the running simulator and an agent started with --test-api
#   test_all.sh all    all three, live last
set -eo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root/product"
source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"
export PYTHONPATH="$root/product/agent:$root/product/ripple_edge:$root/product/tests:${PYTHONPATH:-}"
py="$root/product/.venv/bin/python"

unit() {
  "$py" -m coverage run --source=agent/ripple_agent,ripple_edge/ripple_edge -m unittest discover -s tests
  "$py" -m coverage report --sort=cover | tail -45
}

tui() { "$py" scripts/tui_smoke.py; }

live() {
  api=http://127.0.0.1:8060
  curl -fsS -m 5 "$api/api/state" >/dev/null || { echo "Start the simulator and an agent with --test-api first." >&2; exit 1; }
  # The recovery checks need room around the robot: start from the open floor at the Home dock.
  python3 - <<'PY'
import json, time, urllib.request
API = 'http://127.0.0.1:8060'
def post(path, body):
    r = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(r, timeout=120))
state = lambda: json.load(urllib.request.urlopen(API + '/api/state', timeout=10))
for k in state()['keepouts']:
    post('/api/keepouts/reopen', {'id': k['id']})
post('/api/test/tool', {'name': 'set_station_availability', 'args': {'destination': 'HOME', 'available': True, 'reason': 'test start'}})
post('/api/test/tool', {'name': 'navigate_to', 'args': {'destination': 'HOME', 'reason': 'test start'}})
for _ in range(240):
    g = state()['goal'] or {}
    if g.get('label') == 'Home dock' and g.get('status') in ('SUCCEEDED', 'ABORTED', 'CANCELED') and 'stopped' in g:
        break
    time.sleep(1)
print('start:', g.get('status'), g.get('outcome_reason'))
PY
  site="$(mktemp -d)/ripple.json"
  "$py" -m ripple_edge.crawl --workspace "${RIPPLE_WORKSPACE:-$HOME/Ripple/smr300l_gazebo_ros2control}" --live --out "$site" >/dev/null
  "$py" -m ripple_agent.doctor --site "$site" --root "$root" --no-keys
  python3 scripts/via_check.py
  python3 scripts/live_tests.py
  python3 scripts/learning_check.py
  python3 scripts/second_profile_check.py   # restarts the agent, so it runs last
}

case "${1:-unit}" in
  unit) unit ;;
  tui) tui ;;
  live) live ;;
  all) unit; tui; live ;;
  *) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
