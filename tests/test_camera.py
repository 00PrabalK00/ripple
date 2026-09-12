import unittest
import numpy as np
from ripple.camera import DepthDetector


class CameraTests(unittest.TestCase):
    def test_hysteresis_and_loss(self):
        d=DepthDetector()
        empty=np.ones((20,20))
        d.calibrate(empty)
        for i in range(9):
            self.assertEqual(d.update(empty, i/30), 'UNKNOWN')
        self.assertEqual(d.update(empty, .3), 'CLEAR')
        box=empty.copy();box[:5]=.9
        for i in range(4):
            self.assertEqual(d.update(box, .4+i/30), 'CLEAR')
        self.assertEqual(d.update(box, .6), 'BLOCKED')
        self.assertEqual(d.current(1.61), 'UNKNOWN')
        self.assertEqual(d.update(empty, 1.7), 'UNKNOWN')

    def test_invalid_depth_is_unknown(self):
        d=DepthDetector();depth=np.ones((20,20));d.calibrate(depth)
        for i in range(10):d.update(depth,i/30)
        self.assertEqual(d.update(np.zeros((20,20)),.5), 'UNKNOWN')
        with self.assertRaises(ValueError):d.calibrate(np.zeros((20,20)))
