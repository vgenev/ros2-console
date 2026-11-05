"""ros2-console — operate a live ROS 2 robot from Hermes.

Two surfaces, one package:
  * agent tools (`ctx.register_tool`) for graph, rates, samples, snapshots;
  * a Desktop pane (`desktop/plugin.js`) that reads the same data over the
    plugin's own backend namespace (`dashboard/plugin_api.py`).

Every command runs on the robot over SSH through its own `ros2` CLI, so the
plugin needs nothing installed on the target and never holds a credential.
"""

from __future__ import annotations

TOOLSET = "ros2"


def register(ctx):
    from .ros2_console import tools

    for tool in tools.TOOLS:
        ctx.register_tool(
            name=tool.name,
            toolset=TOOLSET,
            schema=tool.schema,
            handler=tool.handler,
            description=tool.schema["description"],
            emoji=tool.emoji,
        )
