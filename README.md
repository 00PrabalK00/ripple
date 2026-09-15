# Ripple

## Requirements

- Ubuntu 22.04
- ROS 2 Humble
- Python 3.10+
- Node.js and npm
- Docker, for PostgreSQL
- Qt6Core, for RosScope diagnostics

## Download

```bash
git clone https://github.com/00PrabalK00/ripple
cd ripple
```

For the SMR300 simulator, clone the external simulator checkout beside this repository:

```bash
git clone https://github.com/00PrabalK00/smr300l_gazebo_ros2control.git smr300l_gazebo_ros2control
```

Optional simulator patches:

```bash
cd smr300l_gazebo_ros2control
patch -p1 < ../patches/smr300-speed-units.patch
patch -p1 < ../patches/smr300-keepout-publish-on-change.patch
cd ..
```

## Install

Run the installer against a ROS workspace:

```bash
bash product/scripts/install.sh --workspace ~/robot_ws
```

Install Python dependencies manually when developing without the installer:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r product/requirements.txt
```

Set up the Python path for local development:

```bash
source /opt/ros/humble/setup.bash
export PYTHONPATH="$PWD/product/agent:$PWD/product/ripple_edge:$PYTHONPATH"
```

## Configure

Run guided setup:

```bash
ripple setup
```

Check a generated site profile:

```bash
ripple doctor --site ~/robot_ws/ripple.json
```

Common commands:

| Command | Purpose |
|---|---|
| `ripple setup` | Guided terminal setup. |
| `ripple setup --non-interactive` | Setup using command flags. |
| `ripple crawl --live --existing ripple.json` | Inspect the live ROS graph and compare it with an existing profile. |
| `ripple doctor --site ripple.json` | Validate the profile, ROS graph, services, actions, topics and credentials. |
| `ripple run --site ripple.json` | Start the agent and dashboard. |

Secrets belong in `.env`. Keep `OPENROUTER_API_KEY`, `AMBI_API_TOKEN` and database credentials out of committed files.

## Run

Start the agent:

```bash
ripple run --site ~/robot_ws/ripple.json
```

The dashboard runs at:

```text
http://127.0.0.1:8060
```

For product development scripts:

```bash
bash product/scripts/ripple_agent.sh
```

For simulator helpers:

```bash
bash product/scripts/sim_up.sh
bash product/scripts/restart_all.sh
bash product/scripts/sim_down.sh
```

## Database

Install Node dependencies:

```bash
npm ci
```

Run Drizzle migrations:

```bash
npm run db:migrate
```

Generate migrations after schema changes:

```bash
npm run db:generate
```

## RosScope

Clone RosScope beside this repository:

```bash
git clone https://github.com/00PrabalK00/RosScope.git ../RosScope
```

Build the bridge:

```bash
ROSSCOPE_SOURCE=../RosScope bash scripts/build_rosscope_bridge.sh
```

Run the edge observer with RosScope:

```bash
python -m ripple_edge.main --profile product/profiles/smr300.yaml --rosscope-binary "$PWD/build/rosscope-observe" --snapshot-file /tmp/ripple-edge-observation.json --duration 35
```

## Build

Build the edge package:

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths product/ripple_edge --build-base product/build --install-base product/install
```

## Test

Run product tests:

```bash
product/scripts/test_all.sh unit
product/scripts/test_all.sh tui
product/scripts/test_all.sh live
```

Run tests directly:

```bash
source /opt/ros/humble/setup.bash
export PYTHONPATH="$PWD/product/agent:$PWD/product/ripple_edge:$PYTHONPATH"
python -m unittest discover -s product/tests -v
```

Run earlier prototype tests:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Run live utility checks:

```bash
python product/scripts/probe_edge.py
python product/scripts/via_check.py
python product/scripts/learning_check.py
python product/scripts/live_tests.py
```

## Website

The Vercel site source is in `site/`.

```bash
cd site
vercel deploy
```

Local Vercel build output is configured by `site/vercel.json`.

## Repository Layout

| Path | Contents |
|---|---|
| `product/ripple_edge/` | ROS 2 edge package. |
| `product/agent/` | Agent, setup, doctor, dashboard and communication adapters. |
| `product/profiles/` | Robot profiles. |
| `product/scripts/` | Install, simulator, live-check and demo scripts. |
| `product/tests/` | Product test suite. |
| `product/docs/` | Technical docs. |
| `site/` | Vercel website. |
| `db/` | Root Drizzle schema and migrations. |
| `ripple/`, `docs/`, `evidence/` | Earlier prototype code, docs and evidence. |
