"""Validate that an observed ROS grid can be overlaid on the displayed map."""
import math
from io import BytesIO
import numpy as np
from PIL import Image


def mask_png(data, width, height, resolution, origin, frame, map_meta, map_size):
    if (frame != 'map' or (width,height) != tuple(map_size)
            or not math.isclose(resolution,map_meta['resolution'],abs_tol=1e-6)
            or len(origin) != 3 or any(not math.isclose(a,b,abs_tol=1e-5)
                                      for a,b in zip(origin,map_meta['origin']))):
        raise ValueError('ROS mask geometry differs from displayed map; overlay withheld')
    cells=np.asarray(data,dtype=np.int16).reshape(height,width)
    rgba=np.zeros((height,width,4),dtype=np.uint8)
    # ROS grid row 0 is bottom; image row 0 is top.
    occupied=np.flipud(cells)>=100
    rgba[occupied]=[239,74,67,155]
    out=BytesIO();Image.fromarray(rgba).save(out,format='PNG');return out.getvalue()
