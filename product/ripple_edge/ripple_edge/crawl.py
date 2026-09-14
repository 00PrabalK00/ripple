"""Self-configuration: propose a robot profile from the robot's workspace and its live ROS graph.

Topic, action and service names differ between robots, so every role is matched by message type
first, then by who publishes or serves it, then by naming convention. Each proposed field carries
a confidence and the evidence behind it; the setup asks a person only about the uncertain ones.
Live observations outrank static configuration, and a value in an existing config that the live
graph contradicts is reported as drift instead of being silently replaced. Safety-relevant choices
default to the conservative option: teleop never overrides the safety controller unless a person
enables it on a declared simulation, and escape is off without a safety layer.
  python3 -m ripple_edge.crawl --workspace ~/robot_ws [--live] [--out ripple.json]
"""
import json
import math
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
import yaml

SKIP_DIRS = {'build', 'install', 'log', '.git', 'node_modules', '__pycache__', '.venv'}
NAV2_NODES = ('amcl', 'planner_server', 'controller_server', 'bt_navigator', 'behavior_server',
              'smoother_server', 'velocity_smoother', 'waypoint_follower', 'collision_monitor')
T_ODOM, T_SCAN, T_POSE_COV = 'nav_msgs/msg/Odometry', 'sensor_msgs/msg/LaserScan', 'geometry_msgs/msg/PoseWithCovarianceStamped'
T_GRID, T_PATH, T_TWIST = 'nav_msgs/msg/OccupancyGrid', 'nav_msgs/msg/Path', 'geometry_msgs/msg/Twist'
T_STRING, T_POSE, T_FILTER = 'std_msgs/msg/String', 'geometry_msgs/msg/PoseStamped', 'nav2_msgs/msg/CostmapFilterInfo'
DEFAULTS = {
    'triggers': {'speed_below': 0.02, 'turn_below': 0.02, 'halted_for_s': 8, 'flat_for_s': 15, 'min_progress_m': 0.03,
                 'goal_tolerance_m': 0.25, 'failure_count': 3, 'failure_window_s': 120, 'localization_degraded_s': 5},
    'missions': {'channel': 'ambiguous', 'operators': ['site-engineers'], 'operator_command': 'go',
                 'inferred_destination': 'go_announce', 'agent_initiated': 'ask',
                 'dispatch_checks': ['station_registered', 'facts_fresh', 'keepouts_verified', 'route_probe_ok',
                                     'safety_not_holding', 'mode_not_manual', 'single_owner']},
    'escape_requires': ['safety_present', 'safety_not_holding', 'mode_not_manual', 'localization_fresh',
                        'no_active_goal', 'rear_scan_fresh'],
}


@dataclass
class Finding:
    value: object
    confidence: str  # high: confirmed live and unambiguous; medium: static only, or a best of several; low: a default
    source: str
    alternatives: list = field(default_factory=list)


# ----- static scan (no robot needs to be running)
def _files(root, patterns):
    for path in Path(root).rglob('*'):
        if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(root).parts) and path.match(patterns):
            yield path


def _load(path):
    try:
        text = path.read_text(errors='replace')
        return json.loads(text) if path.suffix == '.json' else yaml.safe_load(text)
    except Exception:
        return None


def _stations_in(data):
    """Named poses: {name: {position: {x, y}, orientation: {z, w}}} or {name: {x, y, yaw|theta}}, possibly nested."""
    if not isinstance(data, dict):
        return {}
    if len(data) == 1 and isinstance(next(iter(data.values())), dict):
        inner = _stations_in(next(iter(data.values())))
        if inner:
            return inner
    out = {}
    for name, pose in data.items():
        if not isinstance(pose, dict):
            continue
        pos, ori = pose.get('position'), pose.get('orientation') or {}
        if isinstance(pos, dict) and {'x', 'y'} <= set(pos):
            yaw = 2 * math.atan2(float(ori.get('z', 0)), float(ori.get('w', 1)))
            out[str(name)] = {'frame': pose.get('frame_id', 'map'), 'x': float(pos['x']), 'y': float(pos['y']), 'yaw': round(yaw, 3)}
        elif {'x', 'y'} <= set(pose) and all(isinstance(pose[k], (int, float)) for k in ('x', 'y')):
            out[str(name)] = {'frame': pose.get('frame', pose.get('frame_id', 'map')), 'x': float(pose['x']),
                              'y': float(pose['y']), 'yaw': float(pose.get('yaw', pose.get('theta', 0.0)))}
    return out if len(out) >= 2 else {}


