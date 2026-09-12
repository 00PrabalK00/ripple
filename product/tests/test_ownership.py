import unittest
from unittest.mock import patch
from types import SimpleNamespace
from ripple_edge.ownership import Ownership

class OwnershipTests(unittest.TestCase):
    def lease(self):
        node=SimpleNamespace(profile=SimpleNamespace(navigation=SimpleNamespace(
            navigate_action='/navigate_to_pose',internal_action_clients=['/bt_navigator'],goal_input_topics=['/goal_pose'])),
            count_publishers=lambda topic:0,get_name=lambda:'edge',get_namespace=lambda:'/',
            get_node_names_and_namespaces=lambda:[('bt_navigator','/')])
        lease=Ownership.__new__(Ownership);lease.fd=1;lease.node=node
        return lease
    def test_nav2_self_client_requires_declared_server_and_no_goal_publisher(self):
        lease=self.lease()
        with patch('ripple_edge.ownership.get_action_client_names_and_types_by_node',return_value=[('/navigate_to_pose',[])]) as clients, patch('ripple_edge.ownership.get_action_server_names_and_types_by_node',return_value=[('/navigate_to_pose',[])]) as servers:
            self.assertTrue(lease.available())
            servers.return_value=[]
            self.assertFalse(lease.available())
            servers.return_value=[('/navigate_to_pose',[])]
            lease.node.count_publishers=lambda topic:1
            self.assertFalse(lease.available())
    def test_external_client_is_not_exempted(self):
        lease=self.lease();lease.node.get_node_names_and_namespaces=lambda:[('other_operator','/')]
        with patch('ripple_edge.ownership.get_action_client_names_and_types_by_node',return_value=[('/navigate_to_pose',[])]):
            self.assertFalse(lease.available())
