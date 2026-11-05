"""The five agent tools. Handlers return JSON strings — the Hermes tool contract."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import docs as knowledge
from . import graph as obs
from .settings import resolve
from .transport import Target


@dataclass
class Tool:
    name: str
    schema: dict
    handler: Callable
    emoji: str = "🤖"


def _target(args: dict, ctx: Any = None) -> Target:
    """Per-call host/distro overrides win over saved settings."""
    settings = resolve({
        "host": (args or {}).get("host"),
        "distro": (args or {}).get("distro"),
    })
    return Target(host=settings["host"], distro=settings["distro"],
                  setup=settings["setup"], timeout=settings["timeout"])


def _call(fn, args: dict, ctx=None) -> str:
    try:
        # A rejected host/distro raises here; a tool handler must answer with
        # JSON, never with an exception the caller sees as a crash.
        target = _target(args, ctx)
        payload, result = fn(target, args)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    payload.setdefault("ok", bool(result.ok) or bool(payload.get("received")))
    payload["target"] = target.label
    payload["mode"] = target.mode
    if not payload.get("ok") and "error" not in payload:
        payload["error"] = result.error()
    return json.dumps(payload, default=str)


# ------------------------------------------------------------------------- handlers

def _host_status(args, ctx=None):
    def work(target, a):
        return obs.host_status(target)
    return _call(lambda t, a: obs.host_status(t), args, ctx)


def _graph(args, ctx=None):
    include_system = bool((args or {}).get("include_system"))
    try:
        target = _target(args, ctx)
        data, result = obs.graph(target, include_system=include_system)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    if data is None:
        # The shell answered but ROS 2 did not: name that, instead of returning an
        # empty-looking graph an agent would read as a healthy idle robot.
        return json.dumps({"ok": False, "target": target.label, "mode": target.mode,
                           "error_kind": "ros_not_answered",
                           "error": result.meta.get("discovery_error") or result.error(),
                           "stdout_tail": (result.stdout or "")[-500:]})
    data.update({"target": target.label, "mode": target.mode,
                 "seconds": round(result.seconds, 2)})
    # `graph()` refuses to return data without the discovery marker; parse_graph
    # records the verdict, so keep the flag tied to that evidence instead of
    # setting it unconditionally.
    data["ok"] = data.get("ros_answered") is not False
    if not args.get("all_topics"):
        # The model needs the live map, not three hundred idle introspection topics.
        data["topics"] = [t for t in data["topics"] if t["health"] != "live" or t["publishers"]]
        data["topics"] = data["topics"][:80]
    return json.dumps(data, default=str)


def _topic_rate(args, ctx=None):
    target = _target(args, ctx)

    def work(t, a):
        return obs.rate(t, a["topic"], a.get("seconds", 6))
    return _call_requires_topic(work, args, ctx)


def _topic_sample(args, ctx=None):
    def work(t, a):
        # rclpy with the publisher's QoS, not `ros2 topic echo`: echo subscribes
        # RELIABLE and therefore sees nothing from a BEST_EFFORT publisher such as
        # a camera driver.
        data, result = obs.sample_last(t, a["topic"], a.get("seconds", 6))
        data["ok"] = data["received"]
        if not data["received"]:
            data["error"] = result.error() if not result.ok else "no message in the window"
        return data, result
    return _call_requires_topic(work, args, ctx)


def _camera_snapshot(args, ctx=None):
    topic = (args or {}).get("topic")
    if not topic:
        return json.dumps({"ok": False, "error": "topic is required, e.g. /camera/image_raw/compressed"})
    try:
        target = _target(args, ctx)
        data, result = obs.snapshot(target, topic, args.get("seconds", 8))
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    data["target"] = target.label
    data["ok"] = bool(data.get("ok"))
    if not data["ok"]:
        data.setdefault("error", result.error())
        return json.dumps(data, default=str)

    raw = base64.b64decode(data.pop("data", ""))
    path = _frame_path(topic)
    path.write_bytes(raw)
    data.update({"path": str(path), "saved_bytes": len(raw)})
    return json.dumps(data, default=str)


def _docs_query(args, ctx=None):
    """Answers come from the user's own corpus; nothing is bundled (the upstream
    docs are Apache-2.0 / BSD-3-Clause / CC-BY-4.0 work)."""
    settings = resolve()
    docs_path = (args or {}).get("docs_path") or settings.get("docs_path") or ""
    payload = knowledge.query(
        docs_path,
        (args or {}).get("query", ""),
        limit=(args or {}).get("limit", 4),
        timeout=min(40, int(settings.get("timeout") or 15)),
    )
    return json.dumps(payload, default=str)


def _call_requires_topic(work, args, ctx) -> str:
    if not (args or {}).get("topic"):
        return json.dumps({"ok": False, "error": "topic is required, e.g. /chatter"})
    try:
        target = _target(args, ctx)
        payload, result = work(target, args)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    payload.setdefault("ok", bool(result.ok))
    payload["target"] = target.label
    payload["mode"] = target.mode
    if not payload["ok"] and "error" not in payload:
        payload["error"] = result.error()
    return json.dumps(payload, default=str)


def _frame_path(topic: str):
    from plugins.plugin_storage import plugin_data_dir

    frames = plugin_data_dir("ros2-console") / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    slug = topic.strip("/").replace("/", "_") or "frame"
    return frames / f"{slug}-{int(time.time())}.jpg"


# --------------------------------------------------------------------------- schemas

_TOOLS = [
    Tool(
        name="ros2_host_status",
        emoji="🔌",
        handler=_host_status,
        schema={
            "name": "ros2_host_status",
            "description": (
                "Check that the configured ROS 2 target is reachable and report its distro, "
                "RMW middleware, hostname, uptime and node/topic counts. Call this first when "
                "ROS 2 answers look wrong or a command timed out: it separates 'wrong address' "
                "from 'robot is up but the graph is empty'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string",
                             "description": "Override the SSH target for this call (e.g. robot@10.0.0.5)."},
                    "distro": {"type": "string", "description": "Override the ROS 2 distro, e.g. humble."},
                },
            },
        },
    ),
    Tool(
        name="ros2_graph",
        emoji="🕸️",
        handler=_graph,
        schema={
            "name": "ros2_graph",
            "description": (
                "Read the live ROS 2 graph: nodes and every topic with its type, publisher and "
                "subscriber counts, plus health flags. Flags topics that are structurally "
                "disconnected — 'dead_input' (subscribers but no publisher, so consumers wait "
                "forever) and 'dead_output' (publishers but no subscriber, so results are "
                "discarded). /rosout, /rosout_agg, /parameter_events, and /clock stay in the "
                "topic list with their real health and are left out of orphans unless "
                "include_system is true. `ok` is true only when ROS 2 actually answered: an "
                "unsourced or wrong environment returns ok=false with the reason in `error` "
                "rather than an empty graph. Use it before guessing why a robot misbehaves."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "all_topics": {"type": "boolean",
                                   "description": "Return every topic, not just the live ones (default false)."},
                    "include_system": {"type": "boolean",
                                       "description": "Count ROS housekeeping topics as orphans (default false)."},
                    "host": {"type": "string", "description": "Override the SSH target for this call."},
                    "distro": {"type": "string", "description": "Override the ROS 2 distro."},
                },
            },
        },
    ),
    Tool(
        name="ros2_topic_rate",
        emoji="📈",
        handler=_topic_rate,
        schema={
            "name": "ros2_topic_rate",
            "description": (
                "Measure a topic's actual publish rate in Hz over a window. Returns 0 Hz with "
                "silent=true when nothing arrived — the difference between 'publishes slowly' "
                "and 'publishes nothing' matters, and only this tool tells them apart."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Full topic name, e.g. /chatter."},
                    "seconds": {"type": "integer", "description": "Measurement window, 3-20s (default 6)."},
                    "host": {"type": "string", "description": "Override the SSH target for this call."},
                },
                "required": ["topic"],
            },
        },
    ),
    Tool(
        name="ros2_topic_sample",
        emoji="🔎",
        handler=_topic_sample,
        schema={
            "name": "ros2_topic_sample",
            "description": (
                "Capture one message from a topic as bounded YAML text (array fields omitted, "
                "long strings truncated). Use it to see what a node actually publishes without "
                "flooding the context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Full topic name."},
                    "seconds": {"type": "integer", "description": "Wait for the first message, 2-20s (default 6)."},
                    "host": {"type": "string", "description": "Override the SSH target for this call."},
                },
                "required": ["topic"],
            },
        },
    ),
    Tool(
        name="ros2_camera_snapshot",
        emoji="📷",
        handler=_camera_snapshot,
        schema={
            "name": "ros2_camera_snapshot",
            "description": (
                "Grab one frame from a sensor_msgs/CompressedImage topic and save it as a JPEG "
                "on the machine running Hermes, returning the path so it can be inspected or "
                "shown. This is how a robot's camera becomes something the agent can actually look at."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string",
                              "description": "Compressed image topic, e.g. /camera/image_raw/compressed."},
                    "seconds": {"type": "integer", "description": "Wait for a frame, 2-20s (default 8)."},
                    "host": {"type": "string", "description": "Override the SSH target for this call."},
                },
                "required": ["topic"],
            },
        },
    ),
    Tool(
        name="ros2_docs",
        emoji="📚",
        handler=_docs_query,
        schema={
            "name": "ros2_docs",
            "description": (
                "Answer an architecture question ('how does the executor model work', "
                "'why transient-local durability') from the user's local ROS 2 documentation "
                "corpus, returning bounded excerpts with the upstream repo and commit for "
                "each one. Requires docs_path to be configured; it is a knowledge tool, not "
                "a live-robot tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The question or keywords."},
                    "limit": {"type": "integer", "description": "How many documents to quote, 1-10 (default 4)."},
                },
                "required": ["query"],
            },
        },
    ),
]

TOOLS = _TOOLS
