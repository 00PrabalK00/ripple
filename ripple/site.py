"""Journaled site restrictions; immutable previews and atomic overlay updates."""
import json
import math
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone


def rectangle(bounds, width, height, resolution, origin):
    if len(bounds) != 4 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in bounds):
        raise ValueError('Select a rectangle inside the map')
    left, top, right, bottom = bounds
    if left >= right or top >= bottom or (right-left)*width < 2 or (bottom-top)*height < 2:
        raise ValueError('Region must cover at least two map cells per side')
    if origin[2] != 0:
        raise ValueError('Rotated maps require a transform before site editing')
    return dict(type='rectangle', x1=origin[0]+left*width*resolution,
                x2=origin[0]+right*width*resolution,
                y1=origin[1]+(1-bottom)*height*resolution,
                y2=origin[1]+(1-top)*height*resolution)


def buffered_bounds(bounds, width, height, resolution, margin=.55):
    dx, dy = margin/(width*resolution), margin/(height*resolution)
    return [max(0,bounds[0]-dx), max(0,bounds[1]-dy),
            min(1,bounds[2]+dx), min(1,bounds[3]+dy)]


class SiteMemory:
    def __init__(self, store, layers_file):
        self.store, self.path = store, Path(layers_file)
        self.zones = {}
        for row in store.receipts():
            if row['kind'] == 'site_preview': self.zones[row['zone']['id']] = row['zone']
            elif row['kind'] == 'site_change_requested' and row['id'] in self.zones:
                self.zones[row['id']]['state'] = 'UNCERTAIN'
            elif row['kind'] == 'site_changed' and row['id'] in self.zones:
                self.zones[row['id']]['state'] = row['state']
        # Existing applied state describes the file request, never proof of ROS uptake.

    def preview(self, shape, reason, operator, bounds):
        zone = dict(id=str(uuid4()), shape=shape, bounds=bounds, reason=reason,
                    operator=operator, created_at=datetime.now(timezone.utc).isoformat(),
                    expires_at=None, state='PREVIEW')
        self.store.append('site_preview', zone=zone)
        self.zones[zone['id']] = zone
        return zone

    def change(self, zone_id, remove=False):
        zone = self.zones[zone_id]
        expected = 'APPLIED' if remove else 'PREVIEW'
        if zone['state'] != expected:
            raise ValueError('Zone is not in the expected state')
        # Preserve all inherited layers and other authors' restrictions.
        layers = json.loads(self.path.read_text()) if self.path.exists() else {}
        rows = layers.get('no_go_zones', [])
        if not isinstance(rows, list): raise ValueError('Invalid existing site layers')
        rows = [r for r in rows if r.get('ripple_id') != zone_id]
        if not remove: rows.append(dict(zone['shape'], ripple_id=zone_id))
        layers['no_go_zones'] = rows
        state = 'REMOVED' if remove else 'APPLIED'
        self.store.append('site_change_requested', id=zone_id, state=state)
        zone['state'] = 'UNCERTAIN'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + '.ripple.tmp')
        with temp.open('w') as f:
            json.dump(layers, f, allow_nan=False); f.flush(); os.fsync(f.fileno())
        os.replace(temp, self.path)
        self.store.append('site_changed', id=zone_id, state=state)
        zone['state'] = state
        return zone
