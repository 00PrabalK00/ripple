"""ROS Humble adapter. Callbacks only emit events for the single state writer."""
import math
from time import monotonic
from uuid import UUID

from action_msgs.msg import GoalStatus, GoalStatusArray
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose, ComputePathToPose
from nav2_msgs.srv import ClearEntireCostmap
from rcl_interfaces.msg import Log
from tf2_ros import Buffer, TransformListener
from rclpy.time import Time
from nav_msgs.msg import Odometry, OccupancyGrid
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import String
from std_srvs.srv import Trigger, Empty
from unique_identifier_msgs.msg import UUID as RosUUID

from .contracts import RobotEvent
from .guards import parse_safety_status


class Nav2Adapter(Node):
    def __init__(self, events):
        super().__init__('ripple_robot_adapter')
        self.events = events
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.create_timer(.5, self._check_tf)
        self.create_subscription(Log, '/rosout', self._log, 100)
        self.planner = ActionClient(self, ComputePathToPose, '/compute_path_to_pose')
        self.clear_clients = [self.create_client(ClearEntireCostmap, path) for path in
            ('/global_costmap/clear_entirely_global_costmap', '/local_costmap/clear_entirely_local_costmap')]
        self.client = ActionClient(self, NavigateToPose, '/navigate_to_pose')
        self.handles = {}
        self.pose = None
        self.pose_at = None
        self.keepout_mask = None
        self.global_costmap = None
        self.safety_pending = None
        self.localization_pending = None
        self.create_subscription(Odometry, '/diff_cont/odom', self._odom, 10)
        amcl_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self._pose, amcl_qos)
        self.create_subscription(String, '/control_mode', self._mode, 10)
        self.create_subscription(GoalStatusArray, '/navigate_to_pose/_action/status',
            lambda msg: self.emit('action_status', active=[
                bytes(s.goal_info.goal_id.uuid).hex() for s in msg.status_list
                if s.status in (1, 2, 3)]), amcl_qos)
        self.create_subscription(OccupancyGrid, '/keepout_filter_mask',
            lambda msg: setattr(self, 'keepout_mask', (msg, monotonic())), amcl_qos)
        self.create_subscription(OccupancyGrid, '/global_costmap/costmap',
            lambda msg: setattr(self, 'global_costmap', (msg, monotonic())), 1)
        self.safety = self.create_client(Trigger, '/safety/status')
        self.create_timer(0.25, self._poll_safety)
        self.localization_update = self.create_client(Empty, '/request_nomotion_update')
        self.create_timer(1.0, self._refresh_localization)

    def _check_tf(self):
        try:
            transform = self.tf_buffer.lookup_transform('map', 'base_link', Time())
            age = (self.get_clock().now().nanoseconds - Time.from_msg(transform.header.stamp).nanoseconds)/1e9
            self.emit('tf_health', healthy=0 <= age < 1, age_seconds=age)
        except Exception:
            self.emit('tf_health', healthy=False, age_seconds=None)

    def _log(self, msg):
        if msg.level >= 30 and any(name in msg.name for name in
            ('planner_server','controller_server','bt_navigator','amcl','behavior_server','costmap')):
            self.emit('navigation_log', node=msg.name, level=msg.level, message=msg.msg[:1200])

    def clear_costmaps(self, incident_id):
        if not all(c.service_is_ready() for c in self.clear_clients):
            self.emit('costmaps_cleared', incident_id, success=False)
            return
        remaining = [len(self.clear_clients)]
        errors = []
        def complete(f):
            try: f.result()
            except Exception: errors.append(True)
            remaining[0] -= 1
            if not remaining[0]: self.emit('costmaps_cleared', incident_id, success=not errors)
        for client in self.clear_clients:
            client.call_async(ClearEntireCostmap.Request()).add_done_callback(complete)

    def probe(self, incident_id, target):
        if not self.planner.server_is_ready():
            self.emit('recovery_plan', incident_id, success=False, points=0)
            return
        goal = ComputePathToPose.Goal()
        goal.goal.header.frame_id = target['frame']
        goal.goal.pose.position.x = float(target['x'])
        goal.goal.pose.position.y = float(target['y'])
        goal.goal.pose.orientation.w = 1.
        goal.use_start = False
        def result(f):
            try:
                r=f.result();n=len(r.result.path.poses)
                self.emit('recovery_plan', incident_id, success=r.status == 4 and n>0, points=n)
            except Exception: self.emit('recovery_plan', incident_id, success=False, points=0)
        def accepted(f):
            try:
                h=f.result()
                if not h.accepted: raise ValueError('Planner rejected probe')
                h.get_result_async().add_done_callback(result)
            except Exception: self.emit('recovery_plan', incident_id, success=False, points=0)
        self.planner.send_goal_async(goal).add_done_callback(accepted)

    def _refresh_localization(self):
        # AMCL normally publishes after movement. Ask it to process a real laser
        # observation when stationary instead of treating cached pose as fresh.
        if self.localization_pending and not self.localization_pending.done():
            return
        if self.localization_update.service_is_ready():
            self.localization_pending = self.localization_update.call_async(Empty.Request())

    def emit(self, kind, goal_id=None, **data):
        self.events.put(RobotEvent(kind, goal_id, dict(received_at=monotonic(), **data)))

    def send(self, goal_id, target):
        if not self.client.server_is_ready():
            self.emit('rejected', goal_id, reason='Nav2 action server unavailable')
            return
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = target.frame
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x, goal.pose.pose.position.y = float(target.x), float(target.y)
        goal.pose.pose.orientation.z = math.sin(target.yaw / 2)
        goal.pose.pose.orientation.w = math.cos(target.yaw / 2)
        future = self.client.send_goal_async(
            goal, goal_uuid=RosUUID(uuid=list(UUID(goal_id).bytes)),
            feedback_callback=lambda msg: self.emit(
                'feedback', goal_id, distance_remaining=msg.feedback.distance_remaining))
        future.add_done_callback(lambda f: self._accepted(goal_id, f))

    def _accepted(self, goal_id, future):
        try:
            handle = future.result()
            if not handle.accepted:
                self.emit('rejected', goal_id, reason='Nav2 rejected goal')
                return
            self.handles[goal_id] = handle
            self.emit('accepted', goal_id)
            handle.get_result_async().add_done_callback(lambda f: self._result(goal_id, f))
        except Exception as error:
            self.emit('uncertain', goal_id, reason=str(error))

    def _result(self, goal_id, future):
        try:
            status = future.result().status
            outcome = {GoalStatus.STATUS_SUCCEEDED: 'SUCCEEDED',
                       GoalStatus.STATUS_CANCELED: 'CANCELED',
                       GoalStatus.STATUS_ABORTED: 'ABORTED'}.get(status)
            if outcome is None:
                self.emit('uncertain', goal_id, reason=f'unexpected result status {status}')
                return
            self.handles.pop(goal_id, None)
            self.emit('terminal', goal_id, outcome=outcome, final_pose=self.pose,
                      pose_received_at=self.pose_at)
        except Exception as error:
            self.emit('uncertain', goal_id, reason=str(error))

    def cancel(self, goal_id):
        handle = self.handles.get(goal_id)
        if handle is None:
            raise RuntimeError('owned accepted goal handle is unavailable')
        handle.cancel_goal_async().add_done_callback(lambda f: self._cancel_ack(goal_id, f))

    def _cancel_ack(self, goal_id, future):
        try:
            response = future.result()
            self.emit('cancel_ack', goal_id, return_code=response.return_code)
        except Exception as error:
            self.emit('uncertain', goal_id, reason=str(error))

    def _odom(self, msg):
        velocity = msg.twist.twist
        self.emit('odometry', linear_speed=math.hypot(velocity.linear.x, velocity.linear.y),
                  angular_speed=velocity.angular.z)

    def _pose(self, msg):
        p = msg.pose.pose
        self.pose = dict(frame=msg.header.frame_id, x=p.position.x, y=p.position.y,
                         qz=p.orientation.z, qw=p.orientation.w)
        self.pose_at = monotonic()
        self.emit('localization', pose=self.pose, covariance=list(msg.pose.covariance))

    def _mode(self, msg):
        self.emit('mode', value=msg.data.strip().lower())

    def _poll_safety(self):
        if self.safety_pending is not None and not self.safety_pending.done():
            return  # No response means the supervisor's last fact ages out.
        if self.safety.service_is_ready():
            self.safety_pending = self.safety.call_async(Trigger.Request())
            self.safety_pending.add_done_callback(self._safety_result)

    def _safety_result(self, future):
        try:
            response = future.result()
            self.emit('safety', clear=parse_safety_status(response.success, response.message),
                      raw=response.message)
        except Exception as error:
            self.emit('safety', clear=False, raw=str(error))
