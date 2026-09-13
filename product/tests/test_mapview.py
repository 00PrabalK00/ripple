import io
import unittest
from types import SimpleNamespace
from PIL import Image
from ripple_agent import mapview
from ripple_edge.geometry import MapGeometry


def msg():
    info = SimpleNamespace(width=4, height=3, resolution=1.0,
                           origin=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0), orientation=SimpleNamespace(x=0, y=0, z=0, w=1)))
    data = [0] * 12
    data[0] = 100   # grid (0, 0): bottom-left of the map
    data[11] = -1   # grid (3, 2): top-right, unknown
    return SimpleNamespace(info=info, data=data)


class MapViewTests(unittest.TestCase):
    def test_row_zero_is_the_top_of_the_map(self):
        image = mapview.grid_image(msg())
        self.assertEqual(image.size, (4, 3))
        self.assertEqual(image.getpixel((0, 2)), 38)    # occupied, drawn at the bottom
        self.assertEqual(image.getpixel((3, 0)), 206)   # unknown, drawn at the top
        self.assertEqual(image.getpixel((1, 1)), 247)   # free

    def test_png_outputs(self):
        self.assertTrue(mapview.base_png(msg()).startswith(b'\x89PNG'))
        m = msg()
        png = mapview.labeled_png(m, MapGeometry.from_info(m.info), [{'x': 1.5, 'y': 1.5, 'label': 'Dock'}],
                                  [], [{'polygon': [(0, 0), (1, 0), (1, 1), (0, 1)]}], {'x': 2.0, 'y': 1.0, 'yaw': 0.0})
        self.assertEqual(Image.open(io.BytesIO(png)).size, (12, 9))


if __name__ == '__main__':
    unittest.main()
