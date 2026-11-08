#!/usr/bin/env python3
"""A neutral four-topic rig for screenshots and demos.

It exists so a demo can show every health state without exposing anyone's real
namespace. Run it on the robot you want to film:

    source /opt/ros/<distro>/setup.bash
    python3 demo/rig.py

It publishes and subscribes only under /demo:

    /demo/status      TRANSIENT_LOCAL String, one message  -> 1 pub, 0 sub  = `state`
    /demo/chatter     String at 10 Hz, with a subscriber   -> 1 pub, 1 sub  = `live`
    /demo/orphan_in   no publisher, one subscriber         -> 0 pub, 1 sub  = `dead_input`
    /demo/orphan_out  String at 1 Hz, no subscriber        -> 1 pub, 0 sub  = `dead_output`

`/demo/status` is the interesting one: it looks dead by publisher/subscriber
count, and it is not — a late subscriber gets the value it never re-sent.
"""

import sys
import time

import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

CHATTER_HZ = 10.0
ORPHAN_HZ = 1.0


class Rig(Node):
    def __init__(self) -> None:
        super().__init__("demo_rig")

        latched = QoSProfile(
            depth=1,
            history=HistoryPolicy.KEEP_LAST,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        plain = QoSProfile(
            depth=10,
            history=HistoryPolicy.KEEP_LAST,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )

        self.status_pub = self.create_publisher(String, "/demo/status", latched)
        self.chatter_pub = self.create_publisher(String, "/demo/chatter", plain)
        self.orphan_pub = self.create_publisher(String, "/demo/orphan_out", plain)

        # One subscriber on /demo/chatter so the topic has both ends, and one on a
        # topic nobody publishes to, so `dead_input` exists to be seen.
        self.chatter_sub = self.create_subscription(
            String, "/demo/chatter", lambda m: None, plain
        )
        self.orphan_in = self.create_subscription(
            String, "/demo/orphan_in", lambda m: None, plain
        )

        msg = String()
        msg.data = "armed"
        self.status_pub.publish(msg)

        self.created = time.time()
        self._timers = [
            self.create_timer(1.0 / CHATTER_HZ, self._chatter),
            self.create_timer(1.0 / ORPHAN_HZ, self._orphan),
        ]
        self.get_logger().info(
            "demo rig up: /demo/status (latched, 1 message), /demo/chatter %.0f Hz, "
            "/demo/orphan_in (subscriber only), /demo/orphan_out %.0f Hz"
            % (CHATTER_HZ, ORPHAN_HZ)
        )

    def _chatter(self) -> None:
        msg = String()
        msg.data = "tick %d" % int((time.time() - self.created) * CHATTER_HZ)
        self.chatter_pub.publish(msg)

    def _orphan(self) -> None:
        msg = String()
        msg.data = "%.2f" % round(time.time() % 100, 2)
        self.orphan_pub.publish(msg)


def main() -> int:
    rclpy.init()
    node = Rig()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
