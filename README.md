# ROS 2 Console

This plugin lets Hermes read a live ROS 2 system. It lists nodes and topics, measures publish rates, samples one message, and saves a compressed camera frame. Every command runs on the robot over SSH, through the robot's own `ros2` CLI. Nothing is installed on the robot, and the plugin holds no credentials.

## Install

```bash
hermes plugins install OWNER/ros2-console
```

`OWNER` stands for the GitHub account that will publish this repository. That account is not chosen yet.

## Tools

- `ros2_host_status` checks that the target answers, and reports the distro, middleware, hostname, uptime, and node and topic counts.
- `ros2_graph` lists nodes and topics with publisher and subscriber counts, and flags `dead_input` and `dead_output`.
- `ros2_topic_rate` measures a topic's rate in Hz over a short window. A topic with no messages returns 0 Hz and `silent` true.
- `ros2_topic_sample` reads one message as short YAML, with arrays omitted and long strings cut.
- `ros2_camera_snapshot` saves one `sensor_msgs/CompressedImage` frame as a JPEG on the machine running Hermes.

## Settings

Defaults come from `plugin.yaml`.

| Key | Default | Meaning |
| --- | --- | --- |
| `host` | empty | SSH target. Empty runs `ros2` on this machine. |
| `distro` | `jazzy` | Distro sourced from `/opt/ros/<distro>/setup.bash`. |
| `setup` | empty | Extra setup file to source after the distro, for custom message types. |
| `timeout` | `15` | Per-command timeout in seconds. Values are clamped to 5 through 120. |

## Pane

Hermes Desktop opens a pane titled **ROS 2** on the right, 360 pixels wide. The header shows the target, the distro, and the time of the last refresh. **Refresh** loads the graph once. **Poll** repeats that load every 5 seconds, and it stays off until you turn it on. A banner names up to three topics whose health is `dead_input` or `dead_output`. The status bar shows the orphan count. **Grab frame** loads the camera topic. That topic defaults to `/camera/image_raw/compressed`, and the pane remembers a change.

## Safety

Allowed commands are node list, topic list, topic info, topic hz, topic echo, and one subscription for a compressed image. The plugin does not publish to a topic, does not call `ros2 param set`, and does not kill nodes.

SSH runs with `BatchMode=yes`. Authentication is your SSH config and agent. The plugin stores no password and no key. One command keeps at most 200,000 characters of output. The plugin sends no telemetry.

## Reach the robot

Put the robot in your SSH config before you set `host` to that name. The demo in this repo uses the host `ros2demo`.

```
Host ros2demo
  HostName 127.0.0.1
  Port 2222
  User root
```

`ssh -o BatchMode=yes ros2demo` has to succeed with no prompt. If a password would have been required, the command fails in about six seconds.

## Limits

`ros2_camera_snapshot` refuses a raw `sensor_msgs/Image` topic. Publish a compressed image on a topic such as `/camera/image_raw/compressed`.