def static_scan(root):
    root = Path(root).expanduser().resolve()
    found = {'root': str(root), 'packages': [], 'params': {}, 'param_files': {}, 'remaps': [], 'stations': {}, 'station_file': None}
    for path in _files(root, 'package.xml'):
        m = re.search(r'<name>\s*([^<\s]+)\s*</name>', path.read_text(errors='replace'))
        if m:
            found['packages'].append(m.group(1))
    for path in list(_files(root, '*.yaml')) + list(_files(root, '*.json')):
        data = _load(path)
        if not isinstance(data, dict):
            continue
        rel = str(path.relative_to(root))
        for node, body in data.items():
            # Nav2 nests costmaps as {local_costmap: {local_costmap: {ros__parameters: ...}}}.
            if isinstance(body, dict) and len(body) == 1 and isinstance(body.get(node), dict):
                body = body[node]
            if isinstance(body, dict) and isinstance(body.get('ros__parameters'), dict):
                # The fullest definition of a node wins (a working params file over a stub).
                if len(json.dumps(body['ros__parameters'], default=str)) > len(json.dumps(found['params'].get(node, {}), default=str)):
                    found['params'][node], found['param_files'][node] = body['ros__parameters'], rel
        stations = _stations_in(data)
        if len(stations) > len(found['stations']):
            found['stations'], found['station_file'] = stations, rel
    for path in list(_files(root, '*.launch.py')) + list(_files(root, '*.py')):
        text = path.read_text(errors='replace')
        if 'remappings' in text:
            found['remaps'] += re.findall(r"\(\s*['\"](/?[\w/]+)['\"]\s*,\s*['\"](/?[\w/]+)['\"]\s*\)", text)
    found['packages'].sort()
    return found


# ----- live scan (reads the running graph; needs a sourced ROS 2 environment)
def live_scan(timeout=4.0):
    import time
    import rclpy
    from rclpy.action import get_action_client_names_and_types_by_node, get_action_server_names_and_types_by_node
    from rcl_interfaces.srv import GetParameters, ListParameters
    from std_srvs.srv import Trigger
    rclpy.init()
    node = rclpy.create_node('ripple_crawler')
    try:
        time.sleep(min(timeout, 2.0))  # let discovery settle
        for _ in range(int(timeout * 10)):
            rclpy.spin_once(node, timeout_sec=0.1)
        nodes = [(n, ns) for n, ns in node.get_node_names_and_namespaces() if n != 'ripple_crawler']
        full = lambda n, ns: (ns.rstrip('/') + '/' + n) if ns != '/' else '/' + n
        topics = {}
        for name, types in node.get_topic_names_and_types():
            topics[name] = {'types': types,
                            'publishers': [full(i.node_name, i.node_namespace) for i in node.get_publishers_info_by_topic(name)],
                            'subscribers': [full(i.node_name, i.node_namespace) for i in node.get_subscriptions_info_by_topic(name)]}
        services = {name: types for name, types in node.get_service_names_and_types()}
        actions = {}
        for n, ns in nodes:
            for kind, fn in (('servers', get_action_server_names_and_types_by_node), ('clients', get_action_client_names_and_types_by_node)):
                try:
                    for name, types in fn(node, n, ns):
                        actions.setdefault(name, {'types': types, 'servers': [], 'clients': []})[kind].append(full(n, ns))
                except Exception:
                    pass

        def call(client, request):
            if not client.wait_for_service(timeout_sec=2.0):
                return None
            future = client.call_async(request)
            rclpy.spin_until_future_complete(node, future, timeout_sec=3.0)
            return future.result()

        def params(target, names=None, prefixes=None):
            if names is None:
                listed = call(node.create_client(ListParameters, target + '/list_parameters'),
                              ListParameters.Request(prefixes=prefixes or [], depth=4))
                names = list(listed.result.names) if listed else []
            got = call(node.create_client(GetParameters, target + '/get_parameters'), GetParameters.Request(names=names)) if names else None
            out = {}
            for n, v in zip(names, got.values if got else []):
                value = [v.bool_value, v.integer_value, v.double_value, v.string_value, list(v.byte_array_value),
                         list(v.bool_array_value), list(v.integer_array_value), list(v.double_array_value),
                         list(v.string_array_value)][v.type - 1] if 1 <= v.type <= 9 else None
                out[n] = value
            return out

        live_params = {}
        names = {full(n, ns) for n, ns in nodes}
        for target, wanted in (('/controller_server', ['odom_topic']), ('/bt_navigator', ['odom_topic']),
                               ('/amcl', ['scan_topic', 'base_frame_id', 'global_frame_id']),
                               ('/global_costmap/global_costmap', ['global_frame', 'robot_base_frame', 'footprint', 'robot_radius']),
                               ('/robot_state_publisher', ['robot_description'])):
            if target in names:
                live_params[target] = params(target, wanted)
        for target in names:
            if target.rsplit('/', 1)[-1] == 'twist_mux':
                live_params[target] = params(target, prefixes=['topics'])
        for tname, t in topics.items():
            if T_FILTER in t['types']:
                for pub in t['publishers']:
                    live_params.setdefault(pub, params(pub))
        safety_text = {}
        for sname, types in services.items():
            if 'std_srvs/srv/Trigger' in types and 'safety' in sname and 'status' in sname:
                reply = call(node.create_client(Trigger, sname), Trigger.Request())
                safety_text[sname] = reply.message if reply else ''
        return {'nodes': sorted(names), 'topics': topics, 'services': services, 'actions': actions,
                'params': live_params, 'safety_text': safety_text}
    finally:
        node.destroy_node()
        rclpy.shutdown()


