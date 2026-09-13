"""Site memory: Ripple gets better at the places it works.

Each closed incident is distilled into an evidence-bound *lesson* for that place: the situation (what
went wrong), the recovery *recipe* that preceded a verified arrival, the steps that did not help, and
what an allowlisted engineer taught. When trouble happens again near the same spot, the most relevant
lessons are put into the model's briefing, so it tries what worked there first.

The approach follows ExpeL (upvote/downvote insights, drop them at zero importance), ReasoningBank
(learn from failures as well as successes), CLIN (causal "helped / did not help" abstractions), Agent
Workflow Memory (reusable recipes), and Generative Agents (retrieval by relevance, recency and
importance). Guardrails follow the memory-poisoning literature: lessons are built only from
edge-verified outcomes and allowlisted people, carry their evidence, are shown to the model as data,
never grant anything, and can be reviewed and forgotten. See product/docs/self_improvement.md.
"""
import math
import time
from uuid import uuid4

SAME_PLACE_M = 1.5       # incidents this close are the same place
RECALL_M = 3.0           # lessons this close are relevant
HALF_LIFE_DAYS = 30.0    # recency decay
MAX_LESSONS = 300
MAX_WEIGHT = 10
# Steps worth remembering: recovery and the commands an engineer's reply turns into.
STEPS = ('clear_costmap', 'teleop', 'escape', 'retry_navigation', 'navigate_via', 'lifecycle_reset',
         'navigate_to', 'add_keepout', 'set_station_availability')
FAMILY = {'safety_obstacle': 'blocked by an obstacle', 'nav2_stall': 'stalled', 'component_down': 'a Nav2 component down',
          'localization_stop': 'lost localization', 'emergency_stop': 'an emergency stop', 'manual_control': 'manual control'}
TRIGGER_FAMILY = {'navigation_failed': 'navigation failed', 'halted_while_commanded': 'stalled', 'no_progress': 'stalled',
                  'localization_degraded': 'lost localization', 'node_not_active': 'a Nav2 component down'}


def family(trigger, cause):
    return FAMILY.get(cause) or TRIGGER_FAMILY.get(trigger) or 'trouble'


def step(action):
    """One recovery step as a short, comparable label, e.g. 'teleop backward' or 'navigate_via (-3.3,-1.2)'."""
    tool, args = action['tool'], action.get('args') or {}
    if tool == 'navigate_via':
        points = ' → '.join(f"({p.get('x', 0):.1f},{p.get('y', 0):.1f})" for p in args.get('via') or [] if isinstance(p, dict))
        return f'navigate_via {points}'.strip()
    detail = {'teleop': args.get('direction'), 'escape': args.get('primitive'), 'clear_costmap': args.get('costmap'),
              'lifecycle_reset': args.get('node'), 'navigate_to': args.get('destination'),
              'set_station_availability': args.get('destination')}.get(tool)
    return f'{tool} {detail}' if detail else tool


