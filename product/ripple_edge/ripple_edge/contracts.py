"""Versioned wire contracts. Policy is enforced in the edge, never in model text."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Endpoint(Strict):
    topic: str
    max_age_s: float = Field(gt=0, le=60)

class MotionInput(Endpoint):
    priority: int = Field(ge=0, le=255)

class Safety(Strict):
    required: bool
    service: str | None
    parser: Literal['smr300_text', 'none']
    max_age_s: float = Field(gt=0, le=10)
    never_touch: list[str]
    @model_validator(mode='after')
    def valid(self):
        if self.required and (not self.service or self.parser=='none'):
            raise ValueError('Required safety needs a declared service and parser')
        return self

class Thresholds(Strict):
    speed_below: float = Field(gt=0, le=.1)
    turn_below: float = Field(gt=0, le=.1)
    halted_for_s: float = Field(gt=0)
    flat_for_s: float = Field(gt=0)
    min_progress_m: float = Field(gt=0)
    goal_tolerance_m: float = Field(gt=0)
    failure_count: int = Field(gt=0)
    failure_window_s: float = Field(gt=0)
    localization_degraded_s: float = Field(gt=0)

class ActionPolicy(Strict):
    autonomy: Literal['off','suggest','ask','auto']
    per_incident: int = Field(ge=0, le=10)

class Escape(ActionPolicy):
    backup_action: str
    spin_action: str
    max_backup_m: float = Field(gt=0, le=1)
    max_backup_mps: float = Field(gt=0, le=.3)
    max_spin_rad: float = Field(gt=0, le=3.15)
    requires: list[str]

class Navigation(Strict):
    navigate_action: str
    internal_action_clients: list[str] = Field(default_factory=list)
    goal_input_topics: list[str] = Field(default_factory=list)
    planner_action: str
    lifecycle_nodes: list[str]
    clear_services: dict[Literal['local','global'],str]
    keepout_adapter: Literal['layers_file','none']
    keepout_path: str | None
    verify_mask: str | None
    map_topic: str
    global_frame: str
    base_frame: str

class Station(Strict):
    frame: str
    x: float
    y: float
    yaw: float

class Missions(Strict):
    channel: str
    operators: list[str]
    operator_command: Literal['go']
    inferred_destination: Literal['go_announce','ask']
    agent_initiated: Literal['ask']
    dispatch_checks: list[str]

class Recovery(Strict):
    clear_costmaps: ActionPolicy
    retry_goal: ActionPolicy
    lifecycle_reset: ActionPolicy
    reset_nodes: list[str]
    escape: Escape

class Logs(Strict):
    rosout_topic: str
    nodes: list[str]
    min_level: int = Field(ge=0,le=50)
    journald_services: list[str]

class Profile(Strict):
    schema_version: Literal[1]
    robot: str = Field(min_length=1)
    ros_distro: Literal['humble']
    domain_id: int = Field(ge=0,le=232)
    navigation: Navigation
    safety: Safety
    motion_inputs: dict[str,MotionInput]
    motion_output: Endpoint
    odometry: Endpoint
    localization: Endpoint
    localization_refresh_service: str | None
    covariance_xy_max: float = Field(gt=0)
    covariance_yaw_max: float = Field(gt=0)
    scans: dict[str,Endpoint]
    mode: Endpoint
    triggers: Thresholds
    logs: Logs
    missions: Missions
    recovery: Recovery
    stations: dict[str,Station]
    escalation_channel: str
    reply_timeout_s: float = Field(gt=0)
    @model_validator(mode='after')
    def consistent(self):
        checks={'station_registered','facts_fresh','keepouts_verified','route_probe_ok','safety_not_holding','mode_not_manual','single_owner'}
        if not checks.issubset(self.missions.dispatch_checks):
            raise ValueError('Mandatory dispatch checks cannot be removed')
        e=self.recovery.escape
        required={'safety_present','safety_not_holding','mode_not_manual',
                  'localization_fresh','no_active_goal','rear_scan_fresh'}
        if e.autonomy!='off' and (not self.safety.required or not required.issubset(e.requires)):
            raise ValueError('Escape requires safety and every mandatory precondition')
        if not set(self.recovery.reset_nodes).issubset(self.navigation.lifecycle_nodes):
            raise ValueError('Lifecycle reset must be limited to declared nodes')
        if len({m.priority for m in self.motion_inputs.values()}) != len(self.motion_inputs):
            raise ValueError('Motion priorities must be unambiguous')
        return self

class Observation(Strict):
    value: Any
    source: str
    age_s: float | None = Field(default=None,ge=0)
    fresh: bool

class Event(Strict):
    schema_version: Literal[1] = 1
    id: str
    robot: str
    kind: Literal['halted_while_commanded','no_progress','nav_failures','node_not_active','localization_degraded','cause_changed']
    cause: Literal['safety_obstacle','emergency_stop','localization_stop','manual_control','nav2_stall','component_down','unknown']
    evidence: dict[str,Observation]
    observed_at: str
    derived: Literal[True] = True

class ToolResult(Strict):
    schema_version: Literal[1] = 1
    robot: str
    request_id: str
    status: Literal['ok','denied','unknown','failed']
    reason: str
    observations: dict[str,Observation]
    event_ids: list[str]
    verified: bool

class ObserveRequest(Strict):
    request_id: str = Field(min_length=1)
    tool: Literal['get_robot_health','get_pose','get_nav_status','get_diagnostics','get_recent_logs','get_events']

class NavigateRequest(Strict):
    request_id: str = Field(min_length=1)
    tool: Literal['set_goal']
    station: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1,description='Edge-verifiable record from the authenticated operator channel, never identity asserted by the model')

class EscapeRequest(Strict):
    request_id: str = Field(min_length=1)
    tool: Literal['escape']
    primitive: Literal['backup','spin']
    amount: float = Field(gt=0)
    speed: float = Field(gt=0)
    incident_id: str = Field(min_length=1)
    authorization_id: str | None = None

class RecoveryRequest(Strict):
    request_id: str = Field(min_length=1)
    tool: Literal['clear_costmap','lifecycle_reset','cancel_goal']
    incident_id: str = Field(min_length=1)
    target: str = Field(min_length=1,description='local/global costmap, declared node, or owned goal ID')
    authorization_id: str | None = None
