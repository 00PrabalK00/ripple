# Related work, and what Ripple takes from it

Ripple is an always-on site engineer for Nav2 robots. It detects halts, investigates, recovers within bounds,
asks a person when the problem is physical, and turns the answer into site state. This note maps published
work onto the parts of Ripple it informs, including the planned setup, self-configuration and self-improvement.

## LLM agents on ROS

- **ROSA — Robot Operating System Agent (NASA JPL, 2024).** A ReAct agent over ROS 1/2 tools for inspecting,
  diagnosing and operating robots in natural language, with parameter validation and constraints.
  [arXiv 2410.06472](https://arxiv.org/abs/2410.06472) · [github.com/nasa-jpl/rosa](https://github.com/nasa-jpl/rosa)
  *For Ripple:* the same "typed tools over ROS" idea, but Ripple is event-driven (it acts when the robot halts,
  not only when asked). Policy, budgets and verified outcomes are enforced in the edge, not in the prompt.
- **ROSClaw (2026).** A discovery node introspects the ROS 2 graph and publishes a capability manifest (topics,
  services and actions with types) that is injected into the agent prompt. Moving to a new robot needed only a
  new manifest and a per-platform safety allowlist, with no source changes.
  [arXiv 2603.26997](https://arxiv.org/abs/2603.26997)
  *For Ripple:* this is the live half of the planned self-configuring setup. Crawl the graph, match topics by type
  and role, and keep the safety allowlist in the profile, not in the model.
- **bob_llm.** A ROS 2 node that exposes dynamically loaded tools to any OpenAI-compatible model.
  [github.com/bob-ros2/bob_llm](https://github.com/bob-ros2/bob_llm)

## Recovering the configuration of a ROS system

- **ROSDiscover (ICSA 2022) and HAROS.** These recover the run-time architecture statically, from source and
  launch files. They then check it against rules to find misconfigurations such as misrouted or dropped topics,
  a common silent failure caused by late binding.
  [IEEE 9779703](https://ieeexplore.ieee.org/document/9779703/) ·
  [paper PDF](https://squareslab.github.io/materials/Timperley2022ROSDiscover.pdf) ·
  [static ROS graph extraction](https://www.researchgate.net/publication/332076364_Static-Time_Extraction_and_Analysis_of_the_ROS_Computation_Graph)
  *For Ripple:* this is the static half of the crawler (package.xml, launch files, param YAMLs). Its rules are
  the checks to run before a proposed profile is accepted.
- **ros2probe (2026).** Kernel-selective, non-intrusive observability for ROS 2 middleware.
  [arXiv 2606.10746](https://arxiv.org/pdf/2606.10746)
  *For Ripple:* a reference design if RosScope needs a light build. It observes without the ros2 CLI churn
  that loaded the simulator during recordings.

## Runtime monitoring and self-adaptation

- **ROSMonitoring / ROSMonitoring 2.0 and ROSRV.** Runtime verification of ROS topics, and in 2.0 of services
  and message order, against formal properties.
  [ROSMonitoring](https://dl.acm.org/doi/10.1007/978-3-030-63486-5_40) ·
  [2.0: arXiv 2411.14367](https://arxiv.org/abs/2411.14367) ·
  [ROSRV](https://fsl.cs.illinois.edu/publications/huang-erdogan-zhang-moore-luo-sundaresan-rosu-2014-rvtool.pdf) ·
  [survey: RV and field testing for ROS](https://arxiv.org/pdf/2404.11498)
  *For Ripple:* the observer and detector are informal monitors (stalls, holds, lifecycle). Their triggers
  could be stated as monitor properties and tested the same way.
- **MROS: metacontrol and MAPE-K for ROS (2023), AC-ROS.** These reason over a knowledge base (an ontology or an
  assurance case) and reconfigure the ROS node graph in a Monitor-Analyse-Plan-Execute loop over Knowledge.
  [MROS arXiv 2303.09227](https://arxiv.org/pdf/2303.09227) ·
  [AC-ROS](https://www.cse.msu.edu/~mckinley/Pubs/files/Cheng-AC-ROS-MODELS-2020.pdf)
  *For Ripple:* the incident loop is a MAPE-K loop. The observer monitors, the reasoner analyses, the recovery
  ladder plans and the edge executes, with site and incident memory as the knowledge.

## Failure explanation, asking for help, and remote assistance

- **REFLECT (CoRL 2023).** An LLM explains robot failures from a hierarchical summary of multisensory
  experience, and the explanation guides the correction.
  [arXiv 2306.15724](https://arxiv.org/abs/2306.15724)
  *For Ripple:* the incident briefing is a compact experience summary. Summarising closed incidents
  hierarchically is also the input to self-improvement.
- **KnowNo: Robots That Ask For Help (CoRL 2023).** Conformal prediction calibrates an LLM planner so it asks
  a person only when its options are genuinely ambiguous, with statistical guarantees.
  [arXiv 2307.01928](https://arxiv.org/abs/2307.01928)
  *For Ripple:* escalation is currently rule-based (the ladder is exhausted, or the cause is physical). KnowNo is
  a principled way to decide when to ask the engineer rather than retry.
- **Remote assistance for autonomous fleets.** In a minimal-risk stop, an operator gives guidance, not direct
  control, and most stuck vehicles are recovered remotely.
  [overview](https://www.fusioncx.com/blog/high-tech/autonomous-vehicles-robots/remote-assistance-for-autonomous-vehicles-and-drones/) ·
  [mixed-initiative variable autonomy](https://arxiv.org/pdf/1911.04848)
  *For Ripple:* this is the same split Ripple uses. The engineer supplies information ("a pallet is on the dock"),
  and Ripple turns it into keepouts, availability and a new route.

## Self-improvement from a robot's own experience

- **Lifelong Learning for Navigation, LLfN (ICRA 2021).** A robot finds its own sub-optimal actions, retrieves
  similar situations where it did well, and improves in each environment without forgetting earlier ones.
  [arXiv 2007.14486](https://arxiv.org/pdf/2007.14486)
- **APPL: Adaptive Planner Parameter Learning (APPLD / APPLI / APPLE / APPLR).** This family adapts a classical
  planner's parameters per region, learning from demonstrations, corrective interventions, evaluative feedback
  or RL, and keeps the planner's safety and explainability.
  [APPL arXiv 2105.07620](https://arxiv.org/abs/2105.07620) · [APPLI](https://arxiv.org/pdf/2011.00400) ·
  [APPLR](https://arxiv.org/pdf/2011.00397)
  *For Ripple:* this is the closest match to "self-improving for one area". Each teleop unstick or engineer
  reroute is an intervention (APPLI). Learned per-region settings, such as inflation near a shelf corner or a
  preferred via route, are the output. Today's fix, widening inflation after repeated safety stops at shelf
  corners, is the kind of change to learn and then propose, never to apply silently.
- **Reflexion (NeurIPS 2023)** and **Voyager (2023).** Reflexion keeps verbal reflections in episodic memory to
  improve later attempts. Voyager keeps a library of verified, reusable skills.
  [Reflexion arXiv 2303.11366](https://arxiv.org/abs/2303.11366) ·
  [Voyager arXiv 2305.16291](https://arxiv.org/abs/2305.16291)
  *For Ripple:* store per-location lessons, e.g. "the Staging Z3 approach from the east trips the safety zone;
  the via route through (-3.3, -1.2) works". Store verified recoveries as reusable procedures, and retrieve them
  when a new incident happens at the same place.

## Waypoints and replanning in Nav2

- **Nav2 NavigateThroughPoses and RemovePassedGoals.** Native multi-pose navigation that culls passed goals and
  reports per-waypoint status. The default recovery subtree clears costmaps, spins, waits and backs up.
  [Nav2 through-poses BT](https://docs.nav2.org/behavior_trees/trees/nav_through_poses_recovery.html) ·
  [BT walkthrough](https://docs.nav2.org/behavior_trees/overview/detailed_behavior_tree_walkthrough.html)
  *For Ripple:* `navigate_via` drives the legs as separate NavigateToPose goals, so every leg is probed before
  motion and only the final arrival is verified. NavigateThroughPoses is the smoother alternative once a
  profile declares that it has the action.

## Safety of LLM-controlled robots

- **RoboGuard (2025).** A root-of-trust LLM grounds safety rules in the robot's context as temporal-logic
  specifications, and control synthesis resolves them against the proposed plan. Unsafe plans fell from 92% to
  under 2.5%, including under jailbreak attacks.
  [arXiv 2503.07885](https://arxiv.org/abs/2503.07885) · [github.com/KumarRobotics/RoboGuard](https://github.com/KumarRobotics/RoboGuard)
  *For Ripple:* Ripple's edge policy is the deterministic version. Levels, budgets and an operator authorization
  per command are enforced below the model, and message text is treated as data. Setup must keep the safety
  rules in the validated profile, where the model cannot edit them.
