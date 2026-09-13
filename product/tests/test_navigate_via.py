import math
import unittest
import numpy as np
from pydantic import ValidationError
from ripple_edge.geometry import MapGeometry
from ripple_edge.navigation import clearance, nearest_clear
from ripple_edge.tools import NavigateVia


class ViaGeometryTest(unittest.TestCase):
    def setUp(self):
        self.geom = MapGeometry(width=100, height=80, resolution=0.05, origin_x=-2.0, origin_y=-1.0)
        self.grid = np.zeros((80, 100), dtype=np.int16)
        self.grid[:, 50] = 100  # a wall along map x = 0.5

    def test_clearance_is_the_distance_to_the_nearest_lethal_cell(self):
        self.assertAlmostEqual(clearance(self.geom, self.grid, 0.0, 0.5), 0.5, delta=0.06)
        self.assertEqual(clearance(self.geom, self.grid, -1.9, 0.5, reach=1.0), 1.0)
        self.assertIsNone(clearance(self.geom, self.grid, 9.0, 0.0))

    def test_nearest_clear_moves_a_point_off_the_wall(self):
        point = nearest_clear(self.geom, self.grid, 0.4, 0.5, need=0.5)
        self.assertIsNotNone(point)
        self.assertGreaterEqual(clearance(self.geom, self.grid, *point), 0.5)
        self.assertLess(math.dist(point, (0.4, 0.5)), 1.0)

    def test_rotated_map_uses_the_grid_frame(self):
        geom = MapGeometry(width=100, height=80, resolution=0.05, origin_x=0.0, origin_y=0.0, origin_yaw=math.pi / 2)
        grid = np.zeros((80, 100), dtype=np.int16)
        grid[40, :] = 100  # grid row gy = 40 is map x = -2.0 on this rotated map
        self.assertAlmostEqual(clearance(geom, grid, -1.5, 2.5), 0.5, delta=0.06)

    def test_via_arguments(self):
        args = NavigateVia.model_validate({'destination': 'B', 'via': [{'x': 1, 'y': 2}]})
        self.assertEqual(args.via[0].x, 1.0)
        with self.assertRaises(ValidationError):
            NavigateVia.model_validate({'destination': 'B', 'via': []})
        with self.assertRaises(ValidationError):
            NavigateVia.model_validate({'via': [{'x': 1}]})


if __name__ == '__main__':
    unittest.main()
