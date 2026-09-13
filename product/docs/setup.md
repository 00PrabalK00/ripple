# Setting up Ripple for a robot

Ripple is installed once, then set up per robot with a guided terminal wizard. The wizard finds the
robot's topics, actions and services itself, so a robot whose names differ from the SMR300's still
gets a correct configuration.

## Install

```bash
bash install.sh --workspace ~/robot_ws     # from a checkout: product/scripts/install.sh
```

The installer clones Ripple into `~/.ripple/ripple` (or uses the checkout it runs from). It creates a
Python environment on top of the system ROS Python, installs the dependencies, and links a `ripple`
command into `~/.local/bin`. Then it starts `ripple setup`.

It needs ROS 2 Humble, python3-venv and git. Some parts are optional:

| Optional | Enables |
|---|---|
| Node.js | The Ambiguous CLI and the database bridge |
| Docker | PostgreSQL |
| Qt6Core | RosScope |

## `ripple setup`

Run it inside the robot's workspace, ideally with the robot (or its simulation) running.

1. **Keys.** Paste your OpenRouter key; setup checks it live. Setup creates the PostgreSQL settings and
   can start the database with Docker. Secrets go only to `.env` in the Ripple checkout (mode 600).
2. **Ambiguous.** Paste the Ripple agent's Ambiguous API token, or use the identity already signed in
   with `npx ambiguous auth login`. Setup checks it with `whoami` and lists your channels. You pick
   where Ripple should ask for help, and confirm which of that channel's members may command the
   robot. Their messages are the approval for what they ask.
3. **The robot.** The crawler reads the workspace (package.xml, launch-file remappings, Nav2 and
   twist_mux parameters, station and waypoint files) and the live ROS graph.
   - It matches every role by message type, then by the node that publishes or serves it. For
     example, odometry is the topic the controller actually uses, the rear scan is found by name, and
     the velocity output is the multiplexer's output.
   - You're asked only about fields it's unsure of, plus the robot's name and whether it's a
     simulation.
   - The teleop safety override stays off. It can be enabled only on a declared simulation.
4. **RosScope.** Found if installed; otherwise optionally cloned and built.
5. **Output.** Setup writes `ripple.json` into the robot workspace and keeps the previous one as
   `ripple.json.bak`. It validates the file against the same schema the edge enforces first, then
   runs `ripple doctor`.

`ripple setup --non-interactive --openrouter-key … --robot-name …` takes every answer from flags,
for scripted installs.

## `ripple.json`

```json
{ "schema": 1,
  "profile": { "robot": "...", "navigation": {...}, "safety": {...}, "stations": {...}, ... },
  "agent":   { "ambiguous": {...}, "operators": [...] },
  "setup":   { "evidence": { "odometry": {"confidence": "high", "source": "live graph"}, ... } } }
```

It's safe to edit by hand: every start validates it, and `ripple doctor` checks it against the robot.

## Everyday commands

| Command | What it does |
|---|---|
| `ripple crawl --live` | Shows what Ripple sees on the robot now, with confidence and evidence for each field. `--existing ripple.json` also reports drift, where the robot no longer matches the file. |
| `ripple doctor --site ripple.json` | Checks the schema. Checks every configured topic (with a live publisher where one is required), action server and service. Reports drift and checks both keys. Exits non-zero on any failure. |
| `ripple run --site ripple.json` | Starts the agent for this robot. The dashboard is at http://127.0.0.1:8060. |
