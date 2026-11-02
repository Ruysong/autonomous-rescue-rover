"""ROS adapter for straight-line mock counts; no TF or actuator publishing."""

import sys

from nav_msgs.msg import Odometry

from rcl_interfaces.msg import ParameterDescriptor

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

from rover_localization.straight_odometry import (
    MockGeometry, Motion, StraightOdometry, diagonal_covariance, positive_finite,
)

from std_msgs.msg import Int64MultiArray


DEFAULTS = {
    'mock_only': True,
    'input_topic': '/wheel_ticks',
    'output_topic': '/mock/odom_wheel',
    'odom_frame': 'mock_odom',
    'base_frame': 'mock_base_link',
    'wheel_radius_m': 0.05,
    'counts_per_wheel_rev': 1000,
    'left_encoder_sign': 1,
    'right_encoder_sign': 1,
    'min_dt_s': 1e-6,
    'max_gap_s': 0.5,
    'max_tick_rate_per_s': 10000.0,
    'pose_variances': [0.01, 1e6, 1e6, 1e6, 1e6, 1e6],
    'twist_variances': [0.01, 1e6, 1e6, 1e6, 1e6, 1e6],
    'discontinuity_variance': 1e6,
}


class MockWheelOdom(Node):
    """Receive mock cumulative counts; stamp estimates with ROS receipt time."""

    def __init__(self, parameter_overrides: list[Parameter] | None = None):
        """Configure once and clean up the node if configuration fails."""
        super().__init__('mock_wheel_odom', parameter_overrides=parameter_overrides)
        try:
            self._configure()
        except Exception:
            self.destroy_node()
            raise

    def _configure(self) -> None:
        params = {}
        for name, default in DEFAULTS.items():
            params[name] = self.declare_parameter(
                name, default, ParameterDescriptor(read_only=True)).value
        if params['mock_only'] is not True:
            raise ValueError('B-01 implements mock_only=true; no hardware model exists')
        self.odom_frame = params['odom_frame']
        self.base_frame = params['base_frame']
        for name in ('odom_frame', 'base_frame'):
            frame = params[name]
            if not frame or frame.startswith('/') or any(char.isspace() for char in frame):
                raise ValueError(f'{name} needs a nonempty relative frame name')
        if self.odom_frame == self.base_frame:
            raise ValueError('odom_frame and base_frame must differ')
        if params['input_topic'] == params['output_topic']:
            raise ValueError('input_topic and output_topic must differ')
        geometry = MockGeometry(**{name: params[name] for name in (
            'wheel_radius_m', 'counts_per_wheel_rev', 'left_encoder_sign',
            'right_encoder_sign', 'min_dt_s', 'max_gap_s', 'max_tick_rate_per_s',
        )})
        self.estimator = StraightOdometry(geometry)
        self.pose_covariance = diagonal_covariance(params['pose_variances'])
        self.twist_covariance = diagonal_covariance(params['twist_variances'])
        self.discontinuity_variance = params['discontinuity_variance']
        positive_finite(self.discontinuity_variance, 'discontinuity_variance')
        if self.discontinuity_variance < self.pose_covariance[0]:
            raise ValueError('discontinuity_variance cannot reduce pose x uncertainty')
        qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=10,
                         reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.VOLATILE)
        self.publisher = self.create_publisher(Odometry, params['output_topic'], qos)
        self.subscription = self.create_subscription(
            Int64MultiArray, params['input_topic'], self._on_ticks, qos)
        self.received_count = 0
        self.last_status = 'waiting'
        self.get_logger().warning(
            'MOCK ONLY: straight-line assumption, ROS receipt timestamps, no TF. '
            'Dimensions/CPR/covariances are mock values, not measured hardware specs.')

    def _on_ticks(self, message: Int64MultiArray) -> None:
        self.received_count += 1
        previous_status = self.last_status
        if message.layout.dim or message.layout.data_offset != 0:
            self.estimator.invalidate()
            self.last_status = 'invalid_layout'
            motion = None
        else:
            update = self.estimator.update(
                tuple(message.data), self.get_clock().now().nanoseconds)
            self.last_status, motion = update.status, update.motion
        if motion is not None:
            self.publisher.publish(self.to_message(motion))
        elif self.last_status != previous_status and self.last_status != 'baseline':
            self.get_logger().warning(
                f'No odometry output: {self.last_status}; reset/rebase policy applied.')

    def to_message(self, motion: Motion) -> Odometry:
        """Pose uses the parent frame; twist uses the child frame; yaw is assumed."""
        message = Odometry()
        message.header.stamp.sec = motion.stamp_ns // 1_000_000_000
        message.header.stamp.nanosec = motion.stamp_ns % 1_000_000_000
        message.header.frame_id = self.odom_frame
        message.child_frame_id = self.base_frame
        message.pose.pose.position.x = motion.x_m
        message.pose.pose.orientation.w = 1.0
        message.twist.twist.linear.x = motion.linear_velocity_mps
        pose_covariance = self.pose_covariance.copy()
        if not motion.continuous:
            pose_covariance[0] = self.discontinuity_variance
        message.pose.covariance = pose_covariance
        message.twist.covariance = self.twist_covariance
        return message


def main(args: list[str] | None = None) -> int:
    """Own one context/node lifecycle, including external shutdown."""
    rclpy.init(args=args)
    node = None
    try:
        node = MockWheelOdom()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        return 0
    except (ValueError, TypeError) as exc:
        print(f'mock_wheel_odom configuration error: {exc}', file=sys.stderr)
        return 1
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0
