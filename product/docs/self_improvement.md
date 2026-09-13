# How Ripple improves at one site

Ripple runs one robot in one place for months, so the same trouble comes back: the same shelf corner, the
same dock that gets blocked, the same approach that trips the safety stop. It should get better at that
place without anyone retraining a model. It does this with memory, not weights. Each incident is distilled
into an evidence-bound lesson, and lessons are recalled when a similar problem happens at the same place.

## What the literature says, and what Ripple takes

| Work | Idea | In Ripple |
|---|---|---|
| **ExpeL** (AAAI 2024) [arXiv 2308.10144](https://arxiv.org/abs/2308.10144) | Learn across tasks without parameter updates: collect trajectories, distil natural-language insights by comparing successes with failures, maintain them with ADD / EDIT / UPVOTE / DOWNVOTE, and drop an insight when its importance reaches zero. | A lesson is upvoted each time its recipe precedes a verified recovery and downvoted when it doesn't. Lessons whose weight falls to zero are dropped. |
| **ReasoningBank** (2025) [arXiv 2509.25140](https://arxiv.org/abs/2509.25140) | Memory items (title, description, content) from both successful and failed experiences; retrieve before acting, write back after. | Lessons are written after every closed incident, including failed ones ("clearing the costmap did not help here"), and retrieved when the next incident opens. |
| **Generative Agents** [arXiv 2304.03442](https://arxiv.org/abs/2304.03442) | Retrieval scores recency, importance and relevance; reflection synthesises higher-level conclusions. | Retrieval score = place proximity × situation match × recency decay × reliability. Periodic site reflection turns repeated lessons into suggestions. |
| **CLIN** [arXiv 2310.10134](https://arxiv.org/abs/2310.10134) | Memory of *causal abstractions* ("X is necessary for Y", "X does not contribute to Y"), updated after every trial. | Each lesson says which action helped and which did not, for this situation at this place. |
| **Agent Workflow Memory** (ICML 2025) [arXiv 2409.07429](https://arxiv.org/abs/2409.07429) | Reusable workflows induced from successful trajectories, retrieved as procedural guidance. | A *recipe* is the ordered recovery steps that preceded a verified arrival (e.g. teleop back 0.3 m, then `navigate_via` through remembered points). |
| **Dynamic Cheatsheet** [arXiv 2504.07952](https://arxiv.org/abs/2504.07952) | A small, self-curated memory of transferable snippets, not transcripts. | Lessons are short structured records with at most three briefing lines per incident. |
| **HG-DAgger / APPLI** [ICRA 2019](https://dl.acm.org/doi/10.1109/ICRA.2019.8793698), [arXiv 2011.00400](https://arxiv.org/pdf/2011.00400) | Human interventions are the most informative signal; learn where the human had to step in. | What an allowlisted engineer taught (e.g. "pallet on the dock, send it to the Charger") is kept as a *human-taught* lesson with a higher weight. |
| **Case-based reasoning for navigation** [Autonomous Robots](https://link.springer.com/article/10.1023/A:1020979520454) | A casebase of paths and their traversability improves later planning. | Via routes that worked around a trouble spot are kept and offered for trips that pass it. |
| **Memory poisoning** [arXiv 2601.05504](https://arxiv.org/abs/2601.05504), [arXiv 2606.04329](https://arxiv.org/pdf/2606.04329); **safety of self-evolving agents** [arXiv 2604.16968](https://arxiv.org/pdf/2604.16968) | Persistent memory is an attack surface: entries survive restarts and are trusted as the agent's own experience; aggressive write policies widen it. | See the guardrails below. |

## Design

**Lesson record** (persisted in PostgreSQL through the edge memory):

| Field | Meaning |
|---|---|
| `place` | `{x, y}` in the map frame, plus the named area if any |
| `situation` | trigger, cause, destination |
| `recipe` | recovery steps with their parameters that preceded a verified arrival |
| `unhelpful` | steps tried at this place that did not help |
| `taught` | what an allowlisted engineer said about this place (trimmed), with who |
| `successes`, `failures`, `weight` | outcome counts and an ExpeL-style importance count |
| `evidence` | the incident ids the lesson was built from |
| `first_seen`, `last_seen` | for recency |

**Writing.** When an incident closes, it is matched to an existing lesson within 1.5 m with the same kind of
cause, or becomes a new lesson.
- Resolved on a verified arrival: its recipe is upvoted.
- Escalated or closed without recovery: the steps that ran are marked unhelpful and the lesson is downvoted.

Only edge-verified tool outcomes and the incident's own trigger feed a lesson. The model's prose does not.

**Recall.**
- *During an incident:* when an incident opens, the top lessons within 3 m with a compatible cause are put
  into the model's briefing as data. For example: "Known here: safety_obstacle 3 times; teleop back then
  navigate_via (-3.3,-1.2) → verified arrival 3/3; clearing the costmap did not help 0/2."
- *When commanding a trip:* the operator briefing lists known trouble spots along the way to the requested
  destination.

**Reflection.** `site_report()` aggregates lessons by place. A place with repeated safety stops or
escalations becomes a *suggestion* for a person to approve (a keepout, a slow zone, a station tweak). It is
never applied by Ripple.

**Forgetting.** Weight decays with age. A lesson whose recipe keeps failing loses weight and is dropped at
zero. People can list lessons and forget one through the API.

## Guardrails (from the memory-poisoning work)

1. **Provenance.** Every lesson lists the incidents it came from. Outcomes come from edge-verified results
   (a verified arrival, a tool status), not from what the model wrote.
2. **Only allowlisted people teach.** Taught text comes from incident human messages, which only
   allowlisted operators can produce, and is trimmed.
3. **Lessons are hints, never permissions.** They are shown to the model as data. Every action they suggest
   still passes the edge's policy, budgets and dispatch checks. Nothing in memory can add a tool, raise a
   budget or change safety settings.
4. **Bounded and reviewable.** At most three lessons per briefing and a capped store. Lessons can be listed
   and forgotten by a person.

## Measuring improvement

For each place: incidents, time from detection to verified recovery, escalations, and how many recovery
steps were used. A lesson is working when the second occurrence at a place recovers faster and with fewer
unhelpful steps than the first. The live check reproduces the same stop twice and compares.
