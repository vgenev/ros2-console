"""ros2-console — operate a live ROS 2 robot from Hermes.

Two surfaces, one package:
  * agent tools (`ctx.register_tool`) for graph, rates, samples, snapshots;
  * a Desktop pane (`desktop/plugin.js`) that reads the same data over the
    plugin's own backend namespace (`dashboard/plugin_api.py`).

Every command runs on the robot over SSH through its own `ros2` CLI, so the
plugin needs nothing installed on the target and never holds a credential.
"""

from __future__ import annotations

from pathlib import Path

TOOLSET = "ros2"


def register(ctx):
    from .ros2_console import runtime, tools

    # backend routes persist settings through ctx.set_config; keep it reachable
    runtime.set_ctx(ctx)

    for tool in tools.TOOLS:
        ctx.register_tool(
            name=tool.name,
            toolset=TOOLSET,
            schema=tool.schema,
            handler=tool.handler,
            description=tool.schema["description"],
            emoji=tool.emoji,
        )

    # Bundled skills: namespaced as `ros2-console:<name>`, read-only, loaded on
    # demand via skill_view. They carry the triage discipline the tools assume.
    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)
