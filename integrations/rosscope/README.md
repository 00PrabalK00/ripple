# RosScope bridge

This executable uses the upstream ProcessManager and RosInspector implementations.
It emits one JSON observation and exits. Ripple runs it in a dedicated thread,
retains collection start time and rejects stale snapshots as fresh evidence.

Upstream: https://github.com/00PrabalK00/RosScope
Base revision: 583ae6743be802f9f8aed1f834d48ca0d7e172ee
Local compatibility patch: ../../patches/rosscope-command-timeout.patch

Build with ../../scripts/build_rosscope_bridge.sh. Set ROSSCOPE_SOURCE to the
checkout path and optionally ROSSCOPE_QT_ROOT to a local Qt6 installation prefix.
The normal path uses pkg-config Qt6Core. The workspace used a local extraction of
Ubuntu Qt6 development packages because system packages were not installed.

Only process metadata on an explicit field list leaves RosScope. Command lines,
environments, kill/restart actions, remote commands and arbitrary shell execution
are not part of the bridge protocol. RosScope's process CPU first sample and TF
publisher warnings are heuristic. Raw lifecycle/action summaries are observations,
not independent proof that navigation or TF is healthy. Ripple's owned Nav2 adapter
continues to verify motion events.
