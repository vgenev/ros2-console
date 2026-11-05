A topic that has subscribers and no publisher is `dead_input`, and those subscribers wait forever.

A topic that has publishers and no subscriber is `dead_output`, and those messages are discarded.

A node list stays green in both cases, because the processes are still running.

Publisher and subscriber counts are what show the break.

When those counts cannot be read, the topic is unmeasured, and it is not treated as disconnected.
