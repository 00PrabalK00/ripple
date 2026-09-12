"""Map-frame geometry for site constraints, including rotated maps.

Grid convention (nav_msgs/OccupancyGrid): cell (gx, gy) spans
origin + R(yaw) · ([gx, gx+1]·res, [gy, gy+1]·res). Image convention (rendered map
PNG): row 0 is the top of the map (largest gy); normalized image coordinates run
0..1 left to right and top to bottom.

Regions are kept as rectangles in the frame they were drawn in, so buffering stays
exact: a drawing on the map image is a grid-aligned rectangle (rotated with the map),
and a box given in metres is aligned with the map frame.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class MapGeometry:
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    origin_yaw: float = 0.0

    @classmethod
    def from_info(cls, info):
        q = info.origin.orientation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        return cls(int(info.width), int(info.height), float(info.resolution),
                   float(info.origin.position.x), float(info.origin.position.y), yaw)

    @property
    def rotated(self):
        return abs(math.remainder(self.origin_yaw, 2 * math.pi)) > 1e-6

    def grid_to_map(self, gx, gy):
        c, s = math.cos(self.origin_yaw), math.sin(self.origin_yaw)
        dx, dy = gx * self.resolution, gy * self.resolution
        return self.origin_x + c * dx - s * dy, self.origin_y + s * dx + c * dy

    def map_to_grid(self, x, y):
        c, s = math.cos(self.origin_yaw), math.sin(self.origin_yaw)
        dx, dy = x - self.origin_x, y - self.origin_y
        return (c * dx + s * dy) / self.resolution, (-s * dx + c * dy) / self.resolution

    def image_to_map(self, u, v):
        return self.grid_to_map(u * self.width, (1 - v) * self.height)

    def map_to_image(self, x, y):
        gx, gy = self.map_to_grid(x, y)
        return gx / self.width, 1 - gy / self.height

    def public(self):
        return {'width': self.width, 'height': self.height, 'resolution': self.resolution,
                'origin': [self.origin_x, self.origin_y, self.origin_yaw]}


@dataclass(frozen=True)
class Region:
    """A rectangle in grid cells ('grid') or map metres ('map')."""
    frame: str
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_image(cls, geom, bounds):
        if len(bounds) != 4 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in bounds):
            raise ValueError('Region must be [left, top, right, bottom] inside the map image')
        left, top, right, bottom = bounds
        if left >= right or top >= bottom:
            raise ValueError('Region must have positive width and height')
        region = cls('grid', left * geom.width, (1 - bottom) * geom.height,
                     right * geom.width, (1 - top) * geom.height)
        if region.x1 - region.x0 < 2 or region.y1 - region.y0 < 2:
            raise ValueError('Region must cover at least two map cells per side')
        return region

    @classmethod
    def from_box(cls, box):
        if len(box) != 4 or any(not math.isfinite(v) for v in box):
            raise ValueError('Box must be [x1, y1, x2, y2] in metres')
        x1, y1, x2, y2 = box
        region = cls('map', min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        if region.x1 - region.x0 < 0.1 or region.y1 - region.y0 < 0.1:
            raise ValueError('Box must be at least 0.1 m per side')
        return region

    def buffered(self, geom, metres):
        pad = metres / geom.resolution if self.frame == 'grid' else metres
        x0, y0, x1, y1 = self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad
        if self.frame == 'grid':
            x0, y0 = max(0.0, x0), max(0.0, y0)
            x1, y1 = min(float(geom.width), x1), min(float(geom.height), y1)
        return Region(self.frame, x0, y0, x1, y1)

    def polygon(self, geom):
        corners = [(self.x0, self.y0), (self.x1, self.y0), (self.x1, self.y1), (self.x0, self.y1)]
        if self.frame == 'map':
            return corners
        return [geom.grid_to_map(gx, gy) for gx, gy in corners]

    def public(self):
        return {'frame': self.frame, 'x0': self.x0, 'y0': self.y0, 'x1': self.x1, 'y1': self.y1}

    @classmethod
    def load(cls, data):
        return cls(data['frame'], data['x0'], data['y0'], data['x1'], data['y1'])


def layers_entry(polygon, ripple_id):
    """The keepout publisher's rectangle when map-aligned, otherwise its polygon."""
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    aligned = len(polygon) == 4 and all(
        (abs(a[0] - b[0]) < 1e-9) != (abs(a[1] - b[1]) < 1e-9)
        for a, b in zip(polygon, polygon[1:] + polygon[:1]))
    if aligned:
        return {'type': 'rectangle', 'x1': min(xs), 'x2': max(xs), 'y1': min(ys), 'y2': max(ys),
                'ripple_id': ripple_id}
    return {'type': 'polygon', 'points': [{'x': x, 'y': y} for x, y in polygon], 'ripple_id': ripple_id}


def inside(point, polygon):
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def distance(point, polygon):
    """0 inside, otherwise the distance to the nearest edge."""
    if inside(point, polygon):
        return 0.0
    px, py = point
    best = math.inf
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)))
    return best


def edge_distance(point, polygon):
    px, py = point
    best = math.inf
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)))
    return best


def interior_cells(geom, polygon, margin_cells=1.0):
    """Indices of cells whose centres lie inside polygon, at least margin_cells from its edge."""
    grid = [geom.map_to_grid(x, y) for x, y in polygon]
    gx0 = max(0, int(math.floor(min(p[0] for p in grid))))
    gx1 = min(geom.width - 1, int(math.ceil(max(p[0] for p in grid))))
    gy0 = max(0, int(math.floor(min(p[1] for p in grid))))
    gy1 = min(geom.height - 1, int(math.ceil(max(p[1] for p in grid))))
    margin = margin_cells * geom.resolution
    cells = []
    for gy in range(gy0, gy1 + 1):
        for gx in range(gx0, gx1 + 1):
            centre = geom.grid_to_map(gx + 0.5, gy + 0.5)
            if inside(centre, polygon) and edge_distance(centre, polygon) >= margin:
                cells.append(gx + gy * geom.width)
    return cells


def centroid(polygon):
    return sum(p[0] for p in polygon) / len(polygon), sum(p[1] for p in polygon) / len(polygon)
