"""Patterns grounded in recorded SMR300 Humble runs, not model-invented codes."""
import re
PATTERNS=[
    ('controller_no_progress',r'Failed to make progress'),
    ('planner_no_path',r'Planning algorithm\s+failed to generate a valid path'),
    ('planner_tolerance_failure',r'failed to create plan with tolerance'),
    ('navigation_aborted',r'\[navigate_to_pose\].*Aborting handle'),
    ('tf_extrapolation',r'Lookup would require extrapolation'),
    ('service_response_timeout',r'failed to send response to .*get_state \(timeout\)'),
]

def classify(node,text):
    return [{'finding':name,'node':node,'evidence':text,'derived':True}
            for name,pattern in PATTERNS if re.search(pattern,text)]
