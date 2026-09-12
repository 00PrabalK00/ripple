import json
from pathlib import Path
import tempfile
import unittest
from ripple.site import SiteMemory, rectangle, buffered_bounds
from ripple.store import Store

class SiteTests(unittest.TestCase):
    def test_image_y_is_inverted_and_origin_respected(self):
        r=rectangle([0,0,.5,.5],100,200,.1,[-5,-10,0])
        self.assertEqual((r['x1'],r['x2'],r['y1'],r['y2']),(-5,0,0,10))
        for bounds in ([0,0,float('nan'),1],[-.1,0,1,1],[.5,0,.4,1]):
            with self.assertRaises(ValueError):rectangle(bounds,100,200,.1,[0,0,0])

    def test_preserves_other_layers_and_replays_site_memory(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'layers.json';inherited={'type':'circle','radius':1}
            p.write_text(json.dumps({'no_go_zones':[inherited],'slow_zones':[{'speed_percent':30}]}))
            store=Store();site=SiteMemory(store,p)
            z=site.preview(rectangle([0,0,.5,.5],100,100,.1,[0,0,0]),'construction','operator',[0,0,.5,.5])
            site.change(z['id']);self.assertEqual(len(json.loads(p.read_text())['no_go_zones']),2)
            site=SiteMemory(store,p);self.assertEqual(site.zones[z['id']]['state'],'APPLIED')
            site.change(z['id'],remove=True)
            self.assertEqual(json.loads(p.read_text()),{'no_go_zones':[inherited],'slow_zones':[{'speed_percent':30}]})
            with self.assertRaises(ValueError):site.change(z['id'],remove=True)

    def test_uncertain_file_write_survives_restart(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            store=Store();site=SiteMemory(store,Path(d)/'layers.json')
            z=site.preview(rectangle([0,0,.5,.5],100,100,.1,[0,0,0]),'test','operator',[0,0,.5,.5])
            with patch('ripple.site.os.replace',side_effect=OSError('disk error')):
                with self.assertRaises(OSError):site.change(z['id'])
            self.assertEqual(site.zones[z['id']]['state'],'UNCERTAIN')
            self.assertEqual(SiteMemory(store,Path(d)/'layers.json').zones[z['id']]['state'],'UNCERTAIN')

    def test_footprint_margin_is_in_map_units_and_clipped(self):
        b=buffered_bounds([0,0,.5,.5],100,200,.1)
        self.assertEqual(b[:2],[0,0])
        self.assertAlmostEqual(b[2],.555)
        self.assertAlmostEqual(b[3],.5275)
