#!/usr/bin/env python3
"""Print one JSON object with host status and the parsed ROS 2 graph.

Target resolution is the --target flag, then the saved plugin setting, then
local mode (empty host). The Hermes gateway does not need to be running.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ros2_console.graph import graph, host_status
from ros2_console.settings import resolve
from ros2_console.transport import Target


def build_target(flag: str | None) -> Target:
    overrides = {"host": flag} if flag is not None else None
    settings = resolve(overrides)
    return Target(
        host=settings["host"],
        distro=settings["distro"],
        setup=settings["setup"],
        timeout=settings["timeout"],
    )


def probe(flag: str | None) -> dict:
    target = build_target(flag)
    host, _host_result = host_status(target)
    data, _graph_result = graph(target)
    return {"host": host, "graph": data}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe a ROS 2 target.")
    parser.add_argument(
        "--target",
        default=None,
        help="SSH host. Overrides the saved setting. Omit to use the saved host, or local mode.",
    )
    args = parser.parse_args(argv)
    payload = probe(args.target)
    json.dump(payload, sys.stdout, default=str)
    sys.stdout.write("\n")
    return 0 if payload["graph"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
