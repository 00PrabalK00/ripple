# Simulator site control

Use **Site constraints → Preview keepout** with text, or drag a rectangle on the
map before previewing. Text-only previews send the actual map PNG to the configured
OpenRouter vision model. Coordinates use a normalized top-left image origin and
are converted into the registered ROS map frame. Review the highlighted effective
region before pressing **Apply exact keepout**.

Previews include 0.55 m clearance for this simulator's rectangular half-footprint
(0.40 m × 0.25 m) plus map discretization. This is specific to the SMR300 setup and
must change if its footprint changes. The first unbuffered detour test exposed one
smoothed path sample just inside the requested region. Nav2 also documents that
keepout filters need additional clearance/inflation for the robot's extremities:
https://docs.nav2.org/rolling/configuration_and_development/configuration_guide/core_servers/costmap_2d/costmap_filters/keepout_filter/

Changes currently require a confirmed stationary robot and fresh pose. They pause
dispatch and expire pending mission approvals. Existing no-go and speed layers are
preserved in the atomic update to `/home/zuci/map_layers.json`, the inherited zone
publisher's configured file. `RIPPLE_LAYERS_FILE` can change the path, but the zone
publisher must use the same file. This is an extension through the inherited ROS
zone publisher; RosScope supplies observations, not this write operation.

PostgreSQL journals previews, requested changes and completed file updates. A
write with uncertain outcome blocks dispatch, including after restart. Applied
zones are checked against fresh keepout-mask and global-costmap observations before
dispatch is allowed. File acknowledgement alone is never labelled enforcement.
The panel does not claim planner verification from mask/costmap checks.

Read-only planner verification:

```bash
source /opt/ros/humble/setup.bash
.venv/bin/python scripts/probe_path.py --x 3.61 --y 0.47
```

The probe calls ComputePathToPose; it sends no navigation/motion goal. Evidence in
`evidence/site-keepout-planning.json` compares a successful baseline, no path to a
restricted destination and the restored path after removal. Detour evidence is
kept separately, including the failed unbuffered boundary test.

Current scope: indefinite rectangular keepouts and removal by explicit zone ID.
Time-limited requests ask for clarification; slow zones, conversational removal,
constraint updates during motion and autonomous recovery remain pending.


## Visual alignment regression checks

The map now draws the published ROS mask (red), separately from the focused draft
(amber) and current selection (cyan). Applying does not recompute draft geometry.
The canvas preserves its intrinsic aspect ratio at narrow widths so pointer
coordinates cannot include letterboxing. Only the focused draft is highlighted;
old drafts and unrelated test restrictions no longer appear as one implied change.

`node scripts/test_map_alignment.mjs` performs real browser drags, preview/apply,
observed-mask pixel comparison and removal for four corners, including 320-pixel
mobile width. It refuses to run over existing active restrictions. Results:
`evidence/map-ui-alignment.json`; screenshots stay local in
`recordings/alignment-tests/`. A one-cell raster boundary tolerance is explicit.

The moving test around a buffered restriction moved but ended ABORTED. This is
not an arrival pass. See `evidence/navigation-keepout-aborted.json`. Automatic
recovery scaffolding is disabled by default until its live workflow is validated.
