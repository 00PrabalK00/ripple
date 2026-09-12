"""Render the live occupancy grid: plain for the dashboard, labeled for the vision model.

Image convention matches ripple_edge.geometry: row 0 is the top of the map.
"""
import io
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'


def grid_image(msg):
    info = msg.info
    data = np.frombuffer(bytes(bytearray(v & 0xFF for v in msg.data)), dtype=np.uint8).view(np.int8)
    grid = data.reshape(info.height, info.width)
    shade = np.full(grid.shape, 150, dtype=np.uint8)
    shade[grid < 0] = 206
    shade[(grid >= 0) & (grid <= 25)] = 247
    shade[grid >= 65] = 38
    return Image.fromarray(np.flipud(shade), 'L')


def png_bytes(image):
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def base_png(msg):
    return png_bytes(grid_image(msg).convert('RGB'))


def labeled_png(msg, geom, stations, areas, keepouts, pose, scale=3):
    """Map with station/area labels and the robot, so words like 'the aisle past A3' can be located."""
    image = grid_image(msg).convert('RGB')
    w, h = image.size
    image = image.resize((w * scale, h * scale), Image.NEAREST)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(FONT, 15)
    except OSError:
        font = ImageFont.load_default()

    def px(x, y):
        u, v = geom.map_to_image(x, y)
        return u * w * scale, v * h * scale

    for k in keepouts:
        draw.polygon([px(*p) for p in k['polygon']], outline=(220, 70, 50), width=3)
    for a in areas:
        pts = [px(*p) for p in a['polygon']]
        draw.polygon(pts, outline=(120, 80, 220), width=3)
        draw.text((min(p[0] for p in pts) + 4, min(p[1] for p in pts) + 2), a['name'], fill=(90, 50, 190), font=font)
    for s in stations:
        x, y = px(s['x'], s['y'])
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(20, 140, 110))
        draw.text((x + 9, y - 9), s['label'], fill=(10, 100, 80), font=font)
    if pose:
        x, y = px(pose['x'], pose['y'])
        heading = -(pose.get('yaw', 0.0) - geom.origin_yaw)
        tip = (x + 16 * math.cos(heading), y + 16 * math.sin(heading))
        left = (x + 9 * math.cos(heading + 2.5), y + 9 * math.sin(heading + 2.5))
        right = (x + 9 * math.cos(heading - 2.5), y + 9 * math.sin(heading - 2.5))
        draw.polygon([tip, left, right], fill=(40, 110, 220))
        draw.text((x + 12, y + 8), 'ROBOT', fill=(40, 110, 220), font=font)
    return png_bytes(image)
