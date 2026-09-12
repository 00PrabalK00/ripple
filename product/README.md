# Ripple product: edge and agent

Implementation of [the accepted blueprint](docs/blueprint.txt), separate from the
hackathon demo. Worktree branch: `product/edge-agent`. The existing `ripple/` demo
is unchanged. The edge defaults to **observe-only** and cannot send motion or
recovery commands during this stage. The demo remains the simulator's mission owner.

## Acceptance ledger

- [x] 0 Contracts: validate profiles, tools, events and sample responses.
- [ ] 1 Observe-only edge: attribute held safety stop and stall in the simulator.
- [x] 2 Logs: classify actual Humble failures, with source evidence.
- [ ] 3 Policy/Level 2: narrow executors, budgets, denials, verified outcomes.
- [ ] 4 Escape: bounded BackUp/Spin; held-stop refusal and rear-pallet tests.
- [ ] 5 Agent: deterministic recovery ladders, model advice, PostgreSQL memory.
- [ ] 6 People: HumanChannel, ambiguous.ai adapter, workflow MCP end-to-end.
- [ ] 7 Goals/maps: command-as-approval, authenticated operator provenance,
      registered stations, automatic checks, rotated maps and observed keepouts.
- [ ] 8 Second robot: stock Nav2 profile; no code changes; escape disabled without safety.

Gates: steps 0–2 are observe-only. Live mutation tests require the demo runtime to
be off and exclusive edge ownership verified. No second motion owner. All motion
remains below the existing safety controller; no raw velocity or safety overrides.
The Ambiguous CLI is authenticated and its chat/notification contract has been
inspected. The product HumanChannel adapter and end-to-end incident flow remain pending.

Required early scenarios: held stop blocks escape; BackUp rejects rear pallet;
rotation, goal waiting and slow docking do not fire false halt events; Humble log
patterns come from recorded evidence. Later steps are not complete merely because
the foundations compile or unit tests pass.

## Development commands

```bash
# Python 3.10 environment with product/requirements.txt; ROS Humble installed
source /opt/ros/humble/setup.bash
export PYTHONPATH="$PWD/product/ripple_edge:$PYTHONPATH"
python -m unittest discover -s product/tests -v
colcon build --base-paths product/ripple_edge --build-base product/build --install-base product/install
python -m ripple_edge.main --profile product/profiles/smr300.yaml \
  --snapshot-file /tmp/ripple-edge-observation.json --duration 35
```

The observer does not request AMCL updates or create any navigation/behavior action
client. It subscribes to configured inputs and reads safety/GetState services.
`velocity_winner` is derived from fresh samples and configured priorities; it is
not claimed to be direct mux output telemetry. Current live evidence correctly
attributes the existing simulator hold to the safety obstacle stop and safety
velocity input. A simulated Nav2 stall still needs a separate live acceptance run.

Ambiguous uses the CLI credential in this worktree's ignored `.ambi/` directory.
No parallel Ambiguous MCP server is installed. Its Codex managed watcher is a
session integration; autonomous event handling is not verified until two successive
operator test DMs reach the session and receive replies.

Read-only service requests expire, and late or superseded replies cannot refresh
observations. Unit coverage includes a hung request followed by a successful retry.
The first Ambiguous workspace event woke the Codex session and the first test DM
received a reply; a second DM after that reply is still required for repeat-delivery verification.

The post-timeout-change 20-second live run exited cleanly and received fresh
safety, scan, odometry and localization samples. It reported `unknown` with an
incomplete lifecycle sample, rather than claiming healthy Nav2. This run does
not prove a live stall or recovery; the earlier held-stop capture is separate.

## RosScope diagnostics

Build the existing read-only bridge with `scripts/build_rosscope_bridge.sh`, then
pass `--rosscope-binary "$PWD/build/rosscope-observe"` to the edge observer.
This is an operator installation option, not an agent tool parameter. The bridge
uses RosScope inspection services only; its process controls are not exposed.
The child receives ROS/library environment settings without model or database
credentials. A collection is bounded to 150 seconds, and reports older than 90
seconds from collection start are stale. Missing, failed and wrong-domain reports
are unavailable, never healthy. Fast edge observations continue during collection.

The live product integration received a RosScope report containing TF edges and
lifecycle observations. Collection took roughly 80–90 seconds on this running
simulator; the saved capture is already older than the 90-second freshness limit
and is correctly marked stale. This proves collection and expiry, not complete
Nav2 health or readiness to dispatch. The fast observer remains the source for
short-lived safety and motion facts. Live stall acceptance is still pending.

## Read-only MCP edge

The edge uses the [official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/v1/),
pinned to 1.30.0. Install `product/requirements.txt` in an isolated Python environment
with ROS system packages available. With ROS sourced and `product/ripple_edge` on
`PYTHONPATH`, launch `python -m ripple_edge.mcp_server --profile product/profiles/smr300.yaml`.
Use stdio transport from an MCP client on the robot host. The server owns its ROS
observer; it does not serve a development snapshot file. This first transport is
local stdio; remote authenticated transport remains to be implemented.

Tools: `get_robot_health`, `get_pose`, `get_nav_status`, `get_diagnostics`,
`get_recent_logs`, and `get_events`. Every tool requires a `request_id` and returns
an explicit status, observations, source and age. Missing or stale facts produce
`unknown`. A successful read is not a claim that the robot is healthy or that an
action was verified. Motion and recovery verbs are absent at this stage.

Clients can read and subscribe to `ripple://smr300_01/events`. An update notification
means the client should read the bounded event history again. Protocol tests cover
discovery, stale/missing facts, invalid arguments, absent motion tools, subscription,
notification delivery, event reading and unsubscribe. `product/scripts/probe_edge.py`
is the live stdio acceptance probe; it only reads the simulator.

Live stdio evidence is in `evidence/observe-only-mcp-live.json`: all six tools were
discovered, pose was fresh, and health correctly returned `unknown` because the
lifecycle sample was incomplete. The observer and client both shut down cleanly.
This does not establish a successful recovery or navigation mission through MCP.
