"""Exclusive local edge lease plus detection of other navigation clients."""
import fcntl
import os
from pathlib import Path
from rclpy.action import get_action_client_names_and_types_by_node, get_action_server_names_and_types_by_node

class Ownership:
    def __init__(self,node):
        self.node=node
        directory=Path.home()/'.local/state/ripple/owners'
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        name=str(node.profile.domain_id)+'-'+node.profile.robot.encode().hex()+'.lock'
        self.fd=os.open(directory/name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except Exception:
            os.close(self.fd);self.fd=None;raise RuntimeError('Another edge owns this robot')

    def available(self):
        if self.fd is None:return False
        try:
            if any(self.node.count_publishers(topic)>0 for topic in self.node.profile.navigation.goal_input_topics):return False
            for name,namespace in self.node.get_node_names_and_namespaces():
                if name==self.node.get_name() and namespace==self.node.get_namespace():continue
                clients=get_action_client_names_and_types_by_node(self.node,name,namespace)
                full=namespace.rstrip('/')+'/'+name
                if full in self.node.profile.navigation.internal_action_clients:
                    servers=get_action_server_names_and_types_by_node(self.node,name,namespace)
                    if any(action==self.node.profile.navigation.navigate_action for action,_ in servers):continue
                if any(action==self.node.profile.navigation.navigate_action for action,_ in clients):return False
            return True
        except Exception:return False

    def close(self):
        if self.fd is not None:
            fcntl.flock(self.fd,fcntl.LOCK_UN);os.close(self.fd);self.fd=None
