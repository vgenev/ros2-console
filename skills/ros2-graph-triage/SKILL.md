---
name: ros2-graph-triage
description: Use when a ROS 2 topic is silent, a node looks alive but does nothing, or a rate reads wrong. Triage order that separates a dead publisher from a QoS mismatch from a discovery problem.
---

# ROS 2 graph triage — why is nothing coming out?

A ROS 2 graph fails in ways that all look identical from the outside: no data. This is the
order that separates them, cheapest test first. Every command here runs on the robot, over
SSH, with the distro sourced — that is what `ros2_host_status`, `ros2_graph`,
`ros2_topic_rate` and `ros2_topic_sample` do for you.

## Health is six states, not two

`ros2_graph` returns one of: `live`, `silent`, `state`, `dead_input`, `dead_output`, `unmeasured`.
Reading them as "up or down" produces wrong conclusions:

- `state` — the publisher uses `TRANSIENT_LOCAL` durability and publishes on change. Zero subscribers
  is correct here: a late consumer still receives the current value. Do not report it as an orphan.
- `silent` — publisher and subscriber are connected but no message arrived inside the 2 s sample
  window. For an event-driven topic that is normal; confirm with `ros2_topic_rate` over a longer window
  before calling it broken.
- `dead_output` with `VOLATILE` durability — nobody is listening and nothing is retained. This is the
  one that means data is being discarded.

For architecture questions ("how does the executor model work", "why does durability matter"), use
`ros2_docs` against a documentation corpus rather than guessing from a live graph; the graph shows
wiring, not intent.

## 1. Is the graph even populated?

`ros2_graph` first. It returns nodes, topics with type and publisher/subscriber counts, and a
health flag per topic.

- `dead_input` — subscribers, no publisher. The consumer is waiting forever and **still looks
  healthy in `ros2 node list`**. This is the most expensive failure to notice by eye because
  every process is up.
- `dead_output` — publishers, no subscriber. Messages are produced and discarded.
- `unmeasured` — counts unavailable (permissions, a very slow host). Not evidence of health.

If the whole graph is empty, stop triaging topics: you are either on the wrong host, in the
wrong `ROS_DOMAIN_ID`, or discovery is blocked. Check `ros2_host_status` for a reachable host
with zero nodes before you touch anything else.

## 2. Does it publish at all, and at what rate?

`ros2_topic_rate` measures the receive rate. Two traps:

- **0 Hz with publishers > 0** is usually QoS, not a stopped node. Go to step 3.
- **A rate that reads low** is usually the measurement window, not the robot. `ros2 topic hz`
  averages over a *message count*, so a window of 50 on a 10 Hz topic needs 5 seconds to fill;
  measured on Jazzy, sampling that window for only 5 s reported 6.3–6.9 Hz on a publisher that
  was genuinely doing 10.0 Hz. Use a window the measurement time can fill (30 messages at
  10 Hz converges inside ~3 s) and report min/max alongside the average — a stalling
  publisher hides inside an average.

## 3. QoS mismatch — the silent one

Matching topic names and types are **not** enough. Reliability is a compatibility contract:
a `RELIABLE` subscriber cannot receive from a `BEST_EFFORT` publisher, and no error is raised
to either side.

Measured on Jazzy (see `demo/qos_probe.py`): one BEST_EFFORT publisher, 20 messages, two
subscribers — the BEST_EFFORT subscriber received **20/20**, the RELIABLE subscriber received
**0/20**, and the daemon logged:

```
New subscription discovered on topic '/qos_probe', requesting incompatible QoS.
No messages will be sent to it. Last incompatible policy: RELIABILITY
```

So: sensor drivers default to `BEST_EFFORT`; `ros2 topic echo` and naive subscribers default to
`RELIABLE`. That is the classic "the camera publishes fine but my tool sees nothing". When a
rate reads 0 Hz with publishers > 0, re-measure with matched QoS before blaming the driver.

Check what is being offered:

```
ros2 topic info /camera/image_raw/compressed --verbose
```

and compare each endpoint's `Reliability:` line. A RELIABLE subscriber must be changed to
BEST_EFFORT (or the publisher raised to RELIABLE — usually the wrong direction for a wireless
sensor).

## 4. Domain and discovery

Two machines only see each other with the same `ROS_DOMAIN_ID` (default 0). `ROS_LOCALHOST_ONLY=1`
confines a process to loopback, so a node can be running on the robot and invisible to a
colleague's shell. On multi-homed hosts, discovery can also pick the wrong interface; if the
graph is empty between two machines that are otherwise reachable, check the domain id and the
interface before suspecting the node.

## 5. Read one message

`ros2_topic_sample` returns a single bounded message. Read the header: `frame_id`, `stamp`
age, and the data's shape tell you whether the pipeline is fresh (stamp within a second) or
replaying stale data (stamp minutes old while the rate looks healthy).

## Order to keep

1. graph populated? (host, domain, interface)
2. rate: 0, low, or right?
3. QoS endpoints compatible?
4. one message: fresh or stale?
5. only then read the node's logs.
