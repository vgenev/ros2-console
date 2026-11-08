# ROS 2 Console

![A draughtsman's plate: a winged messenger holds the threads that suspend a small boxy robot, over faint construction circles.](docs/assets/hermes-plate.jpg)

This plugin lets Hermes read a live ROS 2 system. It lists nodes and topics, measures publish rates, samples one message, and saves a compressed camera frame. Every command runs on the robot over SSH, through the robot's own `ros2` CLI. Nothing is installed on the robot, and the plugin holds no credentials.

## Install

```bash
hermes plugins install OWNER/ros2-console
```

`OWNER` stands for the GitHub account that will publish this repository. That account is not chosen yet.

## Tools

- `ros2_host_status` checks that the target answers, and reports the distro, middleware, hostname, uptime, and node and topic counts.
- `ros2_graph` returns nodes, topics, publisher/subscriber counts, and the node-to-topic **edges** behind them. Each topic gets a health of `live`, `silent` (connected but nothing arrived in the sample window), `state` (latched: a `TRANSIENT_LOCAL` publisher that replays its last value to a late subscriber), `dead_input`, `dead_output`, or `unmeasured`, plus a **measured** `rate_hz` and the evidence behind it: every rate carries `measured` = `{method, window_s, msgs, at}` — how many messages were counted, over what window, and when. A rate whose evidence is absent (an older capture) is reported with `msgs: null` rather than filled in with a plausible guess. `live` requires a measured rate above 0 Hz: missing rate data or 0 publishers and 0 subscribers is `unmeasured`, never `live`. `/rosout`, `/rosout_agg`, `/parameter_events`, and `/clock` stay in that list with their real health. They are left out of the orphan count unless you pass `include_system`. `counts.system_excluded` is how many were left out. `ok` is `false` when ROS 2 never answered (wrong distro, environment not sourced); the reason is in `error` and `error_kind` is `ros_not_answered` — an empty graph is a finding, an unanswered one is a failure.
- `ros2_topic_rate` measures a topic's rate in Hz over a short window. A topic with no messages returns 0 Hz and `silent` true.
- `ros2_topic_sample` reads one message. It subscribes with the publisher's own QoS, so `BEST_EFFORT` camera topics work; `String` payloads come back whole (JSON is not wrapped in a Python `repr`), and image topics are summarised by size and encoding instead of dumping a pixel array.
- `ros2_camera_snapshot` saves one frame as a JPEG on the machine running Hermes. `sensor_msgs/CompressedImage` passes through; a raw `sensor_msgs/Image` is decoded on the robot when numpy and PIL are present.
- `ros2_docs` answers architecture questions from a local documentation corpus you point it at (see **Knowledge layer**).
- `ros2_publish` writes one message and proves delivery, and is **disabled until you allow a topic** (see **Writing to topics**).

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
put it, and an answer is quoted with its provenance **when the corpus provides it**: `source_repo`,
`source_path` and `source_commit` come from a file's frontmatter and are empty strings when it has
none, so a hit without them is a plain excerpt, not a citation.

```bash
hermes config set plugins.entries.ros2-console.settings.docs_path /path/to/ros2-study
```

That path should contain markdown; if it also has `graphify-out/graph.json` the tool queries the
knowledge graph (about 0.3–0.5 s for a 3,718-node graph), otherwise it falls back to `ripgrep` over
the markdown. Each result carries `source_repo`, `source_path`, `source_commit` — empty strings when
the file has no frontmatter — and a bounded prose excerpt, never a table row or a code fence, which
match every query term and read badly.

Measured on a 412-document corpus: `why transient local durability` → 0.41 s, prose excerpt from
`ros2/design@93a415b`. This is the tool for architecture questions; for "why is this topic dead" the
graph view is the faster answer.

## Bundled skills

The plugin ships three advisory skills — documents the plugin never executes — that carry the
triage discipline its tools assume. They load on demand as `ros2-console:<name>`:

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

**Topology** draws three lanes — publishers on the left, topics in the middle, subscribers on the
right — so direction is readable at a glance. A node that both publishes and subscribes appears in
both columns. Edges are solid where a publisher feeds a topic and dashed where a topic feeds a
subscriber, and each topic carries a dot on both sides: the port publishers arrive at and the port
subscribers leave from. A line carries moving dashes **only when that topic is actually publishing**,
and the dash speed follows the measured `msg/s` — motion is evidence, not decoration. **Table** shows
the same data as rows. A legend spells out all six states and the line grammar.

**Poll** repeats the read every 60 s (a read costs 11–18 s on real hardware, so faster polling only
queues work), and it is **on while the page is open**: opening the page starts it and leaving stops
it, so nothing reads a robot in the background. The Poll button turns it off and on. While polling,
the camera re-grabs every 8 s.

Hovering a topic peeks at its last message; clicking pins the card so you can move into it and copy
the payload, pretty-printed when it is JSON. Frames decode offscreen before swapping in, so a refresh
never blanks the picture.

Below the camera, a **Publish (write path)** row edits `publish_allow` and, once the list is non-empty,
sends a topic and payload and prints the delivery proof — `received · 1 sent · type · echo …` — or the
refusal. It calls the same guard function as the agent, so the pane is not a privileged path.

## Writing to topics (opt-in)

Six tools read; one can write, and only if you tell it to. `ros2_publish` is **disabled by default** —
`publish_allow` is an empty list, so every publish refuses until a human adds a topic glob:

```yaml
plugins:
  entries:
    ros2-console:
      settings:
        publish_allow: ["/demo/*"]
```

Six guards stand between a call and the robot:

1. **Allowlist.** The topic must match a glob in `publish_allow`. Empty list = nothing is writable, and the refusal names the setting.
2. **Actuation is refused regardless.** `cmd_vel`, `joint_*`, `*_cmd`, `servo`, `gripper`, `motor`, `throttle`, steering, brakes and similar names are refused **even if a glob would allow them** — an allowlist chooses which topics are writable, never whether a servo is.
3. **Type from the graph, never from the caller.** The message type is read from the live graph. A type you pass must match it, or the call is refused.
4. **Bounds.** 1–10 messages, 0.5–5 Hz, payload ≤ 8 KB, simple `data`-field message types only.
5. **QoS copied from the topic's existing publisher.** A RELIABLE publisher against a BEST_EFFORT subscriber delivers nothing silently, so the QoS is inherited rather than assumed, and the choice is reported.
6. **Delivery is proven.** A listener on the same QoS must receive the message within the window. `sent but nobody received it` reports `ok: false` — a clean send is not a success.

The tool publishes and reports; it does not stream. Every call is bounded.

## Safety

The plugin runs: `ros2 node list`, `ros2 topic list`, `ros2 topic info`, `ros2 topic hz`, one `rclpy`
process that introspects the graph and subscribes briefly (2 s, `depth 10`, the publisher's own QoS)
to each non-system topic to count messages, one short subscription to the camera topic to save a
frame, and `graphify`/`ripgrep` against the local `docs_path`. Every **tool** is read-only: the
plugin does not publish to a topic, does not call `ros2 param set`, and does not start, stop, kill
or restart a node.

That is a limit of the tools, not of the documentation. The bundled `ros2-edge-node-ops` skill
describes running, restarting and supervising nodes over SSH — `tmux`, `pgrep`, redirects, `systemd`.
Those are commands **you** run in a terminal: the skill is text, and the plugin executes none of it.
Read that skill as a runbook for the human, and this list as what the agent itself does.

SSH runs with `BatchMode=yes`. Authentication is your SSH config and agent. The plugin stores no password and no key. One command keeps at most 200,000 characters of output; a camera frame larger than that fails with a size error rather than saving a truncated image. The plugin sends no telemetry.

## Reach the robot

Put the robot in your SSH config before you set `host` to that name — an alias keeps the address
and the key out of the plugin's settings.

```
Host robot
  HostName robot.local
  User your-user
```

`ssh -o BatchMode=yes robot` has to succeed with no prompt. If a password would have been required, the command fails in about six seconds.

`host` is checked against an allow-list before ssh runs: a hostname, IPv4 address, ssh_config alias or `user@host`. Anything else — including a value that starts with `-`, which ssh would read as an option — is refused, so a hostile `host` from a tool call, a settings POST or `/graph?host=` never becomes an argv.

## Limits

A graph read on a small ARM board measured 11–12 s: a single `rclpy` introspection pass, because the
ROS daemon serialises `ros2 topic info` at about 1.9 s per topic. Discovery speed, not topic count,
sets the floor.

Rates are counted over a 2 s window, so an event-driven topic can report `silent` between events —
that is a true statement about the window, not a fault. A latched publisher's single replayed sample
is reported as 0 msg/s state delivery rather than an invented rate.

The camera decoder needs numpy and PIL on the robot for raw `sensor_msgs/Image`. Without them, publish
a `CompressedImage` topic instead.
