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
from ros2_console.tools import _camera_snapshot, _topic_rate, _topic_sample
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


def _tool_args(topic: str, target: Target, seconds: int) -> dict:
    args = {"topic": topic, "seconds": seconds}
    if target.host:
        args["host"] = target.host
    if target.distro:
        args["distro"] = target.distro
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe a ROS 2 target.")
    parser.add_argument(
        "--target",
        default=None,
        help="SSH host. Overrides the saved setting. Omit to use the saved host, or local mode.",
    )
    parser.add_argument("--rate", action="append", default=[], help="Topic to measure with ros2_topic_rate.")
    parser.add_argument("--sample", action="append", default=[], help="Topic to read with ros2_topic_sample.")
    parser.add_argument("--snapshot", action="append", default=[], help="Compressed image topic to save.")
    parser.add_argument("--seconds", type=int, default=5, help="Window for --rate, --sample, and --snapshot.")
    args = parser.parse_args(argv)
    try:
        payload = probe(args.target)
    except ValueError as exc:
        # A refused ssh destination or distro is user input, not a crash.
        parser.error(str(exc))
    if args.rate or args.sample:
        target = build_target(args.target)
        payload["rates"] = [
            json.loads(_topic_rate(_tool_args(topic, target, args.seconds))) for topic in args.rate
        ]
        payload["samples"] = [
            json.loads(_topic_sample(_tool_args(topic, target, args.seconds))) for topic in args.sample
        ]
        payload["snapshots"] = [
            json.loads(_camera_snapshot(_tool_args(topic, target, args.seconds)))
            for topic in args.snapshot
        ]
    json.dump(payload, sys.stdout, default=str)
    sys.stdout.write("\n")
    return 0 if payload["graph"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
