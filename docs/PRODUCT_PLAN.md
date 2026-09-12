# Ripple: always-on site engineer for robots

Scope accepted September 12, 2026: one SMR300 in simulation, with RosScope as the
inspection interface and Ripple as the reasoning, policy and execution layer.
Physical camera/marker work is archived; no D435i prerequisite in simulator mode.
Keep PostgreSQL and Drizzle for durable site and incident memory.

## Build order and acceptance

1. RosScope read-only bridge and continuous observations: expose real process,
   lifecycle and TF observations, collection time, freshness and missing data.
2. Site constraints: map image plus operator text/rectangle → exact preview →
   keepout overlay preserving existing restrictions → observed mask/costmap →
   planning checks. Store author, reason, polygon, creation and expiry metadata.
3. Operational missions: human-approved destinations with current constraints,
   owned Nav2 cancellation and terminal/stop evidence before replacement goals.
4. Autonomous incidents: detect failed actions, collect ROS evidence, bounded
   costmap recovery/retry, and request site context in the dashboard when uncertain.
5. Human-assisted recovery: identify the reported aisle, preview restriction,
   apply, replan and verify actual progress/arrival; retain the incident history.
6. Simulator hero demo: construction restriction, route around it, unexpected
   simulated obstacle, diagnosis, human context, temporary restriction and recovery.

## Policy

Read-only inspection is autonomous. Exact operational changes are validated and
journaled. Ambiguous regions get a visible preview and human resolution. Robot
motion uses the existing approval, freshness and independent stop checks. Do not
expose raw velocity, stop overrides, collision disabling or arbitrary shell tools.
Recovery is bounded; a service response is not evidence of navigation recovery.

RosScope source: https://github.com/00PrabalK00/RosScope at
583ae6743be802f9f8aed1f834d48ca0d7e172ee. The current upstream app has C++ inspection
services but no network agent API. Ripple's headless read-only bridge links those
services without copying them or exposing upstream process termination methods.
