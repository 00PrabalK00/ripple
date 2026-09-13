"""Site memory: registered stations, named areas, station availability and keepouts.

Station geometry comes from the robot profile. Areas, availability and keepouts come
from operators and are persisted through the edge's PostgreSQL memory.
"""
import re
from datetime import datetime, timezone
from .geometry import Region, centroid, inside

ACTIVE_KEEPOUT = ('APPLYING', 'APPLIED', 'UNVERIFIED', 'REMOVING')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def norm(name):
    return re.sub(r'[^a-z0-9]+', ' ', str(name).casefold()).strip()


class Site:
    def __init__(self, profile, store=None):
        self.profile = profile
        self.store = store
        self.areas = {}
        self.keepouts = {}
        self.unavailable = {}

    def load(self, records):
        for kind, key, data in records:
            if kind == 'area' and not data.get('deleted'):
                self.areas[key] = data
            elif kind == 'keepout':
                self.keepouts[key] = data
            elif kind == 'station_state':
                if data.get('available') is False:
                    self.unavailable[key] = data
                else:
                    self.unavailable.pop(key, None)

    def persist(self, kind, key, data):
        if self.store:
            self.store(kind, key, data)

    def destinations(self, geom=None):
        out = {}
        for key, s in self.profile.stations.items():
            out[key] = dict(kind='station', name=key, label=s.label or key, aliases=list(s.aliases),
                            frame=s.frame, x=s.x, y=s.y, yaw=s.yaw)
        if geom is not None:
            for area in self.areas.values():
                x, y = centroid(Region.load(area['region']).polygon(geom))
                out.setdefault(area['name'], dict(kind='area', name=area['name'], label=area['name'], aliases=[],
                                                  frame=self.profile.navigation.global_frame, x=x, y=y, yaw=0.0))
        # A destination inside an active keepout cannot be reached, whether or not anyone marked it unavailable.
        zones = [k for k in self.active_keepouts() if k['state'] != 'REMOVING']
        for key, d in out.items():
            state = self.unavailable.get(norm(key))
            zone = None if state else next((k for k in zones if inside((d['x'], d['y']), k['polygon'])), None)
            d['available'] = state is None and zone is None
            d['unavailable_reason'] = state.get('reason') if state else (
                'inside keepout ' + (zone.get('name') or zone['id']) if zone else None)
        return out

    def resolve(self, name, geom=None):
        wanted = norm(name)
        if not wanted:
            return None, None
        for key, d in self.destinations(geom).items():
            if wanted in {norm(n) for n in (key, d['label'], *d['aliases'])}:
                return key, d
        return None, None

    def area(self, name):
        return self.areas.get(norm(name))

    def define_area(self, name, region, author, reason=''):
        key = norm(name)
        if not key:
            raise ValueError('Area name is empty')
        if any(key in {norm(k), norm(s.label or k)} for k, s in self.profile.stations.items()):
            raise ValueError('That name already belongs to a registered station')
        record = dict(name=' '.join(name.split()), region=region.public(), author=author, reason=reason,
                      created_at=now_iso())
        self.areas[key] = record
        self.persist('area', key, record)
        return record

    def set_available(self, key, available, reason, author):
        record = dict(destination=key, available=available, reason=reason, author=author, at=now_iso())
        if available:
            self.unavailable.pop(norm(key), None)
        else:
            self.unavailable[norm(key)] = record
        self.persist('station_state', norm(key), record)
        return record

    def save_keepout(self, record):
        self.keepouts[record['id']] = record
        self.persist('keepout', record['id'], record)

    def active_keepouts(self):
        return [k for k in self.keepouts.values() if k['state'] in ACTIVE_KEEPOUT]

    def listed_keepouts(self):
        """What people and the model see: active keepouts plus ones waiting for the robot to leave the region."""
        return [k for k in self.keepouts.values() if k['state'] in ACTIVE_KEEPOUT or k['state'] == 'PENDING']
