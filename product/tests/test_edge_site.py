import math
import unittest
from pathlib import Path
from ripple_edge.authorization import Authorizations
from ripple_edge.geometry import MapGeometry, Region, distance, inside, interior_cells, layers_entry
from ripple_edge.profile import load_profile
from ripple_edge.site import Site
from ripple_edge.tools import EdgeTools

ROOT = Path(__file__).resolve().parents[1]


class GeometryTests(unittest.TestCase):
    def test_unrotated_image_rectangle_becomes_map_rectangle(self):
        g = MapGeometry(280, 419, .05, -6.99, -10.4, 0.0)
        region = Region.from_image(g, [.5, .25, .75, .5])
        poly = region.polygon(g)
        entry = layers_entry(poly, 'ko-1')
        self.assertEqual(entry['type'], 'rectangle')
        self.assertAlmostEqual(entry['x1'], -6.99 + 140 * .05)
        self.assertAlmostEqual(entry['y2'], -10.4 + .75 * 419 * .05)
        buffered = region.buffered(g, .55).polygon(g)
        self.assertAlmostEqual(min(p[0] for p in buffered), entry['x1'] - .55)

    def test_rotated_map_round_trip_and_polygon_output(self):
        g = MapGeometry(200, 100, .1, 2.0, -1.0, math.pi / 2)
        for u, v in [(0, 0), (.3, .7), (1, 1)]:
            x, y = g.image_to_map(u, v)
            back = g.map_to_image(x, y)
            self.assertAlmostEqual(back[0], u)
            self.assertAlmostEqual(back[1], v)
        # grid +x points along map +y when the map is rotated 90 degrees
        self.assertEqual(tuple(round(c, 6) for c in g.grid_to_map(10, 0)), (2.0, 0.0))
        poly = Region.from_image(g, [.1, .1, .3, .4]).polygon(g)
        self.assertEqual(layers_entry(poly, 'r')['type'], 'rectangle')  # still map-aligned at 90 degrees
        g45 = MapGeometry(200, 100, .1, 0, 0, math.pi / 4)
        self.assertEqual(layers_entry(Region.from_image(g45, [.1, .1, .3, .4]).polygon(g45), 'r')['type'], 'polygon')

    def test_interior_cells_respect_rotation_and_margin(self):
        g = MapGeometry(100, 100, .1, 0, 0, math.pi / 6)
        poly = Region('grid', 20, 20, 40, 30).polygon(g)
        cells = interior_cells(g, poly)
        self.assertGreater(len(cells), 100)
        self.assertLess(len(cells), 200)  # 20x10 cells, minus a one-cell margin
        for i in cells:
            self.assertTrue(20 <= i % 100 < 40 and 20 <= i // 100 < 30)

    def test_distance_and_inside(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.assertTrue(inside((.5, .5), sq))
        self.assertEqual(distance((.5, .5), sq), 0)
        self.assertAlmostEqual(distance((2, .5), sq), 1)

    def test_invalid_regions_are_rejected(self):
        g = MapGeometry(100, 100, .1, 0, 0)
        for bad in ([.5, .5, .5, .6], [-.1, 0, .2, .2], [0, 0, .01, .01]):
            with self.assertRaises(ValueError):
                Region.from_image(g, bad)
        with self.assertRaises(ValueError):
            Region.from_box([0, 0, .05, 1])


class AuthorizationTests(unittest.TestCase):
    def test_only_allowlisted_operators_and_bounded_use(self):
        now = [0.0]
        auths = Authorizations({'u1': 'Prabal'}, ttl_s=600, max_uses=2, clock=lambda: now[0])
        self.assertIsNone(auths.record('ambiguous', 'stranger', 'go to B'))
        a = auths.record('ambiguous', 'u1', 'send the robot to B', 'm1')
        self.assertIsNone(auths.check(a.id, 'navigate'))
        auths.consume(a.id, 'navigate')
        auths.consume(a.id, 'keepout')
        self.assertEqual(auths.check(a.id, 'navigate'), 'authorization_used_up')
        b = auths.record('ambiguous', 'u1', 'block aisle 2')
        now[0] = 601
        self.assertEqual(auths.check(b.id, 'keepout'), 'authorization_expired')
        self.assertEqual(auths.check('auth-made-up', 'navigate'), 'unknown_authorization')
        with self.assertRaises(PermissionError):
            auths.consume(b.id, 'keepout')


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(ROOT / 'profiles/smr300.yaml')
        self.saved = []
        self.site = Site(self.profile, store=lambda *r: self.saved.append(r))
        self.g = MapGeometry(280, 419, .05, -6.99, -10.4)

    def test_resolution_by_key_label_alias_and_area(self):
        key, dest = self.site.resolve('b')
        self.assertEqual(key, 'B')
        self.site.define_area('Loading Dock', Region.from_image(self.g, [.2, .2, .3, .3]), 'Prabal')
        key, dest = self.site.resolve('loading  dock', self.g)
        self.assertEqual((key, dest['kind']), ('Loading Dock', 'area'))
        self.assertEqual(self.saved[-1][0], 'area')
        with self.assertRaises(ValueError):
            self.site.define_area('B', Region.from_image(self.g, [.2, .2, .3, .3]), 'Prabal')

    def test_availability_is_persisted_and_reported(self):
        self.site.set_available('A', False, 'under inspection', 'Prabal')
        self.assertFalse(self.site.destinations()['A']['available'])
        restored = Site(self.profile)
        restored.load([(k, key, data) for k, key, data in self.saved])
        self.assertEqual(restored.destinations()['A']['unavailable_reason'], 'under inspection')
        self.site.set_available('A', True, 'inspection done', 'Prabal')
        self.assertTrue(self.site.destinations()['A']['available'])

    def test_a_station_inside_an_active_keepout_is_unavailable(self):
        home = self.profile.stations['HOME']
        box = [(home.x - .5, home.y - .5), (home.x + .5, home.y - .5), (home.x + .5, home.y + .5), (home.x - .5, home.y + .5)]
        self.site.save_keepout(dict(id='ko-1', name='Pallet', state='APPLIED', polygon=box))
        self.assertEqual((self.site.destinations()['HOME']['available'], self.site.destinations()['HOME']['unavailable_reason']),
                         (False, 'inside keepout Pallet'))
        self.assertTrue(self.site.destinations()['A']['available'])
        self.site.save_keepout(dict(id='ko-1', name='Pallet', state='REMOVED', polygon=box))
        self.assertTrue(self.site.destinations()['HOME']['available'])


class ToolSchemaTests(unittest.TestCase):
    def test_definitions_are_flat_json_schema(self):
        import json
        tools = EdgeTools(rt=None)
        text = json.dumps(tools.definitions())
        self.assertNotIn('$ref', text)
        self.assertNotIn('$defs', text)
        names = {d['function']['name'] for d in tools.definitions()}
        self.assertTrue({'navigate_to', 'add_keepout', 'escape', 'cancel_navigation'} <= names)
        self.assertFalse(any(word in text for word in ('cmd_vel', 'shell')))
        # The only safety override is teleop's, and profiles may enable it on simulations only.
        import yaml
        from ripple_edge.contracts import Profile
        data = yaml.safe_load((ROOT / 'profiles/smr300.yaml').read_text())
        data['simulation'] = False
        with self.assertRaises(Exception):
            Profile.model_validate(data)


if __name__ == '__main__':
    unittest.main()
