"""Layers-file keepout adapter. A change counts only once the mask and costmap show it."""
import asyncio
import json
import os
from pathlib import Path
import time
from nav_msgs.msg import OccupancyGrid
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from .geometry import MapGeometry, inside, interior_cells, layers_entry


class Keepouts:
    def __init__(self, node, profile, site):
        nav = profile.navigation
        self.profile, self.site = profile, site
        self.enabled = nav.keepout_adapter == 'layers_file' and bool(nav.keepout_path) and bool(nav.verify_mask)
        self.path = Path(os.path.expanduser(nav.keepout_path)) if nav.keepout_path else None
        self.map = self.mask = self.costmap = None
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        node.create_subscription(OccupancyGrid, nav.map_topic,
                                 lambda m: setattr(self, 'map', (m, time.monotonic())), latched)
        if nav.verify_mask:
            node.create_subscription(OccupancyGrid, nav.verify_mask,
                                     lambda m: setattr(self, 'mask', (m, time.monotonic())), latched)
        if nav.global_costmap_topic:
            node.create_subscription(OccupancyGrid, nav.global_costmap_topic,
                                     lambda m: setattr(self, 'costmap', (m, time.monotonic())), 1)

    def geometry(self):
        return MapGeometry.from_info(self.map[0].info) if self.map else None

    def _read(self):
        if not self.path.exists():
            return {}
        layers = json.loads(self.path.read_text() or '{}')
        if not isinstance(layers, dict) or not isinstance(layers.get('no_go_zones', []), list):
            raise RuntimeError('The existing site layers file is not in the expected format')
        return layers

    def _write(self, layers):
        # Preserve every other author's layers; replace atomically so the publisher never reads a partial file.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + '.ripple.tmp')
        with temp.open('w') as f:
            json.dump(layers, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)

    def check(self, polygon, present, others=()):
        samples = [('mask', self.mask)]
        if present and self.profile.navigation.global_costmap_topic:
            samples.append(('costmap', self.costmap))
        for label, sample in samples:
            if sample is None:
                return False, label + ' not received'
            msg = sample[0]
            geom = MapGeometry.from_info(msg.info)
            cells = interior_cells(geom, polygon)
            if others:
                cells = [i for i in cells if not any(
                    inside(geom.grid_to_map(i % geom.width + .5, i // geom.width + .5), o) for o in others)]
            if not cells:
                if not present and others:
                    # Every cell is still restricted by another keepout: the mask cannot show this one's removal,
                    # but its entry is gone from the site layers file.
                    return True, 'removed from the site layers; the area is still covered by another keepout'
                return False, 'region is smaller than the ' + label + ' resolution'
            restricted = sum(1 for i in cells if msg.data[i] >= 99) / len(cells)
            if present and restricted < .95:
                return False, f'{label} shows {restricted:.0%} of the region restricted'
            if not present and restricted > .05:
                return False, f'{label} still shows {restricted:.0%} of the region restricted'
        if not present:
            return True, 'cleared from the keepout mask'
        return True, 'observed in the keepout mask' + (' and global costmap' if len(samples) > 1 else '')

    async def _wait(self, polygon, present, since, others=(), timeout=20.0):
        deadline = time.monotonic() + timeout
        why = 'waiting for a new keepout mask'
        while time.monotonic() < deadline:
            mask_new = self.mask is not None and self.mask[1] > since
            costmap_new = (not present or not self.profile.navigation.global_costmap_topic or
                           (mask_new and self.costmap is not None and self.costmap[1] > self.mask[1]))
            if mask_new and costmap_new:
                ok, why = self.check(polygon, present, others)
                if ok:
                    return True, why
            await asyncio.sleep(.25)
        return False, why

    async def apply(self, record):
        if not self.enabled:
            raise RuntimeError('keepout_unsupported_by_profile')
        layers = self._read()
        rows = [r for r in layers.get('no_go_zones', []) if r.get('ripple_id') != record['id']]
        rows.append(layers_entry(record['polygon'], record['id']))
        layers['no_go_zones'] = rows
        since = time.monotonic()
        self._write(layers)
        return await self._wait(record['polygon'], True, since)

    async def remove(self, record):
        if not self.enabled:
            raise RuntimeError('keepout_unsupported_by_profile')
        layers = self._read()
        layers['no_go_zones'] = [r for r in layers.get('no_go_zones', []) if r.get('ripple_id') != record['id']]
        since = time.monotonic()
        self._write(layers)
        others = [k['polygon'] for k in self.site.active_keepouts() if k['id'] != record['id']]
        return await self._wait(record['polygon'], False, since, others)

    def all_verified(self):
        return all(k['state'] == 'APPLIED' for k in self.site.active_keepouts())
