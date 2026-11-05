A topic that has subscribers and no publisher is `dead_input`, and those subscribers wait forever.

A topic that has publishers and no subscriber is `dead_output`, and those messages are discarded.

A node list stays green in both cases, because the processes are still running.

Publisher and subscriber counts are what show the break.

When those counts cannot be read, the topic is unmeasured, and it is not treated as disconnected.

`live` is a claim about traffic, so it needs a measurement: both endpoints connected **and** a rate
above 0 Hz. A topic with no publisher and no subscriber has nothing to measure, and a topic whose
rate sample did not come back (the daemon fallback never measures) is also `unmeasured`, with text
saying the rate was not measured. Neither is reported as `live`.

A reachable host is not a ROS 2 host. When `ros2` is not on `PATH` because the environment is not
sourced, or `ros2 node list` fails, `ros2_graph` returns `ok: false` with the reason in `error`
instead of an empty graph — an empty graph is a finding, an unanswered one is a failure.

`/rosout`, `/rosout_agg`, `/parameter_events`, and `/clock` are housekeeping. They stay in the topic list with the health their counts imply. The orphan list leaves them out unless the caller sets `include_system`. `counts.system_excluded` is how many were left out, and `counts.orphans` counts only what remains.
