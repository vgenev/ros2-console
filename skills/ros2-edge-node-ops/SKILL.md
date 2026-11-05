---
name: ros2-edge-node-ops
description: Use when running, restarting or debugging ROS 2 nodes on an edge device over SSH. Keeps a node alive across disconnects and proves it works instead of assuming it.
---

# ROS 2 node operations on an edge device over SSH

> **These are commands the human runs, not tool calls.** The plugin's tools are read-only: they
> read the graph, measure a rate, sample a message and grab a frame, and never start, stop, kill or
> restart anything. Everything below — tmux sessions, `pgrep`, redirects, `systemd` — is a manual
> operation you run over SSH while the agent observes and verifies the result.

## A process that is running is not a node that works

The cheapest false positive in robotics is `systemctl status` or `pgrep` saying the node is up.
A node can be alive and still be doing nothing useful: subscribed to a topic nobody publishes
(`dead_input`), publishing into a topic nobody subscribes to (`dead_output`), or holding a
device handle it never reads. Always finish with a graph check, not a process check:

```
ros2_graph            → is my node there, and are its topics connected?
ros2_topic_rate       → is it actually moving messages?
ros2_topic_sample     → is the content fresh (header stamp) and shaped right?
```

## Survive the SSH session

`ssh robot 'ros2 run pkg node'` dies with the connection. For anything longer than a quick
command, run it inside a terminal multiplexer on the device:

```
ssh robot 'tmux new -d -s cell "source /opt/ros/$ROS_DISTRO/setup.bash && ros2 run pkg node"'
ssh robot 'tmux ls'                    # is it still there?
ssh robot 'tmux attach -t cell'        # watch it (detach with ctrl-b d)
```

Keep the log somewhere you can read it after the fact — `tmux pipe-pane` or a redirect:

```
ssh robot 'tmux new -d -s cell "source /opt/ros/$ROS_DISTRO/setup.bash && \
  ros2 run pkg node > /tmp/cell.log 2>&1"'
ssh robot 'tail -50 /tmp/cell.log'
```

A redirect alone (`nohup ... &`) also survives, but then you have no attached console to debug
in — the multiplexer gives you both.

## Source the environment explicitly

Every remote command that touches `ros2` needs the distro sourced, and usually the workspace's
overlay too, which is where custom message types come from. Without the overlay, `ros2 topic
list` shows a bare graph and custom types appear as unknown — that is a sourcing problem, not a
broken build.

```
source /opt/ros/${ROS_DISTRO:-jazzy}/setup.bash
source ~/ws/install/setup.bash        # custom msg/srv types live here
```

## Restart without lying to yourself

1. Stop the old process and confirm it is gone (`pgrep -f`), because two instances of the same
   node name silently fight over topics and each looks half-alive.
2. Start the new one under the multiplexer.
3. Verify with the graph, rate and one message — the three checks above.
4. Only then leave. A restart "verified" by a PID is the same false positive as before.

## Do not trust rates on a saturated host

On a small board, all nodes competing for one CPU core will distort every measurement. Measured
example from this repo's own demo cell: six nodes in a single process with a 5 Hz JPEG camera
made a genuinely-10 Hz talker read **6.3 Hz**, and the same topic read **10.0 Hz** once the
camera dropped to 2 Hz. Nothing about the talker changed.

So when rates look wrong on an edge device, check the load before you debug the node:

```
uptime; nproc; top -bn1 | head -15
```

and prefer one process per role, or a lower sensor rate, over tuning the code.

## Resource and watchdog habits

- Keep an eye on disk: bags and JPEG bursts fill small eMMC quickly (`df -h`).
- A node that must survive reboots belongs in a systemd unit or the vendor's supervisor, not in
  a tmux session — the multiplexer is for interactive work, the unit is for production.
- Log rotation matters more than log verbosity on a 16 GB device.
