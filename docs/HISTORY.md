# History: the earlier builds

These notes describe how Ripple got here: first a mission supervisor with a depth camera, then a
RosScope-backed dashboard. They're kept for reproducibility. The current product is described in the
[README](../README.md).

## RosScope-backed dashboard (before the always-on agent)

Current direction at the time: **one robot, simulator-only**, with RosScope inspection and Ripple
reasoning and policy. Physical camera prerequisites were disabled. See [the product plan](PRODUCT_PLAN.md).

The dashboard consumed a read-only headless bridge built against
[RosScope](https://github.com/00PrabalK00/RosScope) revision `583ae6743be802f9f8aed1f834d48ca0d7e172ee`:

```bash
git clone https://github.com/00PrabalK00/RosScope.git ../RosScope
# Qt6 Core development headers/libraries are required.
ROSSCOPE_SOURCE=../RosScope bash scripts/build_rosscope_bridge.sh
```

The build applies `patches/rosscope-command-timeout.patch`, which makes the upstream command timeout
configurable.

**Collection limits**
- The collector uses an eight-second command floor and a 150-second limit for a whole collection.
- Samples older than 90 seconds are stale.
- The bridge collects process, lifecycle and TF samples asynchronously.
- Collection age is shown; empty data is unknown, not healthy.

**What it could and couldn't decide**
- Upstream TF warnings and action summaries are heuristic observations, not motion authorization.
- The owned Nav2 adapter was authoritative for mission outcomes.
- Failed navigation opened a durable incident.
- Rectangular keepouts supported map and text previews, with checks against the observed mask and
  costmap. See [site control](SITE_CONTROL.md).
- No process command lines, environments or model keys were exported by the bridge.

## The earlier mission-supervisor prototype

A local mission supervisor for one simulated SMR300 robot. Ripple tracked station availability,
proposed exact destinations, required single-use approval, and rejected work when its evidence changed.
Depth-camera observations stood for whether Packing B was free; the robot itself moved in Gazebo.

### Run (prototype)

```bash
bash scripts/start_sim.sh
# In another terminal:
bash scripts/start_navigation.sh
# In another terminal, after sourcing ROS and install/setup.bash:
ros2 run next_ros2ws_core keepout_zone_publisher
ros2 topic pub --once /control_mode std_msgs/msg/String '{data: zones}'
# In another terminal:
bash scripts/start_ripple.sh
```

Open http://127.0.0.1:8050. Don't start duplicate instances of these processes. The navigation
launcher starts AMCL at the simulator's initial (0, 0) spawn. Don't rerun it after the robot has moved
unless you supply a correct initial pose.

The configured agent was `z-ai/glm-5.3`, with `z-ai/glm-5.3-flash` for on-demand vision. Model calls
were bounded, ran outside the supervisor loop, and couldn't approve or dispatch goals. Their output was
validated locally.

### Camera (prototype)

**Placement**
- Connect a D435i and aim it down at a small tabletop region. A fixed support is preferable.
- Handheld testing is supported procedurally: brace your arms and hold still for the entire
  empty/box/empty sequence.
- Recalibrate after every repositioning. Returning to roughly the same view doesn't preserve the
  baseline, and camera motion isn't detected automatically.

**Calibration**
1. In the panel, set the region as x, y, width, height in the 640×480 image.
2. Clear the region.
3. Click Calibrate empty region, then wait for ten valid clear frames.

**Readings**
- A box at least 3 cm closer, covering 10% of the region for five frames, reports BLOCKED.
- Invalid depth, or a stream more than a second stale, reports UNKNOWN.
- Calibration is per session; reconnecting requires recalibration.
- Vision descriptions are on demand and don't authorize movement.

### Verification (prototype)

```bash
.venv/bin/python -m unittest discover -s tests -v
source /opt/ros/humble/setup.bash
python3 scripts/verify_nav2.py             # sends and cancels an actual simulated goal
python3 scripts/verify_nav2.py --complete  # attempts arrival
```

Verified live results at the time:
- An owned goal moved, returned CANCELED, and produced fresh settled odometry.
- Packing A = A1 (3.61, 0.47) and Packing B = EXIT BAY (4.21, 2.18) were reached with Nav2 SUCCEEDED,
  in both directions.
- The handheld D435i observed CLEAR → BLOCKED → CLEAR around the right-hand physical B marker.
- A human-approved B proposal expired when `camera.B` changed from version 2 to 3. Releasing dispatch
  was then rejected with `send_attempted=false`.
- GLM 5.3 passed both inspection-update wordings, the irrelevant-input check and the ambiguous-input
  check. GLM 5.3 Flash returned a live scene description.

Other notes from that stage:
- The original B1 route failed at the independent rear-obstacle stop and was replaced with the verified
  EXIT BAY zone. The safety controller was not overridden.
- A local action-acknowledgement timeout was raised from 20 ms to 1000 ms after observed failures.
- On restart, Ripple held for operator reconciliation; old approvals never replayed.

### PostgreSQL and Drizzle

- **Database:** PostgreSQL 17 runs locally with Docker Compose, on a named persistent volume.
- **Schema:** Drizzle's TypeScript schema and generated migrations live in `db/`. `npm run db:generate`
  generates migrations and `npm run db:migrate` applies them.
- **Journal:** the Python side talks to a private Node subprocess over stdin/stdout, and every journal
  read and write goes through Drizzle.
- **Ordering:** a database acknowledgement must come before any goal is sent. A failed or uncertain
  database connection blocks further dispatch until reconciliation.
- **Legacy import:** `scripts/migrate_sqlite.py` performs a one-time transactional import of legacy
  receipts.
