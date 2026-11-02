import rclpy
from rclpy.node import Node
from std_msgs.msg import Int64MultiArray


class MockEncoder(Node):

    def __init__(self):
        super().__init__('mock_encoder')

        self.publisher = self.create_publisher(
            Int64MultiArray,
            '/wheel_ticks',
            10
        )

        self.left_count = 0
        self.right_count = 0

        # 0.1초마다 timer_callback 호출 (10Hz)
        self.timer = self.create_timer(
            0.1,
            self.timer_callback
        )

        self.get_logger().info('Mock Encoder Started')

    def timer_callback(self):
        # 가상의 엔코더 누적 카운트 증가
        self.left_count += 10
        self.right_count += 10

        msg = Int64MultiArray()
        msg.data = [self.left_count, self.right_count]

        self.publisher.publish(msg)

        self.get_logger().info(
            f'Publishing: L={self.left_count}, R={self.right_count}'
        )


def main(args=None):
    rclpy.init(args=args)

    node = MockEncoder()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()