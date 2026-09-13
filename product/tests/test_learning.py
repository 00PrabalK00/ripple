import unittest
from ripple_agent.learning import SiteMemory, step


def incident(iid, loc, state='RESOLVED', verified=True, actions=(), human=(), cause='safety_obstacle',
             trigger='halted_while_commanded', dest='Staging Z3'):
    return {'id': iid, 'state': state, 'verified_arrival': verified, 'location': list(loc),
            'trigger': {'kind': trigger, 'cause': cause}, 'goal': {'label': dest},
            'actions': [dict(a) for a in actions], 'human_messages': [dict(h) for h in human]}


TELEOP = {'tool': 'teleop', 'status': 'ok', 'args': {'direction': 'backward'}}
VIA = {'tool': 'navigate_via', 'status': 'ok', 'args': {'via': [{'x': -3.3, 'y': -1.2}]}}
CLEAR = {'tool': 'clear_costmap', 'status': 'ok', 'args': {'costmap': 'local'}}
DENIED_ESCAPE = {'tool': 'escape', 'status': 'denied', 'args': {'primitive': 'backup'}}


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.now = [1_000_000.0]
        self.saved = []
        self.m = SiteMemory(store=lambda kind, key, data: self.saved.append((kind, key, data)), clock=lambda: self.now[0])

    def test_a_verified_recovery_becomes_a_recipe_and_repeats_upvote_it(self):
        a = self.m.record(incident('inc-1', (-2.6, 1.0), actions=[TELEOP, VIA]))
        b = self.m.record(incident('inc-2', (-2.4, 1.1), actions=[TELEOP, VIA]))
        self.assertIs(a, b)  # same place, same kind of trouble: one lesson
        self.assertEqual(a['recipes'], {'teleop backward → navigate_via (-3.3,-1.2)': 2})
        self.assertEqual((a['successes'], a['failures'], a['evidence']), (2, 0, ['inc-1', 'inc-2']))
        self.assertEqual(self.saved[-1][:2], ('lesson', a['id']))

    def test_steps_that_did_not_help_are_remembered_and_denied_steps_never_count(self):
        lesson = self.m.record(incident('inc-1', (0, 0), state='CLOSED', verified=False, actions=[CLEAR, DENIED_ESCAPE, TELEOP]))
        self.assertEqual(lesson['unhelpful'], {'clear_costmap local': 1, 'teleop backward': 1})
        self.assertEqual(lesson['failures'], 1)

    def test_different_places_and_kinds_of_trouble_stay_separate(self):
        a = self.m.record(incident('inc-1', (0, 0), actions=[TELEOP]))
        b = self.m.record(incident('inc-2', (5, 0), actions=[TELEOP]))
        c = self.m.record(incident('inc-3', (0, 0), actions=[CLEAR], cause='component_down', trigger='node_not_active'))
        self.assertEqual(len({a['id'], b['id'], c['id']}), 3)

    def test_recall_prefers_near_same_situation_reliable_and_recent(self):
        near = self.m.record(incident('inc-1', (0, 0), actions=[TELEOP]))
        self.m.record(incident('inc-2', (2.5, 0), actions=[TELEOP]))
        other_kind = self.m.record(incident('inc-3', (0.2, 0), actions=[CLEAR], cause='component_down', trigger='node_not_active'))
        self.m.record(incident('inc-4', (10, 10), actions=[TELEOP]))  # too far to be relevant
        got = self.m.recall((0.1, 0), 'halted_while_commanded', 'safety_obstacle')
        self.assertEqual(got[0]['id'], near['id'])
        self.assertEqual(len(got), 3)
        self.assertEqual(got[-1]['id'], other_kind['id'])
        self.now[0] += 400 * 86400  # a year later, old lessons fade but are still ranked
        self.assertEqual(self.m.recall((0.1, 0), 'halted_while_commanded', 'safety_obstacle')[0]['id'], near['id'])

    def test_what_an_engineer_taught_is_kept_trimmed_and_weighted(self):
        lesson = self.m.record(incident('inc-1', (1, 1), state='CLOSED', verified=False,
                                        human=[{'from': 'Prabal', 'text': 'pallet on the dock   ' + 'x' * 400}]))
        self.assertEqual(lesson['taught'][0]['from'], 'Prabal')
        self.assertLessEqual(len(lesson['taught'][0]['text']), 200)
        self.assertIn('Prabal said', self.m.describe(lesson))

    def test_a_lesson_whose_recipe_keeps_failing_is_dropped(self):
        lesson = self.m.record(incident('inc-1', (0, 0), state='CLOSED', verified=False, actions=[CLEAR]))
        self.m.record(incident('inc-2', (0, 0), state='CLOSED', verified=False, actions=[CLEAR]))
        self.assertNotIn(lesson['id'], self.m.lessons)
        self.assertTrue(self.saved[-1][2]['forgotten'])

    def test_nothing_is_learned_from_an_incident_without_place_or_outcome(self):
        self.assertIsNone(self.m.record(incident('inc-1', (0, 0), state='ESCALATED')))
        self.assertIsNone(self.m.record({**incident('inc-2', (0, 0)), 'location': None}))
        self.assertIsNone(self.m.record(incident('inc-3', (0, 0), state='CLOSED', verified=False)))

    def test_describe_report_and_round_trip(self):
        for i in range(3):
            self.m.record(incident(f'inc-{i}', (-2.6, 1.0), actions=[TELEOP, VIA]))
        lesson = next(iter(self.m.lessons.values()))
        text = self.m.describe(lesson)
        self.assertIn('blocked by an obstacle 3 times on the way to Staging Z3', text)
        self.assertIn('Worked: teleop backward → navigate_via (-3.3,-1.2) (3 of 3)', text)
        self.assertIn('keepout or a slow zone', self.m.report()[0]['suggestion'])
        restored = SiteMemory()
        restored.load([{'data': data} for _, _, data in self.saved])
        self.assertEqual(restored.lessons[lesson['id']]['successes'], 3)
        self.assertTrue(restored.forget(lesson['id']))
        self.assertEqual(restored.lessons, {})

    def test_step_labels(self):
        self.assertEqual(step({'tool': 'retry_navigation'}), 'retry_navigation')
        self.assertEqual(step({'tool': 'escape', 'args': {'primitive': 'spin'}}), 'escape spin')


if __name__ == '__main__':
    unittest.main()