# ----- matching (pure: works on scan dictionaries, so it is testable without ROS)
def _with_type(live, type_name):
    return {n: t for n, t in (live or {}).get('topics', {}).items() if type_name in t['types']}


def _pick(candidates, score):
    ranked = sorted(candidates, key=lambda c: (-score(c), c))
    return ranked[0] if ranked else None, ranked[1:]


def _static_param(static, node, key, default=None):
    return (static.get('params', {}).get(node) or {}).get(key, default)


def _footprint_radius(value):
    try:
        points = yaml.safe_load(value) if isinstance(value, str) else value
        return round(max(math.hypot(float(x), float(y)) for x, y in points), 2)
    except Exception:
        return None


class Proposal:
    def __init__(self):
        self.fields = {}
        self.drift = []

    def set(self, path, value, confidence, source, alternatives=()):
        self.fields[path] = Finding(value, confidence, source, list(alternatives))

    def value(self, path, default=None):
        f = self.fields.get(path)
        return default if f is None else f.value

    def uncertain(self):
        return {p: f for p, f in self.fields.items() if f.confidence != 'high'}

    def profile(self):
        """The proposal as a profile dictionary (validate it with contracts.Profile)."""
        v = self.value
        stations = v('stations', {}) or {}
        safety_required = bool(v('safety.service'))
        inputs = v('motion_inputs', {})
        teleop_topic = v('teleop.topic')
        profile = {
            'schema_version': 1, 'robot': v('robot'), 'ros_distro': 'humble', 'domain_id': v('domain_id', 0),
            'navigation': {
                'navigate_action': v('navigation.navigate_action'), 'internal_action_clients': v('navigation.internal_action_clients', []),
                'goal_input_topics': v('navigation.goal_input_topics', []), 'planner_action': v('navigation.planner_action'),
                'lifecycle_nodes': v('navigation.lifecycle_nodes', []), 'clear_services': v('navigation.clear_services', {}),
                'keepout_adapter': v('navigation.keepout_adapter', 'none'), 'keepout_path': v('navigation.keepout_path'),
                'verify_mask': v('navigation.verify_mask'), 'map_topic': v('navigation.map_topic', '/map'),
                'global_frame': v('navigation.global_frame', 'map'), 'base_frame': v('navigation.base_frame', 'base_link'),
                'plan_topic': v('navigation.plan_topic', '/plan'), 'global_costmap_topic': v('navigation.global_costmap_topic'),
                'keepout_clearance_m': v('navigation.keepout_clearance_m', 0.55), 'footprint_radius_m': v('navigation.footprint_radius_m', 0.5)},
            'safety': {'required': safety_required, 'service': v('safety.service'), 'parser': v('safety.parser', 'none'),
                       'max_age_s': 1.0, 'never_touch': v('safety.never_touch', [])},
            'motion_inputs': inputs, 'motion_output': {'topic': v('motion_output'), 'max_age_s': 0.5},
            'odometry': {'topic': v('odometry'), 'max_age_s': 1.0}, 'localization': {'topic': v('localization'), 'max_age_s': 5.0},
            'localization_refresh_service': v('localization_refresh_service'),
            'covariance_xy_max': 0.5, 'covariance_yaw_max': 0.5,
            'scans': {k: {'topic': t, 'max_age_s': 1.0} for k, t in (v('scans', {}) or {}).items()},
            'mode': {'topic': v('mode'), 'max_age_s': 1.0} if v('mode') else None,
            'triggers': dict(DEFAULTS['triggers']),
            'logs': {'rosout_topic': '/rosout', 'nodes': v('logs.nodes', []), 'min_level': 30, 'journald_services': []},
            'missions': json.loads(json.dumps(DEFAULTS['missions'])),
            'recovery': {
                'clear_costmaps': {'autonomy': 'auto', 'per_incident': 1}, 'retry_goal': {'autonomy': 'auto', 'per_incident': 2},
                'lifecycle_reset': {'autonomy': 'ask', 'per_incident': 1}, 'reset_nodes': v('recovery.reset_nodes', []),
                'escape': {'autonomy': 'ask' if safety_required and v('recovery.backup_action') and 'rear' in (v('scans') or {}) else 'off',
                           'backup_action': v('recovery.backup_action') or '/backup', 'spin_action': v('recovery.spin_action') or '/spin',
                           'max_backup_m': 0.30, 'max_backup_mps': 0.15, 'max_spin_rad': 1.57, 'per_incident': 2,
                           'requires': list(DEFAULTS['escape_requires'])}},
            'stations': stations, 'escalation_channel': 'ambiguous', 'reply_timeout_s': 120,
            'simulation': bool(v('simulation', False)),
        }
        if teleop_topic:
            profile['teleop'] = {'topic': teleop_topic, 'mode_topic': v('mode'), 'manual_mode': 'manual',
                                 'restore_mode': v('teleop.restore_mode', 'zones'), 'max_speed_mps': 0.15, 'max_distance_m': 0.5,
                                 'turn_rate': 0.4, 'min_clearance_m': 0.2, 'override_safety': False}
        return profile


