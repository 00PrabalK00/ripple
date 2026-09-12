from io import BytesIO
import unittest
from PIL import Image
from ripple.map_projection import mask_png

class MapProjectionTests(unittest.TestCase):
    def test_ros_bottom_row_becomes_image_bottom_row(self):
        meta={'resolution':.05,'origin':[-7,-10,0]}
        png=mask_png([100,0,0,0,0,100],3,2,.05,[-7,-10,0],'map',meta,(3,2))
        im=Image.open(BytesIO(png))
        self.assertEqual(im.getpixel((0,1))[3],155)
        self.assertEqual(im.getpixel((2,0))[3],155)
        self.assertEqual(im.getpixel((0,0))[3],0)
        self.assertEqual(im.getpixel((2,1))[3],0)

    def test_misaligned_mask_is_withheld(self):
        meta={'resolution':.05,'origin':[-7,-10,0]}
        for origin,frame,res in [([-6,-10,0],'map',.05),([-7,-10,0],'odom',.05),([-7,-10,0],'map',.1)]:
            with self.assertRaises(ValueError):mask_png([0]*6,3,2,res,origin,frame,meta,(3,2))
