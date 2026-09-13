#!/usr/bin/env bash
# Install Ripple, then run the guided setup for a robot.
#   bash install.sh [--workspace ~/robot_ws]        (from a checkout, or fetched on its own)
# Needs: ROS 2 Humble, python3-venv, git. Optional: Node.js (Ambiguous CLI, database bridge),
# Docker (PostgreSQL), Qt6Core (RosScope). Everything else is installed into ~/.ripple.
set -eo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -f "$here/../agent/ripple_agent/setup.py" ]; then
  dest="$(cd "$here/../.." && pwd)"   # already inside a checkout
else
  dest="${RIPPLE_HOME:-$HOME/.ripple/ripple}"
  repo="${RIPPLE_REPO:-https://github.com/00PrabalK00/ripple-private.git}"
  branch="${RIPPLE_BRANCH:-product/edge-agent}"
  if [ -d "$dest/.git" ]; then git -C "$dest" pull --ff-only; else git clone --branch "$branch" "$repo" "$dest"; fi
fi
cd "$dest"
[ -f "/opt/ros/${ROS_DISTRO:-humble}/setup.bash" ] || { echo "ROS 2 ${ROS_DISTRO:-humble} is required (/opt/ros/${ROS_DISTRO:-humble})." >&2; exit 1; }
source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"
# rclpy and the message packages come from ROS, so the venv sees the system site packages.
[ -x product/.venv/bin/python ] || python3 -m venv --system-site-packages product/.venv
product/.venv/bin/pip install -q --upgrade pip
product/.venv/bin/pip install -q -r product/requirements.txt
node_bin="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | tail -1)"
[ -n "$node_bin" ] && export PATH="$node_bin:$PATH"
if command -v npm >/dev/null && [ -f package-lock.json ]; then npm ci --silent; else echo "Node.js not found: Ambiguous and the database bridge need it (https://nodejs.org)."; fi
mkdir -p "$HOME/.local/bin"
ln -sf "$dest/product/scripts/ripple" "$HOME/.local/bin/ripple"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Add ~/.local/bin to PATH to use the 'ripple' command.";; esac
echo "Ripple is installed in $dest. Starting setup..."
exec "$dest/product/scripts/ripple" setup "$@"