class SiteMemory:
    def __init__(self, store=None, clock=time.time):
        self.lessons = {}
        self.store = store or (lambda kind, key, data: None)
        self.clock = clock

    # ----- persistence
    def load(self, rows):
        for row in rows:
            data = row.get('data', row)
            if data.get('id') and not data.get('forgotten'):
                self.lessons[data['id']] = data

    def _save(self, lesson):
        self.store('lesson', lesson['id'], lesson)

    # ----- writing
    def match(self, x, y, fam):
        near = [(math.dist((x, y), (l['place']['x'], l['place']['y'])), l) for l in self.lessons.values()
                if l['situation']['family'] == fam]
        near = [(d, l) for d, l in near if d <= SAME_PLACE_M]
        return min(near, key=lambda n: n[0])[1] if near else None

    def record(self, inc):
        """Distil a closed incident into its place's lesson. Returns the lesson, or None when there is nothing to learn."""
        loc = inc.get('location')
        if not loc or inc.get('state') not in ('RESOLVED', 'CLOSED', 'INTERRUPTED'):
            return None
        trigger = inc.get('trigger') or {}
        fam = family(trigger.get('kind'), trigger.get('cause'))
        # Only what the edge reported as done counts: a step the model proposed but the edge denied never ran.
        done = []
        for a in inc.get('actions') or []:
            if a.get('tool') in STEPS and a.get('status') == 'ok':
                label = step(a)
                if label not in done:
                    done.append(label)
        taught = [{'from': h.get('from'), 'text': ' '.join(str(h.get('text', '')).split())[:200]}
                  for h in inc.get('human_messages') or [] if h.get('text')]
        success = inc.get('state') == 'RESOLVED' and bool(inc.get('verified_arrival'))
        if not done and not taught and not success:
            return None
        now = self.clock()
        x, y = float(loc[0]), float(loc[1])
        lesson = self.match(x, y, fam)
        if lesson is None:
            lesson = {'id': 'lesson-' + uuid4().hex[:8], 'place': {'x': round(x, 2), 'y': round(y, 2)},
                      'situation': {'family': fam, 'trigger': trigger.get('kind'), 'cause': trigger.get('cause'),
                                    'destinations': []},
                      'recipes': {}, 'unhelpful': {}, 'taught': [], 'successes': 0, 'failures': 0, 'weight': 2,
                      'evidence': [], 'first_seen': now, 'last_seen': now}
            self.lessons[lesson['id']] = lesson
        else:  # the place drifts toward where the trouble keeps happening
            n = lesson['successes'] + lesson['failures'] + 1
            lesson['place'] = {'x': round((lesson['place']['x'] * n + x) / (n + 1), 2),
                               'y': round((lesson['place']['y'] * n + y) / (n + 1), 2)}
        dest = (inc.get('goal') or {}).get('label')
        if dest and dest not in lesson['situation']['destinations']:
            lesson['situation']['destinations'] = (lesson['situation']['destinations'] + [dest])[-5:]
        if success:
            recipe = ' → '.join(done) or 'retried as is'
            lesson['recipes'][recipe] = lesson['recipes'].get(recipe, 0) + 1
            lesson['successes'] += 1
            lesson['weight'] = min(MAX_WEIGHT, lesson['weight'] + 1)
        else:
            for label in done:
                lesson['unhelpful'][label] = lesson['unhelpful'].get(label, 0) + 1
            lesson['failures'] += 1
            lesson['weight'] -= 1
        if taught:  # an engineer's word about this place is the most valuable signal
            lesson['taught'] = (lesson['taught'] + taught)[-3:]
            lesson['weight'] = min(MAX_WEIGHT, lesson['weight'] + 1)
        lesson['evidence'] = (lesson['evidence'] + [inc['id']])[-10:]
        lesson['last_seen'] = now
        if lesson['weight'] <= 0 and not lesson['taught']:
            self.forget(lesson['id'])
            return None
        self._trim()
        self._save(lesson)
        return lesson

    def _trim(self):
        if len(self.lessons) > MAX_LESSONS:
            for lesson in sorted(self.lessons.values(), key=self.value)[:len(self.lessons) - MAX_LESSONS]:
                self.forget(lesson['id'])

    def forget(self, lesson_id):
        lesson = self.lessons.pop(lesson_id, None)
        if lesson:
            self.store('lesson', lesson_id, {**lesson, 'forgotten': True})
        return lesson is not None

    # ----- recall
    def value(self, lesson):
        """Importance: reliability of what worked here, plus human teaching, faded by age."""
        age_days = max(0.0, (self.clock() - lesson['last_seen']) / 86400)
        reliability = (lesson['successes'] + 1) / (lesson['successes'] + lesson['failures'] + 2)
        return (reliability + 0.25 * bool(lesson['taught'])) * lesson['weight'] / MAX_WEIGHT * 0.5 ** (age_days / HALF_LIFE_DAYS)

    def recall(self, loc, trigger=None, cause=None, k=3):
        if not loc:
            return []
        fam = family(trigger, cause) if trigger or cause else None
        scored = []
        for lesson in self.lessons.values():
            d = math.dist((float(loc[0]), float(loc[1])), (lesson['place']['x'], lesson['place']['y']))
            if d > RECALL_M:
                continue
            same = 1.0 if fam is None or fam == lesson['situation']['family'] else 0.4
            scored.append(((1 - d / (RECALL_M * 1.5)) * same * self.value(lesson), lesson))
        return [lesson for _, lesson in sorted(scored, key=lambda s: -s[0])[:k]]

    def describe(self, lesson):
        s = lesson['situation']
        n = lesson['successes'] + lesson['failures']
        where = f"near ({lesson['place']['x']:.1f}, {lesson['place']['y']:.1f})"
        dests = f" on the way to {', '.join(s['destinations'])}" if s['destinations'] else ''
        parts = [f"{where}: {s['family']} {n} time{'s' if n != 1 else ''}{dests}."]
        if lesson['recipes']:
            recipe, count = max(lesson['recipes'].items(), key=lambda r: r[1])
            parts.append(f'Worked: {recipe} ({count} of {n}).')
        unhelpful = [f'{k} ({v}×)' for k, v in sorted(lesson['unhelpful'].items(), key=lambda u: -u[1])[:3]]
        if unhelpful:
            parts.append('Did not help: ' + ', '.join(unhelpful) + '.')
        if lesson['taught']:
            t = lesson['taught'][-1]
            parts.append(f"{t['from'] or 'The engineer'} said: “{t['text']}”.")
        parts.append('Evidence: ' + ', '.join(lesson['evidence'][-3:]) + '.')
        return ' '.join(parts)

    def trouble_spots(self, k=3):
        """Places where trouble keeps happening, for the operator's briefing."""
        return sorted(self.lessons.values(), key=lambda l: -(l['successes'] + l['failures']) * self.value(l))[:k]

    # ----- reflection
    def report(self):
        """Suggestions for a person to approve. Ripple never applies these itself."""
        out = []
        for lesson in sorted(self.lessons.values(), key=lambda l: -(l['successes'] + l['failures'])):
            n = lesson['successes'] + lesson['failures']
            where = f"({lesson['place']['x']:.1f}, {lesson['place']['y']:.1f})"
            if lesson['situation']['family'] == 'blocked by an obstacle' and n >= 3:
                out.append({'lesson': lesson['id'], 'suggestion': f'The robot was stopped for an obstacle {n} times near {where}. '
                            'Consider a keepout or a slow zone there, or moving what keeps blocking it.'})
            elif lesson['failures'] >= 2 and lesson['failures'] > lesson['successes']:
                out.append({'lesson': lesson['id'], 'suggestion': f"Recovery near {where} failed {lesson['failures']} of {n} times "
                            f"({lesson['situation']['family']}). This place needs a person's attention."})
        return out

    def public(self):
        return sorted(({**l, 'summary': self.describe(l)} for l in self.lessons.values()), key=lambda l: -l['last_seen'])
