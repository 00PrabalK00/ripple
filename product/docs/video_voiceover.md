# Ripple demo video: voiceover

Eight sections, one audio clip each. The video is cut so each section starts exactly when its clip
starts, and each section's footage is timed to its clip's length. The silent cut runs 3 min 39 s with the
lengths below; if a clip comes out longer or shorter, its section stretches or shrinks to match.

## ElevenLabs

**Voice (Voice Design prompt):**

> A calm, confident narrator in their mid-thirties with a neutral American accent. Warm, clear
> mid-range voice, crisp diction, an unhurried and even pace, friendly but precise, like the narrator
> of a technical product demo. Studio-quality close-mic recording, no background noise, no reverb,
> no music.

**Settings:** Eleven Multilingual v2 · Stability 55% · Similarity 80% · Style 10% · Speaker boost on ·
Speed 1.0.

**How to generate:** paste each section below as its own generation (text only, not the heading), and
download the eight clips as `01_intro.mp3` … `08_close.mp3`. The spellings ("Nav two", "Z three",
"ROS") are written the way they should be spoken.

## Script

### 01_intro (about 14 s)
This is Ripple, an always-on site engineer for Nav two robots. It watches the robot, works out why it
stopped, fixes what it safely can, and asks a person when it can't.

### 02_setup (about 38 s)
Setup is one command. Ripple checks your OpenRouter and Ambiguous keys live, then crawls the robot's
workspace and its running ROS graph. Every topic, action and service is matched by its message type
and by the node behind it, so a robot with different names still gets the right configuration. Ripple
asks only about what it isn't sure of, writes one validated file for this robot, and runs doctor,
which checks every entry against the live robot.

### 03_site_control (about 25 s)
Now the site. An operator closes a walkway on the map. Ripple applies the keepout, and reports success
only once it shows up in Nav two's mask and costmap. Sent to Staging Z three, the robot plans around the
closed walkway, and the arrival counts only when the robot has stopped inside tolerance. Then one
sentence reopens the walkway.

### 04_failure (about 30 s)
Next, something Ripple can't see: a pallet is left on the robot's dock, and the trip home fails. Ripple
opens an incident on its own. It reads Nav two's diagnostics and logs, clears the global costmap, finds
a route and retries. Nav two aborts again. Ripple doesn't keep guessing. It stops and asks the site
engineer on Ambiguous, with what it checked and what it tried.

### 05_human_reply (about 33 s)
The engineer answers on Ambiguous, in plain words: a pallet is on the dock; keep robots out, and go to
the charger. That reply becomes site state. The keepout waits until the robot has driven out of it,
then it's applied and verified. The dock is marked unavailable, the robot drives to the charger, and
the incident closes only on a verified arrival. Asked for a write-up, Ripple files the report straight
into the team's Ambiguous workspace.

### 06_reroute (about 23 s)
Sometimes the direct route is the problem. Asked to avoid a tight spot between the racks, Ripple
chooses its own waypoints on open floor. The edge checks every point and every leg before the robot
moves. One point is too close to a rack, so it's refused, and Ripple adjusts. Only the final arrival
counts.

### 07_self_improvement (about 40 s)
Ripple also learns the place it works. Here, a block stops the robot at the same spot three times. The
first time, its first move is refused by the safety layer, so it backs out, which this simulation
allows, and retries. It records a lesson for that spot: what worked, and what was refused. The next two
times, that lesson is in its briefing, so it skips the refused step and goes straight to what worked.
Lessons come only from verified outcomes, never grant permissions, and people can review or delete
them.

### 08_close (about 16 s)
The model decides; the edge enforces. Every action is typed, checked against policy, journaled and
verified, and on a real robot, its own safety controller always has the last word. Ripple is open
source, at ripple dot prabal khare dot com.