def propose(static=None, live=None, existing=None):
    """Match every profile role. `existing` is a current profile dict: its values are kept when the
    live graph confirms them or says nothing, and reported as drift when the live graph contradicts them."""
    static, p = static or {}, Proposal()
    live_topics = (live or {}).get('topics', {})
    services, actions = (live or {}).get('services', {}), (live or {}).get('actions', {})
    lp = (live or {}).get('params', {})
    src_live = 'live graph'

    def static_src(node):
        return static.get('param_files', {}).get(node, 'workspace config') + f' ({node})'

    def action(type_name, prefer):
        names = [n for n, a in actions.items() if type_name in a['types'] and a['servers']]
        best, rest = _pick(names, lambda n: 2 if n == prefer else 0)
        return best, rest

    # Robot identity
    description = next((pr.get('robot_description') for pr in lp.values() if pr.get('robot_description')), None)
    name = re.search(r'<robot\s+name="([^"]+)"', description or '')
    if name:
        generic = name.group(1).lower() in ('robot', 'my_robot', 'my_bot', 'bot', 'urdf')
        p.set('robot', re.sub(r'\W+', '_', name.group(1)), 'medium' if generic else 'high',
              'robot_description (URDF)' + ('; the name is generic, so confirm it' if generic else ''))
    else:
        p.set('robot', re.sub(r'\W+', '_', socket.gethostname()), 'low', 'host name; name the robot')
    p.set('domain_id', int(os.environ.get('ROS_DOMAIN_ID', 0)), 'high' if 'ROS_DOMAIN_ID' in os.environ else 'medium', 'ROS_DOMAIN_ID')
    sim = any('gazebo' in n or n.endswith('/gzserver') for n in (live or {}).get('nodes', []))
    p.set('simulation', sim, 'medium', 'Gazebo nodes present' if sim else 'no simulator nodes seen')

    # Navigation actions and services
    for path, type_name, prefer in (('navigation.navigate_action', 'nav2_msgs/action/NavigateToPose', '/navigate_to_pose'),
                                    ('navigation.planner_action', 'nav2_msgs/action/ComputePathToPose', '/compute_path_to_pose'),
                                    ('recovery.backup_action', 'nav2_msgs/action/BackUp', '/backup'),
                                    ('recovery.spin_action', 'nav2_msgs/action/Spin', '/spin')):
        best, rest = action(type_name, prefer)
        if best:
            p.set(path, best, 'high' if not rest else 'medium', src_live, rest)
        elif live is None:
            p.set(path, prefer, 'medium', 'Nav2 default name (not confirmed live)')
        elif path.startswith('navigation.'):
            p.set(path, prefer, 'low', 'not found live; Nav2 default name')
    nav_action = p.value('navigation.navigate_action')
    clients = [c for c in actions.get(nav_action, {}).get('clients', []) if c.rsplit('/', 1)[-1] in NAV2_NODES]
    p.set('navigation.internal_action_clients', sorted(set(clients)) or ['/bt_navigator'], 'high' if clients else 'medium', src_live)
    goals = [n for n, t in _with_type(live, T_POSE).items() if any(s.endswith('bt_navigator') for s in t['subscribers'])]
    p.set('navigation.goal_input_topics', goals or ['/goal_pose'], 'high' if goals else 'medium', src_live)
    lifecycle = sorted({s[:-len('/get_state')] for s, t in services.items()
                        if s.endswith('/get_state') and 'lifecycle_msgs/srv/GetState' in t and s[:-len('/get_state')].rsplit('/', 1)[-1] in NAV2_NODES})
    if lifecycle:
        p.set('navigation.lifecycle_nodes', lifecycle, 'high', src_live)
    else:
        nodes = [f'/{n}' for n in NAV2_NODES[:5] if n in static.get('params', {})]
        p.set('navigation.lifecycle_nodes', nodes, 'medium' if nodes else 'low', 'Nav2 params in the workspace')
    p.set('recovery.reset_nodes', [n for n in p.value('navigation.lifecycle_nodes') if n.endswith(('controller_server', 'planner_server'))],
          'high', 'declared lifecycle nodes')
    clear = {kind: s for s, t in services.items() for kind in ('global', 'local')
             if 'nav2_msgs/srv/ClearEntireCostmap' in t and f'{kind}_costmap' in s}
    p.set('navigation.clear_services', clear or {'global': '/global_costmap/clear_entirely_global_costmap',
                                                 'local': '/local_costmap/clear_entirely_local_costmap'},
          'high' if len(clear) == 2 else 'medium', src_live if clear else 'Nav2 default names')

    # Maps, costmaps, frames, keepouts
    grids = _with_type(live, T_GRID)
    grid = lambda test: sorted(n for n in grids if test(n))
    maps = grid(lambda n: n == '/map') or grid(lambda n: n.endswith('/map'))
    p.set('navigation.map_topic', maps[0] if maps else '/map', 'high' if maps else 'medium', src_live if maps else 'default')
    costmaps = grid(lambda n: n.endswith('global_costmap/costmap'))
    p.set('navigation.global_costmap_topic', costmaps[0] if costmaps else '/global_costmap/costmap',
          'high' if costmaps else 'medium', src_live if costmaps else 'Nav2 default name')
    plans = [n for n, t in _with_type(live, T_PATH).items() if any(pub.endswith('planner_server') for pub in t['publishers'])]
    p.set('navigation.plan_topic', '/plan' if '/plan' in plans else (plans[0] if plans else '/plan'), 'high' if plans else 'medium', src_live)
    gc = lp.get('/global_costmap/global_costmap', {}) or _static_param(static, 'global_costmap', '__none__', {}) or {}
    static_gc = static.get('params', {}).get('global_costmap', {})
    for path, key, default in (('navigation.global_frame', 'global_frame', 'map'), ('navigation.base_frame', 'robot_base_frame', 'base_link')):
        if gc.get(key):
            p.set(path, gc[key], 'high', 'global costmap parameters (live)')
        elif static_gc.get(key):
            p.set(path, static_gc[key], 'medium', static_src('global_costmap'))
        else:
            p.set(path, default, 'low', 'default')
    radius = _footprint_radius(gc.get('footprint') or static_gc.get('footprint'))
    radius = radius or (gc.get('robot_radius') or static_gc.get('robot_radius'))
    if radius:
        p.set('navigation.footprint_radius_m', float(radius), 'high' if gc else 'medium', 'costmap footprint (circumscribed)')
        p.set('navigation.keepout_clearance_m', round(float(radius) + 0.08, 2), 'medium', 'footprint radius + 0.08 m margin')
    masks = grid(lambda n: 'keepout' in n and 'mask' in n)
    p.set('navigation.verify_mask', masks[0] if masks else None, 'high' if masks else 'medium',
          src_live if masks else 'no keepout mask topic seen')
    layers = next((v for pub, pr in lp.items() for k, v in pr.items() if k.endswith('layers_file') and v), None)
    if layers and masks:
        p.set('navigation.keepout_adapter', 'layers_file', 'high', 'keepout publisher parameters (live)')
        p.set('navigation.keepout_path', layers.replace(os.path.expanduser('~'), '~', 1), 'high', 'keepout publisher layers_file')
    else:
        p.set('navigation.keepout_adapter', 'none', 'medium', 'no keepout publisher with a layers file found')
        p.set('navigation.keepout_path', None, 'medium', 'no keepout adapter')

    # Motion: twist_mux inputs and output, or the controller's own command topic
    mux = next((n for n in lp if n.rsplit('/', 1)[-1] == 'twist_mux'), None)
    inputs = {}
    if mux:
        mp = lp[mux]
        for key, value in mp.items():
            m = re.match(r'topics\.([^.]+)\.topic$', key)
            if m:
                name = m.group(1)
                topic = value if value.startswith('/') else '/' + value
                inputs[name] = {'topic': topic, 'priority': int(mp.get(f'topics.{name}.priority', 0)),
                                'max_age_s': float(mp.get(f'topics.{name}.timeout', 0.5)) or 0.5}
        out = [n for n, t in _with_type(live, T_TWIST).items() if mux in t['publishers']]
        p.set('motion_inputs', inputs, 'high', 'twist_mux parameters (live)')
        p.set('motion_output', out[0] if out else '/cmd_vel_out', 'high' if len(out) == 1 else 'medium', 'twist_mux output (live)', out[1:])
    else:
        static_mux = static.get('params', {}).get('twist_mux', {}).get('topics', {})
        for name, cfg in static_mux.items():
            topic = cfg.get('topic', '')
            inputs[name] = {'topic': topic if topic.startswith('/') else '/' + topic, 'priority': int(cfg.get('priority', 0)),
                            'max_age_s': float(cfg.get('timeout', 0.5))}
        remapped = [dst for src, dst in static.get('remaps', []) if src.lstrip('/') == 'cmd_vel_out']
        if inputs:
            p.set('motion_inputs', inputs, 'medium', static_src('twist_mux'))
            p.set('motion_output', remapped[0] if remapped else '/cmd_vel_out', 'medium', 'twist_mux remapping in launch files')
        else:
            cmd = [n for n, t in _with_type(live, T_TWIST).items() if any(pub.endswith('controller_server') for pub in t['publishers'])]
            topic = cmd[0] if cmd else '/cmd_vel'
            p.set('motion_inputs', {'navigation': {'topic': topic, 'priority': 10, 'max_age_s': 0.5}},
                  'medium' if cmd else 'low', 'controller command topic (no velocity multiplexer found)')
            p.set('motion_output', topic, 'medium' if cmd else 'low', 'controller command topic')
    joy = next((i['topic'] for n, i in p.value('motion_inputs').items() if n.lower() in ('joystick', 'joy', 'teleop', 'manual')), None)
    if joy:
        p.set('teleop.topic', joy, 'high' if mux else 'medium', 'manual input of the velocity multiplexer')

    # Odometry, localization, scans
    odoms = _with_type(live, T_ODOM)
    wanted = [lp.get(n, {}).get('odom_topic') for n in ('/controller_server', '/bt_navigator')]
    wanted = ['/' + w.lstrip('/') for w in wanted if w] or ['/' + str(_static_param(static, 'controller_server', 'odom_topic', '')).lstrip('/')]
    best, rest = _pick(list(odoms), lambda n: 3 if n in wanted else (1 if n.endswith('/odom') else 0))
    if best:
        p.set('odometry', best, 'high' if best in wanted or not rest else 'medium', src_live, rest)
    else:
        p.set('odometry', wanted[0] if wanted[0] != '/' else '/odom', 'medium' if wanted[0] != '/' else 'low', static_src('controller_server'))
    # Only published estimates count: /initialpose has the same type but is an input to the localizer.
    poses = {n: t for n, t in _with_type(live, T_POSE_COV).items() if t['publishers']}
    best, rest = _pick(list(poses), lambda n: 3 if any(pub.endswith('amcl') for pub in poses[n]['publishers']) else (1 if 'pose' in n else 0))
    p.set('localization', best or '/amcl_pose', 'high' if best and not rest else ('medium' if best else 'low'), src_live, rest)
    empties = [s for s, t in services.items() if 'std_srvs/srv/Empty' in t and 'nomotion' in s]
    p.set('localization_refresh_service', empties[0] if empties else None, 'high' if empties else 'medium', src_live)
    scans = [n for n, t in _with_type(live, T_SCAN).items() if t['publishers'] and not re.search(r'merge|filter', n)]
    rear = [n for n in scans if re.search(r'rear|back|scan2|_2$', n)]
    front = [n for n in scans if n not in rear]
    chosen = {}
    if front:
        chosen['front'] = sorted(front, key=lambda n: (n != '/scan', n))[0]
    if rear:
        chosen['rear'] = sorted(rear)[0]
    if not chosen:
        amcl_scan = _static_param(static, 'amcl', 'scan_topic')
        chosen = {'front': '/' + amcl_scan.lstrip('/')} if amcl_scan else {}
    p.set('scans', chosen, 'high' if front and len(front) == 1 and len(rear) <= 1 else 'medium',
          src_live if scans else static_src('amcl'), sorted(set(scans) - set(chosen.values())))

    # Safety controller, control mode, never-touch list
    texts = (live or {}).get('safety_text', {})
    service = next((s for s, text in texts.items() if 'Safety Controller Status' in text), None)
    if service:
        p.set('safety.service', service, 'high', 'safety status service (answered in the SMR300 format)')
        p.set('safety.parser', 'smr300_text', 'high', 'status text format')
        mode_now = re.search(r'Control Mode:\s*(\w+)', texts[service])
        if mode_now:
            p.set('teleop.restore_mode', mode_now.group(1), 'high', 'current control mode from the safety status')
    else:
        other = next(iter(texts), None)
        p.set('safety.service', None, 'low' if other else 'medium',
              f'{other} answered in an unknown format' if other else 'no safety status service found: escape and teleop override stay off')
        p.set('safety.parser', 'none', 'medium', 'no supported safety parser')
    modes = [n for n, t in _with_type(live, T_STRING).items() if 'mode' in n and t['subscribers']]
    p.set('mode', modes[0] if modes else None, 'high' if len(modes) == 1 else 'medium', src_live, modes[1:])
    nav_priority = min((i['priority'] for i in inputs.values()), default=0)
    guarded = sorted({i['topic'] for n, i in inputs.items() if i['priority'] > nav_priority and n.lower() not in ('tracker', 'navigation')})
    safety_services = sorted(s for s in services if service and s.startswith(service.rsplit('/', 1)[0] + '/') and s != service
                             and not s.rsplit('/', 1)[-1].startswith(('get_', 'set_', 'list_', 'describe_')))
    p.set('safety.never_touch', safety_services + guarded + ([p.value('mode')] if p.value('mode') else []), 'medium',
          'safety services, higher-priority velocity inputs and the control mode')

    # Logs and stations
    present = [n.rsplit('/', 1)[-1] for n in (live or {}).get('nodes', [])]
    log_nodes = [n for n in NAV2_NODES if n in present] + sorted({n for n in present if 'safety' in n})
    p.set('logs.nodes', log_nodes or ['planner_server', 'controller_server', 'bt_navigator', 'amcl'], 'high' if log_nodes else 'low', src_live)
    stations = {k: dict(s, label=k) for k, s in (static.get('stations') or {}).items()}  # names as written
    p.set('stations', stations, 'medium' if stations else 'low',
          f"named poses in {static['station_file']}" if stations else 'none found; add stations in setup')

    # An existing config wins unless the live graph contradicts it.
    if existing:
        for path, finding in list(p.fields.items()):
            current = _get(existing, path)
            if current is None or current == finding.value:
                continue
            if finding.confidence == 'high' and _contradicts(path, current, live):
                p.drift.append({'field': path, 'configured': current, 'observed': finding.value})
            else:
                p.set(path, current, 'high', 'existing configuration')
    return p


