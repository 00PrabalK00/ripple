# Ripple for Robotics

A local mission supervisor for one SMR300 simulated robot. Ripple tracks station
availability, proposes exact destinations, requires single-use approval, and
rejects work when its evidence changes. Physical depth observations represent
Packing B availability; robot motion remains in Gazebo.

## Simulator used

Ripple was developed against [SMR300L Gazebo ROS2 Control](https://github.com/00PrabalK00/smr300l_gazebo_ros2control), an existing ROS 2 Humble / Gazebo Classic / Nav2 simulator. It supplies the robot, warehouse, maps, zones, controllers and independent safety controller. It is an external dependency, **not Ripple's new contribution**, and its checkout is excluded from this repository.

Place that simulator checkout at `smr300l_gazebo_ros2control/` beside this README. The supplied local snapshot's exact upstream revision has not yet been verified. The speed-unit correction is provided separately in `patches/smr300-speed-units.patch`; apply it from the simulator checkout with `patch -p1 < ../patches/smr300-speed-units.patch` if that correction is not already present.

## Setup

Use Ubuntu 22.04 with ROS Humble and the simulator's dependencies installed. The local warehouse assets are currently expected at `/opt/ros/humble/share/bcr_bot/models`; adjust `scripts/start_sim.sh` for another installation.

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
npm ci
cp .env.example .env
# Set OPENROUTER_API_KEY and a matching local DB password/URL in .env.
docker compose up -d --wait postgres
npm run db:migrate
source /opt/ros/humble/setup.bash
colcon build --base-paths smr300l_gazebo_ros2control smr300l_gazebo_ros2control/src --symlink-install --cmake-args -DBUILD_TESTING=OFF
```

## Run

ROS Humble and the inherited six packages are built in this workspace.

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

Open http://127.0.0.1:8050. Do not start duplicate instances of these processes.
The navigation launcher initializes AMCL at the simulator's initial (0,0) spawn;
do not rerun it after moving without supplying a correct initial pose.

Put `OPENROUTER_API_KEY` in `.env` locally. `.env.example` documents the settings.
The configured agent is `z-ai/glm-5.3`; on-demand vision uses
`z-ai/glm-5.3-flash`. Restart Ripple after changing credentials. Model calls are
bounded, run outside the supervisor loop, and cannot approve or dispatch goals.
Output is validated locally. A real GLM 5.3 request successfully returned a validated Packing A proposal using the configured key.
[OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)

## Camera

Connect a D435i and aim it down at a small tabletop region. A fixed support is preferable. Handheld testing is supported procedurally: brace your arms, hold still for the entire empty/box/empty sequence, and recalibrate after every repositioning. Returning to roughly the same view is not enough to preserve the baseline. Camera motion is not automatically detected by this prototype. In the
panel, set the region as x,y,width,height in the 640x480 image, clear it, and
click Calibrate empty region. Wait for ten valid clear frames. Adding a box
at least 3 cm closer over 10% of the region for five frames reports BLOCKED.
Invalid depth or a one-second stale stream yields UNKNOWN. Calibration is
session-local and reconnecting requires recalibration. Vision descriptions are
on demand and do not authorize movement.

## Verification status

```bash
.venv/bin/python -m unittest discover -s tests -v
source /opt/ros/humble/setup.bash
python3 scripts/verify_nav2.py             # sends and cancels an actual simulated goal
python3 scripts/verify_nav2.py --complete  # attempts arrival
```

Six ROS packages build. Twenty focused tests pass, including two against a separate local PostgreSQL test database (source ROS Humble to include the runtime regression test).

Verified live results:
- An owned goal moved, returned CANCELED, and produced fresh settled odometry.
- Packing A=A1 (3.61,0.47) and Packing B=EXIT BAY (4.21,2.18) were reached with Nav2 SUCCEEDED. Both travel directions passed.
- The handheld D435i observed CLEAR → BLOCKED → CLEAR around the right-hand physical B marker.
- A human-approved B proposal expired when camera.B changed from version 2 to 3. Releasing dispatch yielded a rejection with `send_attempted=false`.
- GLM 5.3 passed both inspection-update wordings, irrelevant input and ambiguous input checks. GLM 5.3 Flash returned a live scene description.

Machine-readable examples are in `evidence/`. The complete combined scenario still needs two rehearsals on the final station pair; no full demo/video success is claimed yet.

The original B1 route failed due to the independent rear-obstacle stop and was replaced with the verified EXIT BAY zone. The safety controller was not overridden. A local action acknowledgement timeout was increased from 20 ms to 1000 ms after observed failures. AMCL receives stationary update requests for real fresh laser-based localization.

Restart holds for operator reconciliation; old approvals never replay. Use Reconcile previous session only after the prior goal is resolved and the robot is stopped. One mission owner is required. Model descriptions do not prove motion or arrival; only adapter outcomes do.

## PostgreSQL and Drizzle

PostgreSQL 17 runs locally via Docker Compose on `127.0.0.1:5433`, with a named persistent volume. Drizzle's TypeScript schema and generated migrations live in `db/`. The Python supervisor communicates with a private Node subprocess over stdin/stdout; all production journal reads and writes use Drizzle. A database acknowledgement must precede any goal send. A failed/uncertain database connection blocks further dispatch until reconciliation.

`npm run db:generate` generates schema migrations; `npm run db:migrate` applies them. Credentials stay in the ignored `.env`. `scripts/migrate_sqlite.py` performs a one-time transactional import of legacy receipts and leaves the old SQLite file unchanged; new writes use PostgreSQL only. The development machine imported 34 receipts.

The normal unit suite uses an explicit in-memory test adapter. To run the two database integration tests, migrate a separate test database and set `RIPPLE_TEST_DATABASE_URL` to its URL before running unittest. They verify persistence across reconnection and zero goal sends after database disconnection.

## Reuse and changes

`smr300l_gazebo_ros2control/` is the supplied pre-existing robot environment.
New supervisor code is in `ripple/` and local launch wrappers in `scripts/`.
The only inherited source correction so far changes the speed filter info type
from percentage (1) to absolute m/s (2), matching its existing conversion.
Installed missing system package: `ros-humble-gazebo-ros2-control`.
The exact inherited revision/local differences still need verification before
submission. See [TODO.md](TODO.md) for remaining work.
