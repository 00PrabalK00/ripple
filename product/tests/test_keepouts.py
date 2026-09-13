import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from ripple_edge.keepouts import Keepouts
from ripple_edge.profile import load_profile
from ripple_edge.site import Site

ROOT = Path(__file__).resolve().parents[1]
SQUARE = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]


def grid(fill=lambda x, y: 0, size=40, res=0.1):
    """An OccupancyGrid-shaped object: 4 m square, origin (0, 0); fill(x, y) gives each cell's value."""
    info = SimpleNamespace(width=size, height=size, resolution=res,
                           origin=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0), orientation=SimpleNamespace(x=0, y=0, z=0, w=1)))
    data = [fill((i % size + .5) * res, (i // size + .5) * res) for i in range(size * size)]
    return SimpleNamespace(info=info, data=data)


def inside_square(x, y):
    return 100 if 1.0 <= x <= 2.0 and 1.0 <= y <= 2.0 else 0


class KeepoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        profile = load_profile(ROOT / 'profiles/smr300.yaml')
        nav = profile.navigation.model_copy(update={'keepout_path': str(Path(self.tmp.name) / 'layers.json')})
        self.profile = profile.model_copy(update={'navigation': nav})
        self.site = Site(self.profile)
        self.k = Keepouts(SimpleNamespace(create_subscription=lambda *a, **kw: None), self.profile, self.site)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_keepout_counts_only_once_mask_and_costmap_show_it(self):
        self.assertEqual(self.k.check(SQUARE, True), (False, 'mask not received'))
        now = time.monotonic()
        self.k.mask, self.k.costmap = (grid(inside_square), now), (grid(), now)
        ok, why = self.k.check(SQUARE, True)
        self.assertFalse(ok)
        self.assertIn('costmap shows 0%', why)
        self.k.costmap = (grid(inside_square), now)
        self.assertEqual(self.k.check(SQUARE, True), (True, 'observed in the keepout mask and global costmap'))

    def test_removal_is_verified_in_the_mask_even_under_another_keepout(self):
        self.k.mask = (grid(), time.monotonic())
        self.assertEqual(self.k.check(SQUARE, False), (True, 'cleared from the keepout mask'))
        self.k.mask = (grid(lambda x, y: 100), time.monotonic())  # another keepout covers the whole area
        bigger = [(0.5, 0.5), (2.5, 0.5), (2.5, 2.5), (0.5, 2.5)]
        ok, why = self.k.check(SQUARE, False, others=[bigger])
        self.assertTrue(ok)
        self.assertIn('still covered by another keepout', why)

    def test_the_layers_file_is_written_atomically_and_validated(self):
        self.k._write({'no_go_zones': [{'ripple_id': 'x'}], 'slow_zones': [{'keep': True}]})
        self.assertEqual(self.k._read()['slow_zones'], [{'keep': True}])
        self.assertFalse(any(p.name.endswith('.tmp') for p in Path(self.tmp.name).iterdir()))
        self.k.path.write_text(json.dumps({'no_go_zones': 'not a list'}))
        with self.assertRaises(RuntimeError):
            self.k._read()

    async def test_apply_waits_for_a_fresh_mask_and_costmap(self):
        task = asyncio.ensure_future(self.k.apply({'id': 'ko-1', 'polygon': SQUARE}))
        await asyncio.sleep(0.3)
        self.assertFalse(task.done())  # nothing fresh yet
        entry = self.k._read()['no_go_zones'][0]
        self.assertEqual(entry['ripple_id'], 'ko-1')
        self.k.mask = (grid(inside_square), time.monotonic())
        self.k.costmap = (grid(inside_square), time.monotonic() + 0.01)
        self.assertEqual(await asyncio.wait_for(task, 5), (True, 'observed in the keepout mask and global costmap'))


if __name__ == '__main__':
    unittest.main()
