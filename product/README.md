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
