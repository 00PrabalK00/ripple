"""Observe-only ROS boundary: subscriptions and declared read-only services only."""
import math
import time
from collections import deque
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
from geometry_msgs.msg import Twist, PoseWithCovarianceStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from std_srvs.srv import Trigger
from lifecycle_msgs.srv import GetState
from action_msgs.msg import GoalStatusArray
from nav2_msgs.action import NavigateToPose
from rcl_interfaces.msg import Log
from .detector import Detector, parse_smr300
from .logs import classify
from .polling import ReadRequests

class Observer(Node):
    def __init__(self,profile):
        super().__init__('ripple_edge_observer')
        self.profile=profile;self.detector=Detector(profile,time.monotonic)
        self.events=deque(maxlen=200);self.findings=deque(maxlen=100)
        self.active=set();self.terminal=set();self.requests=ReadRequests();self.node_states={}
        self.latched=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(Odometry,profile.odometry.topic,self.odom,qos_profile_sensor_data)
        self.create_subscription(PoseWithCovarianceStamped,profile.localization.topic,self.pose,self.latched)
        if profile.mode:
            self.create_subscription(String,profile.mode.topic,
                lambda m:self.detector.observe('mode',m.data,profile.mode.topic,profile.mode.max_age_s),10)
        for name,cfg in profile.motion_inputs.items():
            self.create_subscription(Twist,cfg.topic,lambda m,n=name,c=cfg:self.velocity(n,c,m),qos_profile_sensor_data)
        self.create_subscription(Twist,profile.motion_output.topic,
            lambda m:self.velocity('output',profile.motion_output,m),qos_profile_sensor_data)
        for name,cfg in profile.scans.items():
            self.create_subscription(LaserScan,cfg.topic,lambda m,n=name,c=cfg:self.scan(n,c,m),qos_profile_sensor_data)
        action=profile.navigation.navigate_action
        self.create_subscription(GoalStatusArray,action+'/_action/status',self.status,self.latched)
        self.create_subscription(NavigateToPose.Impl.FeedbackMessage,action+'/_action/feedback',self.feedback,10)
        self.create_subscription(Log,profile.logs.rosout_topic,self.log,100)
        self.safety=self.create_client(Trigger,profile.safety.service) if profile.safety.service else None
        self.lifecycle={n:self.create_client(GetState,n+'/get_state') for n in profile.navigation.lifecycle_nodes}
        self.create_timer(.25,self.tick)
        self.create_timer(.5,self.read_safety)
        self.create_timer(2.,self.read_lifecycle)

    def velocity(self,name,cfg,msg):
        values=[msg.linear.x,msg.linear.y,msg.angular.z]
        if not all(math.isfinite(v) for v in values):return
        self.detector.observe('velocity.'+name,{'linear':math.hypot(*values[:2]),'angular':values[2]},cfg.topic,cfg.max_age_s)

    def odom(self,msg):
        v=msg.twist.twist;values=[v.linear.x,v.linear.y,v.angular.z]
        if not all(math.isfinite(x) for x in values):return
        self.detector.observe('odometry',{'linear':math.hypot(*values[:2]),'angular':values[2]},self.profile.odometry.topic,self.profile.odometry.max_age_s)
        p=msg.pose.pose;q=p.orientation
        if all(math.isfinite(x) for x in (p.position.x,p.position.y,q.z,q.w)):
            self.detector.observe('odometry_pose',{'x':p.position.x,'y':p.position.y,'yaw':2*math.atan2(q.z,q.w)},
                self.profile.odometry.topic,self.profile.odometry.max_age_s)

    def pose(self,msg):
        cov=msg.pose.covariance;p=self.profile
        ok=(all(math.isfinite(v) for v in cov) and 0<=cov[0]<=p.covariance_xy_max
            and 0<=cov[7]<=p.covariance_xy_max and 0<=cov[35]<=p.covariance_yaw_max)
        self.detector.observe('localization',bool(ok),p.localization.topic,p.localization.max_age_s)
        pos=msg.pose.pose.position;q=msg.pose.pose.orientation
        if all(math.isfinite(v) for v in (pos.x,pos.y,q.z,q.w)):
            self.detector.observe('pose',{'frame':msg.header.frame_id,'x':pos.x,'y':pos.y,'yaw':2*math.atan2(q.z,q.w)},
                p.localization.topic,p.localization.max_age_s)

    def scan(self,name,cfg,msg):
        values=[v for v in msg.ranges if math.isfinite(v) and msg.range_min<=v<=msg.range_max]
        self.detector.observe('scan.'+name,{'minimum_m':min(values) if values else None,'valid_points':len(values)},cfg.topic,cfg.max_age_s)

    def status(self,msg):
        self.active={bytes(s.goal_info.goal_id.uuid).hex() for s in msg.status_list if s.status in (1,2,3)}
        for s in msg.status_list:
            gid=bytes(s.goal_info.goal_id.uuid).hex()
            if s.status in (4,5,6):self.terminal.add(gid)
            if s.status==6:self.detector.failure(gid)
        if not self.active:self.detector.observe('goal',{'active':False,'id':None},'Nav2 status',2)

    def feedback(self,msg):
        gid=bytes(msg.goal_id.uuid).hex()
        if gid in self.terminal:return
        self.detector.observe('goal',{'active':True,'id':gid},'Nav2 feedback',2)
        distance=msg.feedback.distance_remaining
        if math.isfinite(distance):self.detector.observe('distance_remaining',distance,'Nav2 feedback',2)

    def log(self,msg):
        if msg.level<self.profile.logs.min_level or msg.name.split('.')[-1] not in self.profile.logs.nodes:return
        self.findings.extend(classify(msg.name,msg.msg[:2000]))

    def read_safety(self):
        if not self.safety or not self.safety.service_is_ready():return
        if not self.requests.ready('safety'):return
        f=self.safety.call_async(Trigger.Request())
        self.requests.track('safety',f,self.profile.safety.max_age_s)
        def done(f):
            if not self.requests.accept('safety',f):return
            try:
                r=f.result();value=parse_smr300(r.success,r.message)
                self.detector.observe('safety',value,self.profile.safety.service,self.profile.safety.max_age_s)
                if value.get('known'):self.detector.observe('mode',value['mode'],self.profile.safety.service,
                    self.profile.mode.max_age_s if self.profile.mode else self.profile.safety.max_age_s)
            except Exception:pass # Old observations age out.
        f.add_done_callback(done)

    def read_lifecycle(self):
        for name,client in self.lifecycle.items():
            if not client.service_is_ready():continue
            if not self.requests.ready(name):continue
            f=client.call_async(GetState.Request())
            self.requests.track(name,f,5.)
            def done(f,n=name):
                if not self.requests.accept(n,f):return
                try:self.node_states[n]=(f.result().current_state.label,time.monotonic())
                except Exception:pass
            f.add_done_callback(done)

    def tick(self):
        now=time.monotonic()
        states={n:state for n,(state,at) in self.node_states.items() if now-at<5}
        self.detector.observe('lifecycle',states,'declared GetState services',2)
        if self.profile.mode is None:
            self.detector.observe('mode','autonomous','profile declares no manual mode',2)
        self.events.extend(self.detector.tick())

    def snapshot(self):
        facts=self.detector.snapshot()
        return {'schema_version':1,'robot':self.profile.robot,'mode':'observe_only',
                'observations':{k:v.model_dump() for k,v in facts.items()},
                'velocity_winner':{'value':self.detector.winner(facts),'derived':True,'method':'fresh input with greatest configured priority'},
                'cause':{'value':self.detector.cause(facts),'derived':True},
                'events':[e.model_dump() for e in self.events], 'findings':list(self.findings)}
