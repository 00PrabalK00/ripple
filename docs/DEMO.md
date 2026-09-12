# Live rehearsal and recording

Current station mapping: Packing A = A1 (3.61, 0.47); Packing B = EXIT BAY (4.21, 2.18).
The left and right physical tabletop markers represent A and B. Only B has a depth prerequisite.

Before recording, start the processes in README, confirm normal zones mode and
inactive stop flags, and reconcile the prior session while stationary. Hold the
camera steady with both markers visible and calibrate the empty B region. Every
camera repositioning requires recalibration. Keep the case ready beside B.

## Complete scenario — still requires two successful rehearsals

1. Ask for a handoff at Packing A. Review the exact target and approve.
2. While A is EXECUTING, enter: “Packing A is being used for inspection now. Use another available handoff station.”
3. Confirm CANCELLING, a real terminal result and stopped odometry. If A finishes before the interpretation, report that honestly and repeat with more travel time; do not relabel completion as cancellation.
4. With B CLEAR, review the repair. Enable the visible dispatch pause and approve B.
5. Immediately put the case on the physical B marker. Keep it there until BLOCKED appears. The approval must expire because the camera version changed, not because its 60-second timer elapsed.
6. Release dispatch pause. Open the rejection receipt and show `send_attempted: false` and the expired proposal ID.
7. Remove the case and wait for stable CLEAR. Review and approve the newly created proposal; the old approval must remain EXPIRED.
8. Keep the camera steady and B clear until Nav2 reports SUCCEEDED and the final pose/stop receipt appears.

Repeat with: “A cannot accept handoffs during inspection; find a currently available alternative.”
The response time of the model can vary. Avoid ending the video on an HTTP success message; show the actual Nav2 terminal outcome.

## Two-minute recording outline

- 0–15s: Explain the problem: operational conditions can invalidate a reachable destination.
- 15–40s: Show A executing, the inspection update and confirmed cancellation.
- 40–65s: Show the B repair, human approval and visible dispatch pause.
- 65–90s: Place the case, show BLOCKED, EXPIRED and rejected dispatch.
- 90–115s: Remove the case, approve the fresh proposal and show actual B arrival.
- 115–120s: Show the receipt and identify the new Ripple layer versus the inherited simulator.

Physical observations and simulated robot motion must be labelled throughout.
The first combined rehearsal passed on 2026-09-12 (receipts 133–167). The raw panel recording is stored locally under `recordings/`. A was canceled quickly, leaving the robot near B; B subsequently returned SUCCEEDED with little travel. A second rehearsal and a two-minute edit remain pending. Individual live receipts
are available in `evidence/`; the camera-expiry receipt predates the switch from B1 to EXIT BAY.
