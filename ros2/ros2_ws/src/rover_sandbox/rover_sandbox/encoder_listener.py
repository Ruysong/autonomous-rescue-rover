import rclpy
from rclpy.node import Node
from std_msgs.msg import Int64MultiArray


class EncoderListener(Node):

    def __init__(self):
        super().__init__('encoder_listener')

        self.subscription = self.create_subscription(
            Int64MultiArray,
            '/wheel_ticks',
            self.listener_callback,
            10
        )

        self.get_logger().info('Encoder Listener Started')

    def listener_callback(self, msg):
        left = msg.data[0]
        right = msg.data[1]

        self.get_logger().info(
            f'Received: L={left}, R={right}'
        )


def main(args=None):
    rclpy.init(args=args)

    node = EncoderListener()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()