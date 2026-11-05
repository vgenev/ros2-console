# ROS 2 Console

This plugin lets Hermes read a live ROS 2 system. It lists nodes and topics, measures publish rates, samples one message, and saves a compressed camera frame. Every command runs on the robot over SSH, through the robot's own `ros2` CLI. Nothing is installed on the robot, and the plugin holds no credentials.

## Install

```bash
hermes plugins install OWNER/ros2-console
```

`OWNER` stands for the GitHub account that will publish this repository. That account is not chosen yet.

## Tools

- `ros2_host_status` checks that the target answers, and reports the distro, middleware, hostname, uptime, and node and topic counts.
- `ros2_graph` returns nodes, topics, publisher/subscriber counts, and the node-to-topic **edges** behind them. Each topic gets a health of `live`, `silent` (connected but nothing arrived in the sample window), `state` (latched: a `TRANSIENT_LOCAL` publisher that replays its last value to a late subscriber), `dead_input`, `dead_output`, or `unmeasured`, plus a **measured** `rate_hz`. `live` requires a measured rate above 0 Hz: missing rate data or 0 publishers and 0 subscribers is `unmeasured`, never `live`. `/rosout`, `/rosout_agg`, `/parameter_events`, and `/clock` stay in that list with their real health. They are left out of the orphan count unless you pass `include_system`. `counts.system_excluded` is how many were left out. `ok` is `false` when ROS 2 never answered (wrong distro, environment not sourced); the reason is in `error` and `error_kind` is `ros_not_answered` — an empty graph is a finding, an unanswered one is a failure.
- `ros2_topic_rate` measures a topic's rate in Hz over a short window. A topic with no messages returns 0 Hz and `silent` true.
- `ros2_topic_sample` reads one message. It subscribes with the publisher's own QoS, so `BEST_EFFORT` camera topics work; `String` payloads come back whole (JSON is not wrapped in a Python `repr`), and image topics are summarised by size and encoding instead of dumping a pixel array.
- `ros2_camera_snapshot` saves one frame as a JPEG on the machine running Hermes. `sensor_msgs/CompressedImage` passes through; a raw `sensor_msgs/Image` is decoded on the robot when numpy and PIL are present.
- `ros2_docs` answers architecture questions from a local documentation corpus you point it at (see **Knowledge layer**).

## Settings

Defaults come from `plugin.yaml`.

| Key | Default | Meaning |
| --- | --- | --- |
| `host` | empty | SSH target. Empty runs `ros2` on this machine. |
| `distro` | `jazzy` | Distro sourced from `/opt/ros/<distro>/setup.bash`. |
| `setup` | empty | Extra setup file to source after the distro, for custom message types. |
| `timeout` | `15` | Per-command timeout in seconds. Values are clamped to 5 through 120. |
| `docs_path` | empty | Local ROS 2 documentation corpus for `ros2_docs`. Empty disables the tool with an explanatory message. |

## Knowledge layer

`ros2_docs` answers "how does X work" questions from a documentation corpus on the machine running
Hermes. Nothing is bundled with the plugin, and that is deliberate: the ROS 2 documentation, nav2,
moveit2 and `ros2_control` docs are third-party work under Apache-2.0, BSD-3-Clause and CC-BY-4.0,
and normalising 412 documents into markdown makes a derivative. So the corpus stays where the user
put it and every answer is quoted with its provenance.

```bash
hermes config set plugins.entries.ros2-console.settings.docs_path /path/to/ros2-study
```

That path should contain markdown; if it also has `graphify-out/graph.json` the tool queries the
knowledge graph (about 0.3–0.5 s for a 3,718-node graph), otherwise it falls back to `ripgrep` over
the markdown. Each result carries `source_repo`, `source_path`, `source_commit` and a bounded prose
excerpt — never a table row or a code fence, which match every query term and read badly.

Measured on a 412-document corpus: `why transient local durability` → 0.41 s, prose excerpt from
`ros2/design@93a415b`. This is the tool for architecture questions; for "why is this topic dead" the
graph view is the faster answer.

## Bundled skills

The plugin ships three read-only skills that carry the triage discipline its tools assume. They load
on demand as `ros2-console:<name>`:

| Skill | Use it when |
|---|---|
| `ros2-console:ros2-graph-triage` | a topic is silent, a rate reads wrong, or a node looks alive but does nothing |
| `ros2-console:ros2-camera-and-images` | a camera topic is empty, a snapshot fails, or image bandwidth matters |
| `ros2-console:ros2-edge-node-ops` | running or restarting nodes on an edge device over SSH |

Every claim in them that could be measured was: the QoS incompatibility that silently starves a
`RELIABLE` subscriber, the measurement window that makes a 10 Hz topic read 6.3 Hz, and the bandwidth
table are all reproduced in `demo/`. If your Hermes build does not surface plugin skills, they are
plain directories — copy any of `skills/<name>/` into `~/.hermes/skills/` and it behaves as an
ordinary skill.

## Page

Hermes Desktop adds a **ROS 2** row to the sidebar that opens a full page, so it does not occupy chat
space or follow you into other chats. The header shows the target, the distro, a live dot while
polling, and the time of the last read. The target field saves the plugin's `host` setting, so a
fresh install needs no config editing.

**Topology** draws nodes on the left and topics on the right, with solid lines for publishers and
dashed for subscribers. A line carries moving dashes **only when that topic is actually publishing**,
and the dash speed follows the measured `msg/s` — motion is evidence, not decoration. **Table** shows
the same data as rows. A legend spells out all six states and the line grammar.

**Poll** repeats the read every 15 s (a read costs 11–12 s on real hardware, so faster polling only
queues work), and it stays off until you turn it on. While polling, the camera re-grabs every 8 s.

Hovering a topic peeks at its last message; clicking pins the card so you can move into it and copy
the payload, pretty-printed when it is JSON. Frames decode offscreen before swapping in, so a refresh
never blanks the picture.

## Safety

The plugin runs: `ros2 node list`, `ros2 topic list`, `ros2 topic info`, `ros2 topic hz`, one `rclpy`
process that introspects the graph and subscribes briefly (2 s, `depth 10`, the publisher's own QoS)
to each non-system topic to count messages, one short subscription to the camera topic to save a
frame, and `graphify`/`ripgrep` against the local `docs_path`. The plugin does not publish to a topic,
does not call `ros2 param set`, and does not kill or restart nodes.

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

A graph read on a small ARM board measured 11–12 s: a single `rclpy` introspection pass, because the
ROS daemon serialises `ros2 topic info` at about 1.9 s per topic. Discovery speed, not topic count,
sets the floor.

Rates are counted over a 2 s window, so an event-driven topic can report `silent` between events —
that is a true statement about the window, not a fault. A latched publisher's single replayed sample
is reported as 0 msg/s state delivery rather than an invented rate.

The camera decoder needs numpy and PIL on the robot for raw `sensor_msgs/Image`. Without them, publish
a `CompressedImage` topic instead.
