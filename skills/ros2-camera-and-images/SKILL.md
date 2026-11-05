---
name: ros2-camera-and-images
description: Use when a ROS 2 camera topic is empty, a snapshot fails, or image bandwidth saturates an edge link. Covers compressed vs raw images, QoS, and republishing.
---

# ROS 2 cameras and images — get a frame, know its cost

## Raw vs compressed is a bandwidth decision

`sensor_msgs/Image` carries uncompressed pixels; `sensor_msgs/CompressedImage` carries a JPEG
or PNG payload. The difference is not cosmetic:

| Shape | One frame | At 5 Hz |
|---|---|---|
| `Image`, 400×300, `rgb8` | 400 × 300 × 3 = **360,000 B** | ~1.8 MB/s (14.4 Mbit/s) |
| `CompressedImage`, same view, JPEG q85 | **16,374 B** (measured on this repo's test card) | ~82 kB/s (0.65 Mbit/s) |

Same camera, ~22× the wire cost. On a wireless or metered edge link that is the difference
between a working cell and a starved one — and a starved bus does not fail loudly, it makes
every other topic's rate sag.

## Reading a compressed topic

`ros2_camera_snapshot` subscribes once to a `CompressedImage` topic and writes the payload to a
JPEG on the machine running Hermes. It refuses `sensor_msgs/Image` on purpose: decoding raw
pixels correctly needs the encoding, the step (row stride) and colour order, and a wrong guess
produces a plausible-looking, wrong image — worse than an honest error.

If the only topic is raw, republish it compressed on the robot, then snapshot the new topic:

```
ros2 run image_transport republish raw in:=/camera/image_raw \
    compressed out:=/camera/image_raw/compressed
```

Topic naming that follows the `image_transport` convention (`/camera/image_raw/compressed`)
means most ROS tooling finds the compressed form without configuration.

## Empty camera topic: check in this order

1. **Publishers exist?** `ros2_graph` — a camera node that started but never opened the device
   shows 0 publishers on the image topic while the node itself is alive.
2. **QoS.** Sensor drivers are usually `BEST_EFFORT`; a default `RELIABLE` subscriber receives
   nothing at all. See `ros2-graph-triage` for the measured proof. This is the most common
   "the camera works in rviz but not in my script".
3. **Is the topic the one you think?** Compressed topics are separate topics, often under a
   namespace (`/left/camera/image_raw/compressed`). List, do not assume.
4. **Bandwidth.** A raw stream pinning the link shows up as *other* topics dropping rate —
   look at the whole graph, not the camera.

## Freshness beats presence

A frame arriving at a healthy rate can still be stale: check the `header.stamp` against the
robot's clock. A camera re-publishing its last good buffer looks perfect in every rate check
and useless in practice. The snapshot tool returns `stamp` for exactly this reason — compare it
to "now", and if the robot's clock is unsynchronised, compare with a second frame a second
later instead.

## Cost discipline

Grabbing frames continuously through an agent is expensive in bytes and attention. Snapshot on
demand, keep the compressed topic at the lowest rate that serves the task (2 Hz is enough for
inspection dashboards), and never stream raw video through a chat channel.
