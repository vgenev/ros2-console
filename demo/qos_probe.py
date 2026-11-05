"""Prove the QoS mismatch claim on Jazzy: BEST_EFFORT publisher, RELIABLE subscriber.

Run inside a ROS 2 container with the distro sourced:
    python3 qos_probe.py

It publishes 20 messages on /qos_probe as BEST_EFFORT and creates three
subscribers with different reliability settings, then reports who received what.
"""

import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

TOPIC = "/qos_probe"


def profile(reliability):
    return QoSProfile(
        reliability=reliability,
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        durability=DurabilityPolicy.VOLATILE,
    )


def main():
    rclpy.init()
    node = Node("qos_probe")
    pub = node.create_publisher(
        String, TOPIC, profile(ReliabilityPolicy.BEST_EFFORT)
    )
    counts = {"reliable": 0, "best_effort": 0}

    node.create_subscription(
        String, TOPIC,
        lambda msg: counts.__setitem__("reliable", counts["reliable"] + 1),
        profile(ReliabilityPolicy.RELIABLE),
    )
    node.create_subscription(
        String, TOPIC,
        lambda msg: counts.__setitem__("best_effort", counts["best_effort"] + 1),
        profile(ReliabilityPolicy.BEST_EFFORT),
    )

    # Let discovery settle before publishing, or the result is a discovery race.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)

    for i in range(20):
        pub.publish(String(data=f"probe {i}"))
        rclpy.spin_once(node, timeout_sec=0.05)
        time.sleep(0.05)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)

    print(f"published: 20 (BEST_EFFORT publisher)")
    print(f"reliable subscriber received:    {counts['reliable']}")
    print(f"best_effort subscriber received: {counts['best_effort']}")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
