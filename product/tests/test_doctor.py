import json
import tempfile
import unittest
from pathlib import Path
import yaml
import ripple_edge.crawl as crawl
from ripple_agent import doctor

ROOT = Path(__file__).resolve().parents[1]


def graph_for(profile):
    """A live graph that has everything the profile names."""
    scan = {'nodes': ['/bt_navigator'], 'topics': {}, 'services': {}, 'actions': {}, 'params': {}, 'safety_text': {}}
    for label, name, kind, _ in doctor.role_checks(profile):
        if kind == 'topic':
            scan['topics'][name] = {'types': [], 'publishers': ['/someone'], 'subscribers': []}
        elif kind == 'action':
            scan['actions'][name] = {'types': [], 'servers': ['/server'], 'clients': []}
        else:
            scan['services'][name] = []
    return scan


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.profile = yaml.safe_load((ROOT / 'profiles/smr300.yaml').read_text())
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name, 'ripple.json')
        self.site.write_text(json.dumps({'schema': 1, 'profile': self.profile}))
        self.real_scan = crawl.live_scan

    def tearDown(self):
        crawl.live_scan = self.real_scan
        self.tmp.cleanup()

    def run_with(self, scan):
        crawl.live_scan = lambda: scan
        return {name: (ok, detail) for ok, name, detail in doctor.run(self.site, self.tmp.name, live=True, keys=False)}

    def test_every_role_is_checked_and_passes_when_present(self):
        checks = doctor.role_checks(self.profile)
        self.assertIn(('odometry', '/diff_cont/odom', 'topic', True), checks)
        self.assertIn(('escape backup', '/backup', 'action', True), checks)
        results = self.run_with(graph_for(self.profile))
        failed = [n for n, (ok, _) in results.items() if ok is False]
        self.assertEqual(failed, [])
        self.assertTrue(results['schema'][0])

    def test_a_missing_topic_or_a_topic_without_publisher_fails(self):
        scan = graph_for(self.profile)
        del scan['topics']['/diff_cont/odom']
        scan['topics']['/amcl_pose']['publishers'] = []
        results = self.run_with(scan)
        self.assertEqual(results['odometry /diff_cont/odom'], (False, 'missing from the graph'))
        self.assertEqual(results['localization /amcl_pose'], (False, 'no publisher'))

    def test_an_invalid_profile_stops_at_the_schema(self):
        self.profile['teleop']['override_safety'] = True
        self.profile['simulation'] = False
        self.site.write_text(json.dumps({'schema': 1, 'profile': self.profile}))
        results = doctor.run(self.site, self.tmp.name, live=False, keys=False)
        self.assertEqual([(ok, name) for ok, name, _ in results], [(False, 'schema')])


if __name__ == '__main__':
    unittest.main()
