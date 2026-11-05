A topic that has subscribers and no publisher is `dead_input`, and those subscribers wait forever.

A topic that has publishers and no subscriber is `dead_output`, and those messages are discarded.

A node list stays green in both cases, because the processes are still running.

Publisher and subscriber counts are what show the break.

When those counts cannot be read, the topic is unmeasured, and it is not treated as disconnected.

`/rosout`, `/rosout_agg`, `/parameter_events`, and `/clock` are housekeeping. They stay in the topic list with the health their counts imply. The orphan list leaves them out unless the caller sets `include_system`. `counts.system_excluded` is how many were left out, and `counts.orphans` counts only what remains.