def _get(profile, path):
    """A proposal field's value in a profile. Some fields are stored in a different shape there."""
    alias = {'safety.service': 'safety.service', 'motion_output': 'motion_output.topic', 'odometry': 'odometry.topic',
             'localization': 'localization.topic', 'mode': 'mode.topic',
             'recovery.backup_action': 'recovery.escape.backup_action', 'recovery.spin_action': 'recovery.escape.spin_action'}
    node = profile
    for key in alias.get(path, path).split('.'):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if path == 'scans' and isinstance(node, dict):  # profile: {name: {topic, max_age_s}}; proposal: {name: topic}
        return {k: v.get('topic') if isinstance(v, dict) else v for k, v in node.items()}
    return node


def _contradicts(path, current, live):
    """A configured topic, action or service that the live graph does not have."""
    if live is None or not isinstance(current, str) or not current.startswith('/'):
        return False
    return current not in live.get('topics', {}) and current not in live.get('services', {}) and current not in live.get('actions', {})


def report(p):
    lines = []
    for path, f in sorted(p.fields.items()):
        mark = {'high': '✓', 'medium': '?', 'low': '!'}[f.confidence]
        alt = f"  (also: {', '.join(map(str, f.alternatives))})" if f.alternatives else ''
        lines.append(f'{mark} {path:38s} {json.dumps(f.value, default=str)[:70]:72s} {f.source}{alt}')
    for d in p.drift:
        lines.append(f"DRIFT {d['field']}: configured {d['configured']} but the robot has {d['observed']}")
    return '\n'.join(lines)


def main():
    import argparse
    from .contracts import Profile
    ap = argparse.ArgumentParser(description='Propose a Ripple robot profile from a workspace and the live ROS graph.')
    ap.add_argument('--workspace', type=Path, default=Path.cwd())
    ap.add_argument('--live', action='store_true', help='also read the running ROS graph')
    ap.add_argument('--existing', type=Path, help='current ripple.json or profile to check for drift')
    ap.add_argument('--out', type=Path, help='write the proposed profile here (JSON)')
    a = ap.parse_args()
    existing = None
    if a.existing and a.existing.exists():
        data = _load(a.existing)
        existing = data.get('profile', data) if isinstance(data, dict) else None
    p = propose(static_scan(a.workspace), live_scan() if a.live else None, existing)
    print(report(p))
    profile = p.profile()
    try:
        Profile.model_validate(profile)
        print('\nThe proposed profile is valid.')
    except Exception as exc:
        print('\nThe proposed profile does not validate yet:', str(exc).splitlines()[0])
    if a.out:
        a.out.write_text(json.dumps({'schema': 1, 'profile': profile}, indent=2) + '\n')
        print('wrote', a.out)


if __name__ == '__main__':
    main()
