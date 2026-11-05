"""A small, self-contained ROS 2 cell for testing and demos.

It deliberately includes two *structurally disconnected* topics, because that is
the failure this plugin exists to catch:

  /depth/points/compressed   a consumer with no producer  → dead_input
  /board/temperature         a producer with no consumer  → dead_output

Everything else is a healthy pipeline: a camera, a talker/listener pair, and an
inspection node that consumes camera frames and publishes verdicts.

    python3 demo/nodes.py all        # everything, one process
    python3 demo/nodes.py camera     # just the camera (+ verdict consumer)

Requires a sourced ROS 2 environment and the JPEG next to this file.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Float32, String

JPEG = (Path(__file__).with_name("testcard.jpg")).read_bytes()


class Camera(Node):
    """Publishes compressed frames at 2 Hz, like a real inspection camera."""

    def __init__(self):
        super().__init__("inspection_camera")
        self.pub = self.create_publisher(CompressedImage, "/camera/image_raw/compressed", 4)
        # 2 Hz keeps the single-process demo cell from starving the 10 Hz talker;
        # a starved talker made rate measurements bimodal (6.3 vs 10.0 Hz).
        self.create_timer(0.5, self.tick)

    def tick(self):
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "camera_optical_frame"
        msg.format = "jpeg"
        msg.data = JPEG
        self.pub.publish(msg)


class Talker(Node):
    def __init__(self):
        super().__init__("talker")
        self.pub = self.create_publisher(String, "/chatter", 10)
        self.count = 0
        self.create_timer(0.1, self.tick)

    def tick(self):
        msg = String()
        msg.data = f"hello world {self.count}"
        self.count += 1
        self.pub.publish(msg)


class Listener(Node):
    def __init__(self):
        super().__init__("listener")
        self.create_subscription(String, "/chatter", lambda m: None, 10)


class Inspector(Node):
    """Consumes camera frames and publishes a verdict — the live output path."""

    def __init__(self):
        super().__init__("inspection_verdict")
        self.pub = self.create_publisher(String, "/inspection/verdict", 10)
        self.create_subscription(CompressedImage, "/camera/image_raw/compressed", self.on_frame, 4)

    def on_frame(self, msg):
        verdict = String()
        verdict.data = f"ok seq={msg.header.stamp.sec} bytes={len(msg.data)}"
        self.pub.publish(verdict)


class ZombieConsumer(Node):
    """Subscribes to a depth topic nobody publishes: waits forever, looks alive."""

    def __init__(self):
        super().__init__("depth_filter")
        self.create_subscription(CompressedImage, "/depth/points/compressed", lambda m: None, 4)


class MutedProducer(Node):
    """Publishes board temperature with no subscriber: every message is discarded."""

    def __init__(self):
        super().__init__("board_telemetry")
        self.pub = self.create_publisher(Float32, "/board/temperature", 10)
        self.t = 0.0
        self.create_timer(1.0, self.tick)

    def tick(self):
        msg = Float32()
        msg.data = 41.0 + 3.0 * math.sin(self.t)
        self.t += 0.4
        self.pub.publish(msg)


class LatchedStatus(Node):
    """Publishes one status message with TRANSIENT_LOCAL durability and no subscriber.

    This is the case every dashboard gets wrong: 1 publisher, 0 subscribers, no
    traffic, and yet nothing is lost — the sample is retained and replayed to
    whoever subscribes later. It reports as `state`, not as a dead output.
    """

    def __init__(self):
        super().__init__("session_status")
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        qos.reliability = ReliabilityPolicy.RELIABLE
        self.pub = self.create_publisher(String, "/session/status", qos)
        self.create_timer(2.0, self.announce)
        self.said = False

    def announce(self):
        if self.said:
            return
        msg = String()
        msg.data = '{"session": "demo", "state": "armed", "cycle": 0}'
        self.pub.publish(msg)
        self.said = True


BUILDERS = {
    "camera": [Camera, Inspector],
    "talker": [Talker, Listener],
    "zombie": [ZombieConsumer],
    "telemetry": [MutedProducer],
    "stateful": [LatchedStatus],
    "all": [
        Camera,
        Inspector,
        Talker,
        Listener,
        ZombieConsumer,
        MutedProducer,
        LatchedStatus,
    ],
}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode not in BUILDERS:
        raise SystemExit(f"unknown mode {mode!r}; choose from {', '.join(BUILDERS)}")
    rclpy.init()
    executor = MultiThreadedExecutor()
    for builder in BUILDERS[mode]:
        executor.add_node(builder())
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
