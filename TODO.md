# Current simulator-only product work

- [x] Inspect RosScope upstream and pin the integration revision.
- [x] Build a read-only bridge using RosScope C++ inspection services.
- [x] Remove physical camera requirements from simulator missions and dashboard.
- [x] Consume continuous observations without blocking mission execution.
- [x] Open durable incidents on aborted/rejected navigation.
- [x] Verify live RosScope lifecycle observations: all eight observed Nav2 nodes active; TF health remains raw evidence.
- [x] Natural-language map image interpretation and drawn-region previews.
- [x] Durable rectangular keepouts with observed mask/costmap and blocked-destination/restored-path checks.
- [x] Verify buffered planner detour: SUCCEEDED with at least 0.667 m sampled center clearance.
- [ ] Verify moving navigation around restrictions (latest attempt moved, then ABORTED; not passed).
- [x] Check preview-to-published-mask alignment through four browser drags, including mobile width.
- [x] Display actual ROS mask separately from focused drafts.
- [ ] Slow zones and scheduled expiry.
- [ ] Autonomous evidence-based diagnosis with bounded recovery and escalation.
- [ ] Human-assisted restriction, reroute and verified mission continuation.
- [ ] Record the simulator hero demo.

Detailed scope: [PRODUCT_PLAN.md](docs/PRODUCT_PLAN.md).

# Archived first-prototype checklist

# Ripple build checklist

One simulated robot, two registered destinations, owned Nav2 actions, human-approved repairs, and live camera facts. Local PostgreSQL + Drizzle replaces the original plan's SQLite choice at the operator's request.

## Integration and navigation
- [x] Read the complete build plan and inspect the inherited interfaces.
- [x] Build all six ROS Humble packages and resolve local assets/paths.
- [x] Verify live map, AMCL, odometry, mode and safety service.
- [x] Verify Packing A=A1 at (3.61,0.47).
- [x] Verify Packing B=EXIT BAY at (4.21,2.18), including travel in both directions.
- [x] Send and cancel an owned Nav2 goal; confirm terminal CANCELED and settled odometry.
- [x] Reach destinations with real Nav2 SUCCEEDED and settled odometry receipts.

## Mission supervision
- [x] Implement PostgreSQL/Drizzle event journal; migrate 34 old receipts without deleting the SQLite backup.
- [x] Define versioned facts, exact destination proposals and owned adapter events.
- [x] Check freshness, versions, mission revision, target, mode, overrides and stop flags before dispatch.
- [x] Persist a send claim before contacting ROS; block dispatch on database failure.
- [x] Enforce single-use approvals, 60-second expiry and explicit dispatch pause.
- [x] Require terminal result and fresh settled odometry before replacement dispatch.
- [x] Hold after restart or uncertain send; old approvals are never replayed.
- [x] Expire a proposal when a changed target is checked at dispatch.

## Interpretation and panel
- [x] Connect GLM 5.3 through OpenRouter with locally validated structured output.
- [x] Live-test both inspection wordings, unrelated input and ambiguous input.
- [x] Pause dispatch during interpretation; retain recent conversation for clarification.
- [x] Serve the operator panel at localhost:8050 with exact-target approval, rejection, map and receipts.
- [x] Show a visible dispatch pause and approval countdown.
- [x] Connect on-demand GLM 5.3 Flash scene descriptions; keep them outside motion authorization.

## Camera
- [x] Detect and stream the connected D435i.
- [x] Align depth to color and apply the device depth scale.
- [x] Calibrate the empty region around the right-hand physical B marker.
- [x] Observe live CLEAR → BLOCKED → CLEAR with the case.
- [x] Implement valid-depth thresholds, hysteresis and one-second freshness.
- [x] Invalidate pending approvals and request cancellation of executing B on BLOCKED/UNKNOWN.
- [x] Demonstrate a human-approved proposal expiring on camera version change, followed by a rejected dispatch with send_attempted=false.
- [ ] Verify an actual camera disconnect/reconnect; current freshness/invalid-depth behavior has automated coverage.
- [ ] Verify the physical blocking of an executing B mission (unit coverage exists; live test pending).

## Acceptance and demo
- [x] Pass 20 focused tests, including real PostgreSQL durability and failed-write/no-send.
- [x] Capture live cancellation, arrival and camera-expiry receipts in evidence/.
- [ ] Run the complete combined A inspection → cancellation → B expiry → fresh B arrival story twice on the final station pair (first passed; evidence/combined-rehearsal-1.json).
- [ ] Verify the complete ambiguous-input clarification interaction against the live model/runtime.
- [ ] Record the two-minute demo and prepare submission text.
- [ ] Verify the exact inherited simulator revision before submission.
- [x] Create the public GitHub repository, exclude simulator/secrets/data, and credit the simulator in README.
- [x] Verify the newly added GitHub Actions workflow after push (run 34704842164 passed).

## Current constraints and fixes
- The camera is handheld by operator choice. Recalibrate after repositioning; camera movement is not automatically distinguished from object movement.
- Initial B1 route failed because the independent rear-obstacle stop engaged. It was not overridden. B is now the verified EXIT BAY zone.
- Corrected inherited speed-filter units from percentage (1) to absolute m/s (2); reproducible patch included.
- Increased the local Nav2 action acknowledgement timeout from 20 ms to 1000 ms after observed timeouts.
- AMCL is asked to process real stationary laser observations so cached poses do not remain indefinitely fresh.
- Raised the bounded GLM response budget after a truncated response, fixed interpretation journal field collision, and added regression coverage.
- Full combined demo success is not yet claimed. Individual live results and remaining work are listed separately above.
