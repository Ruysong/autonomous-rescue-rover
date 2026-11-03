"""Bounded local ROS graphs using existing mock counts and synthetic /clock."""

import importlib.util
import math
import time
from pathlib import Path

from nav_msgs.msg import Odometry

import pytest

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.parameter import Parameter

from rosgraph_msgs.msg import Clock

from rover_localization.mock_wheel_odom import MockWheelOdom

from std_msgs.msg import Int64MultiArray


class Graph:
    """Every wait is bounded and all nodes belong to this test process."""

    def __init__(self):
        """Collect only nodes created by this test."""
        self.executor = SingleThreadedExecutor()
        self.nodes = []

    def add(self, node):
        """Own a node and register it with the test executor."""
        self.nodes.append(node)
        self.executor.add_node(node)
        return node

    def until(self, predicate, timeout_s=5.0):
        """Spin until a condition or a bounded failure."""
        deadline = time.monotonic() + timeout_s
        while not predicate() and time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.02)
        assert predicate(), 'local mock ROS graph did not meet condition before deadline'

    def spin_for(self, duration_s):
        """Exercise a paused source for a short wall-time interval."""
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.01)


@pytest.fixture
def graph():
    """Own one ROS context and tear it down on every test exit."""
    rclpy.init()
    instance = Graph()
    try:
        yield instance
    finally:
        instance.executor.shutdown(timeout_sec=2.0)
        for node in reversed(instance.nodes):
            instance.executor.remove_node(node)
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def test_existing_mock_encoder_to_odometry_without_tf(graph):
    """Verify the new subscriber against the unchanged encoder publisher."""
    # Load the existing source without editing/building the sandbox package.
    source = (Path(__file__).resolve().parents[2] / 'rover_sandbox' /
              'rover_sandbox' / 'mock_encoder.py')
    spec = importlib.util.spec_from_file_location('b01_existing_mock_encoder', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    odom = graph.add(MockWheelOdom())
    probe = graph.add(rclpy.create_node('b01_probe'))
    received = []
    subscription = probe.create_subscription(Odometry, '/mock/odom_wheel', received.append, 10)
    encoder = graph.add(module.MockEncoder())
    graph.until(lambda: len(received) >= 3)
    encoder.timer.cancel()
    step_m = 10 * math.tau * 0.05 / 1000
    for previous, message in zip(received, received[1:]):
        delta_m = message.pose.pose.position.x - previous.pose.pose.position.x
        assert delta_m == pytest.approx(step_m)
        assert math.isfinite(message.twist.twist.linear.x)
        assert message.twist.twist.linear.x > 0
    assert received[-1].header.frame_id == 'mock_odom'
    assert received[-1].child_frame_id == 'mock_base_link'
    assert received[-1].pose.pose.orientation.w == 1
    assert received[-1].pose.pose.position.y == 0
    assert received[-1].twist.twist.angular.z == 0
    assert received[-1].pose.covariance[7] > received[-1].pose.covariance[0]
    endpoint = subscription.get_publisher_count()
    assert endpoint >= 1
    topics = odom.get_publisher_names_and_types_by_node(odom.get_name(), odom.get_namespace())
    assert '/tf' not in dict(topics) and '/tf_static' not in dict(topics)


def test_sim_clock_stop_reverse_rejection_pause_and_restart(graph):
    """Check exact ROS-time output, discontinuity and replay restart behavior."""
    odom = graph.add(MockWheelOdom(parameter_overrides=[
        Parameter('use_sim_time', value=True),
        Parameter('input_topic', value='/b01_test_ticks'),
        Parameter('output_topic', value='/b01_test_odom'),
    ]))
    probe = graph.add(rclpy.create_node('b01_clock_probe'))
    clock_pub = probe.create_publisher(Clock, '/clock', 10)
    ticks_pub = probe.create_publisher(Int64MultiArray, '/b01_test_ticks', 10)
    received = []
    subscription = probe.create_subscription(Odometry, '/b01_test_odom', received.append, 10)
    graph.until(lambda: clock_pub.get_subscription_count() > 0
                and ticks_pub.get_subscription_count() > 0
                and subscription.get_publisher_count() > 0)

    def sample(stamp_ns, counts, expected_status):
        clock = Clock()
        clock.clock.sec = stamp_ns // 1_000_000_000
        clock.clock.nanosec = stamp_ns % 1_000_000_000
        clock_pub.publish(clock)
        graph.until(lambda: odom.get_clock().now().nanoseconds == stamp_ns)
        previous_count = odom.received_count
        ticks_pub.publish(Int64MultiArray(data=counts))
        graph.until(lambda: odom.received_count > previous_count)
        assert odom.last_status == expected_status

    sample(0, [0, 0], 'clock_unstarted')
    sample(1_000_000_000, [100, 100], 'baseline')
    sample(1_100_000_000, [110, 110], 'ok')
    graph.until(lambda: len(received) == 1)
    step_m = 10 * math.tau * 0.05 / 1000
    assert received[-1].pose.pose.position.x == pytest.approx(step_m)
    assert received[-1].twist.twist.linear.x == pytest.approx(step_m / 0.1)
    assert received[-1].header.stamp.sec == 1
    assert received[-1].header.stamp.nanosec == 100_000_000
    sample(1_200_000_000, [110, 110], 'ok')
    graph.until(lambda: len(received) == 2)
    assert received[-1].twist.twist.linear.x == 0
    sample(1_300_000_000, [100, 100], 'ok')
    graph.until(lambda: len(received) == 3)
    assert received[-1].pose.pose.position.x == pytest.approx(0)
    assert received[-1].twist.twist.linear.x < 0
    sample(1_400_000_000, [110, 100], 'unsupported_turn')
    sample(1_500_000_000, [120, 110], 'ok')
    graph.until(lambda: len(received) == 4)
    assert received[-1].pose.covariance[0] == 1e6
    sample(500_000_000, [0, 0], 'clock_restarted')
    sample(600_000_000, [10, 10], 'ok')
    graph.until(lambda: len(received) == 5)
    assert received[-1].pose.pose.position.x == pytest.approx(step_m)
    assert received[-1].pose.covariance[0] == 0.01
    graph.spin_for(0.15)  # Paused /clock and no encoder input must not fabricate zero speed.
    assert len(received) == 5


@pytest.mark.parametrize(('name', 'value'), [
    ('mock_only', False), ('wheel_radius_m', 0.0),
    ('counts_per_wheel_rev', 0), ('left_encoder_sign', 0),
    ('odom_frame', '/odom'), ('base_frame', 'mock_odom'),
    ('pose_variances', [0.0] * 6), ('discontinuity_variance', 0.001),
])
def test_invalid_node_configuration_fails_without_leaking_node(graph, name, value):
    """Reject an invalid node configuration within the fixture-owned context."""
    with pytest.raises(ValueError):
        MockWheelOdom(parameter_overrides=[Parameter(name, value=value)])
