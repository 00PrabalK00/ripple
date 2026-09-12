#!/usr/bin/env bash
set -eo pipefail
ripple_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source /opt/ros/humble/setup.bash
source "$ripple_root/install/setup.bash"
cd "$ripple_root"
exec .venv/bin/python -m uvicorn ripple.server:app --host 127.0.0.1 --port 8050
