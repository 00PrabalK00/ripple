#!/usr/bin/env bash
# Start the Ripple Agent for one robot. Extra arguments pass through, e.g. --no-ambiguous.
#   product/scripts/ripple_agent.sh [--profile product/profiles/smr300.yaml] [--port 8060]
set -eo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source /opt/ros/humble/setup.bash
node_bin="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | tail -1)"
[ -n "$node_bin" ] && export PATH="$node_bin:$PATH"  # npx runs the Ambiguous CLI
export PYTHONPATH="$root/product/agent:$root/product/ripple_edge:${PYTHONPATH:-}"
args=(--root "$root")
# A ripple.json from `ripple setup` carries the profile and the operators; otherwise use the checkout's defaults.
if [[ " $* " != *" --site "* ]]; then
  args+=(--config "$root/product/config/agent.json")
  [[ " $* " == *" --profile "* ]] || args+=(--profile "$root/product/profiles/smr300.yaml")
fi
for candidate in "$root/build/rosscope-observe" "$HOME/Ripple/build/rosscope-observe"; do
  if [[ -x "$candidate" && "${RIPPLE_ROSSCOPE:-1}" != 0 && " $* " != *" --rosscope-binary "* ]]; then
    export RIPPLE_ROSSCOPE_BIN="$candidate"
    args+=(--rosscope-binary "$root/product/scripts/rosscope_niced.sh"); break
  fi
done
exec "$root/product/.venv/bin/python" -m ripple_agent.app "${args[@]}" "$@"
