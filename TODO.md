# Ripple build checklist

Source: `Ripple_For_Robotics_Plan.docx`. One simulated robot, two registered
destinations, owned Nav2 actions, human-approved repairs, and live camera facts.

## 1. Integration and contracts — in progress
- [x] Read the complete build plan and inspect the inherited simulator interfaces.
- [x] Verify ROS Humble and Nav2 Python imports on this laptop.
- [x] Inspect the live ROS graph: only `/rosout` and `/parameter_events` present.
- [x] Build the inherited ROS packages and resolve local paths/assets.
- [x] Start simulation; verify map, localization, odometry, mode and safety service.
- [ ] Map Packing A/B to existing reachable zones and verify their poses.
- [x] Define initial fact/proposal/target records and robot adapter events.
- [ ] Send, cancel and complete an owned goal against Nav2.
  - Live movement, terminal CANCELED and settled odometry verified. Arrival pending.

## 2. Mission supervision
- [x] Implement PostgreSQL/Drizzle append-only execution journal (user updated persistence choice).
- [x] Implement initial versioned facts, freshness and exact-target proposal guards.
- [x] Implement single-use approvals, 60-second expiry and dispatch pause in the core.
- [x] Serialize guard checks and persist a send claim before contacting ROS.
- [x] Require terminal action result AND fresh settled odometry after cancellation.
- [x] Hold on restart or uncertain send; never replay automatically.

## 3. Interpretation and operator panel
- [ ] Connect one model with validated, bounded structured output.
  - OpenRouter client implemented; GLM 5.3 agent and GLM 5.3 Flash vision configured.
  - Key configured; real GLM 5.3 initial mission interpretation validated.
- [ ] Pause dispatch during interpretation; handle unrelated and ambiguous input.
- [x] Build localhost:8050 panel with proposals, approval/rejection and receipts.
- [x] Show existing map alongside mission state and explicit dispatch pause.

## 4. Live camera
- [ ] Verify D435i connection; select B region and capture empty baseline.
  - Camera pipeline, calibration controls and on-demand vision implemented.
  - D435i connected and streaming; handheld positioning and empty baseline calibration pending.
- [ ] Align depth to color and apply device depth scale.
- [ ] Implement validity, hysteresis, stable transitions and one-second freshness.
- [ ] Invalidate approvals and cancel executing B on BLOCKED or UNKNOWN.
- [ ] Show camera image/region and explain the physical proxy.

## 5. Acceptance and demo
- [ ] Test normal success, stale approvals, clear-after-block and camera loss.
- [ ] Test duplicate clicks, cancellation uncertainty and irrelevant/ambiguous input.
- [ ] Test target changes, mode, overrides, stops and stale robot observations.
- [ ] Run the complete live scenario twice, including alternate wording.
- [ ] Document tested launch sequence, setup, credentials and inherited code.
- [ ] Record two-minute demo and prepare submission text; publishing requires instruction.

## Findings
- Current focused suite: 16 passing tests. Live cancellation verified, arrival
  still fails (initial speed-unit mismatch fixed; later TF time jumps and action
  acknowledgement timeout observed). See README for precise current limits.
- Inherited speed-filter publisher corrected: type 2 (absolute m/s) matches its
  existing multiplier. Previously type 1 misinterpreted 0.3 m/s as 0.3% speed.
- All six inherited ROS packages build successfully. Ten isolated foundation
  tests pass; ROS adapter imports and owned goal UUID API verified locally.
- Warehouse assets found at `/opt/ros/humble/share/bcr_bot/models`; all 13
  referenced models are present. Installed the missing
  `ros-humble-gazebo-ros2-control` package and restarted Gazebo. Both
  `diff_cont` and `joint_broad` activated. Local launcher: `bash scripts/start_sim.sh`.
- D435i connection deferred until camera integration is ready, per operator.
- The supplied launch commands reference `/home/aun/Downloads`; they are not a
  verified launch sequence for this laptop.
- `safety/status` is a `std_srvs/Trigger` service returning human-readable text,
  not a status topic. Parse required fields strictly and fail closed.
- Existing zone names are A1–A6, B1–B6 and other locations; Packing A/B mapping
  is configuration, not yet a verified reachable pair.
- Live simulator and camera acceptance results must remain separate from fake
  adapter unit tests.

## Latest integration status
- Local PostgreSQL 17 is healthy; Drizzle schema/migration and journal bridge are live.
- Imported 34 legacy receipts transactionally; retained SQLite backup.
- 18 tests pass, including PostgreSQL reconnect durability and failed-write/no-send.
- Operator requested handheld camera testing: recalibrate after each repositioning.
- GitHub repository excludes simulator checkout; README credits the simulator and includes setup/patch instructions.
