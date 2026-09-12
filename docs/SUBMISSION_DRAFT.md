# Ripple for Robotics

Ripple keeps a robot mission aligned with changing operational conditions.
An operator can report that a station is unavailable; Ripple interprets the
update, identifies affected work and proposes a new destination. Its deterministic
supervisor owns the Nav2 action, checks live dependencies and requires a fresh
human approval before executing a repair.

The central interaction is an approved repair becoming obsolete while queued.
A physical D435i depth region represents Packing B availability. When the region
becomes blocked, the old approval expires. Clearing the region permits a new
proposal but never restores the old approval. PostgreSQL receipts record the
versions checked, rejected sends and actual robot action outcomes.

New implementation: mission/approval supervisor, OpenRouter interpretation,
depth detector, operator panel, Drizzle/PostgreSQL journal and ROS adapter.
Reused environment: SMR300L Gazebo ROS2 Control, providing the simulated robot,
warehouse, navigation and independent safety controller. See README for the
upstream link, local fix and setup.

Repository: https://github.com/00PrabalK00/ripple

Before submission: verify inherited revision, complete two combined rehearsals,
record the two-minute video and add its link. Do not submit this draft as evidence
that the complete combined demo has already passed.
