"""ROS 2 introspection: the scripts sent to the target and the parsers for them.

Everything here is stdlib — the plugin runs `ros2` on the target and reads its
text output, so it works on any distro version from Foxy to Kilted without a
Python dependency that has to match the robot's.
"""

from __future__ import annotations

import re
import shlex

from .transport import Result, Target, run

TOPIC_RE = re.compile(r"^/[A-Za-z0-9_/]+$")
_NUM = re.compile(r"(?:average rate|min|max|std dev|window):\s*([0-9.]+)", re.I)

# Housekeeping. Each one keeps its real health on the topic list. The orphan
# list omits them unless the caller asks, because a quiet log topic is not a
# broken robot.
SYSTEM_TOPICS = frozenset({
    "/clock",
    "/parameter_events",
    "/rosout",
    "/rosout_agg",
})


def validate_topic(topic: str) -> str:
    """Topic names are interpolated into a remote shell, so they are checked first."""
    topic = (topic or "").strip()
    if not TOPIC_RE.match(topic):
        raise ValueError(f"invalid ROS topic name: {topic!r} (expected /name[/sub])")
    return topic


# --------------------------------------------------------------------------- graph

GRAPH_INTROSPECT = r"""
import sys, time
try:
    import rclpy
    from rclpy.node import Node
except Exception:
    print("@@NORCLPY"); sys.exit(0)
try:
    rclpy.init()
    node = Node("_hermes_graph_probe")
except Exception:
    print("@@NORCLPY"); sys.exit(0)
end = time.time() + 3.0
while time.time() < end:
    rclpy.spin_once(node, timeout_sec=0.1)
total_pubs = 0
for name, types in sorted(node.get_topic_names_and_types()):
    typ = types[0] if types else "?"
    try:
        pubs = len(node.get_publishers_info_by_topic(name))
        subs = len(node.get_subscriptions_info_by_topic(name))
    except Exception:
        pubs = subs = -1
    if pubs > 0:
        total_pubs += pubs
    print("@@T|%s|%s|%s|%s" % (name, pubs, subs, typ))
print("@@PUBS %d" % total_pubs)
node.destroy_node()
rclpy.shutdown()
"""

GRAPH_SCRIPT = r"""
echo "@@DISTRO ${ROS_DISTRO:-unknown}"
echo "@@RMW ${RMW_IMPLEMENTATION:-default}"
echo "@@NODES"
ros2 node list 2>&1 | head -300
echo "@@TOPICS"
TOPICS_OUT=$(ros2 topic list -t 2>&1 | head -500)
printf '%s\n' "$TOPICS_OUT"
echo "@@DETAIL"
# Publisher/subscriber counts. The `ros2 topic info` CLI path is serialized by
# the ros2 daemon (~1.9 s per topic measured on a Qualcomm aarch64 board), so ten
# topics cost 19 s and a hundred would cost three minutes. One rclpy process
# introspects the whole graph in a single discovery pass instead. The daemon loop
# stays as the fallback: if rclpy is missing or discovery came back empty, the
# fast path is discarded rather than reporting every topic as dead.
FAST=$(python3 - <<'HERMES_INTROSPECT'
__INTROSPECT__
HERMES_INTROSPECT
)
found=$(printf '%s\n' "$FAST" | grep -c '^@@T|')
pubs=$(printf '%s\n' "$FAST" | sed -n 's/^@@PUBS *//p' | head -1)
want=$(printf '%s\n' "$TOPICS_OUT" | grep -c '^/')
if [ -n "$found" ] && [ "${found:-0}" -ge "${want:-0}" ] && [ "${pubs:-0}" -gt 0 ]; then
  printf '%s\n' "$FAST" | grep '^@@T|'
else
  for t in $(ros2 topic list 2>/dev/null | head -300); do
    info=$(ros2 topic info "$t" 2>/dev/null)
    pub=$(printf '%s\n' "$info" | sed -n 's/^Publisher count: *//p' | head -1)
    sub=$(printf '%s\n' "$info" | sed -n 's/^Subscription count: *//p' | head -1)
    typ=$(printf '%s\n' "$info" | sed -n 's/^Type: *//p' | head -1)
    echo "@@T|$t|${pub:-?}|${sub:-?}|$typ"
  done
fi
echo "@@END"
""".replace("__INTROSPECT__", GRAPH_INTROSPECT)

# A topic with a type but no publisher is invalid input for its subscribers; a
# topic with a publisher and no subscriber is an output nobody consumes. Both
# look "up" in `ros2 node list` — that is the whole reason this check exists.

HEALTH_TEXT = {
    "live": "publishers and subscribers connected",
    "dead_input": "has subscribers but nobody publishes — subscribers wait forever",
    "dead_output": "has publishers but nobody subscribes — output is being discarded",
    "unmeasured": "publisher/subscriber counts unavailable (permissions or a slow host)",
}


def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"head": []}
    current = "head"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("@@NODES", "@@TOPICS", "@@DETAIL", "@@END"):
            current = stripped.strip("@").lower()
            sections.setdefault(current, [])
            continue
        if current == "head":
            if stripped.startswith("@@DISTRO "):
                sections["head"].append(stripped[len("@@DISTRO "):])
            elif stripped.startswith("@@RMW "):
                sections["head"].append("rmw=" + stripped[len("@@RMW "):])
            continue
        sections[current].append(line)
    return sections


def parse_graph(text: str, *, include_system: bool = False) -> dict:
    sections = _split_sections(text)
    head = sections.get("head") or []
    distro = head[0].strip() if head else "unknown"
    rmw = head[1].split("=", 1)[1].strip() if len(head) > 1 else "default"

    nodes = sorted({ln.strip() for ln in sections.get("nodes", []) if ln.strip().startswith("/")})

    topics: dict[str, dict] = {}
    for line in sections.get("topics", []):
        line = line.strip()
        if not line.startswith("/"):
            continue
        match = re.match(r"^(/\S+)\s+\[(.+?)\]", line)
        if match:
            topics[match.group(1)] = {"name": match.group(1), "type": match.group(2),
                                      "publishers": None, "subscribers": None}

    for line in sections.get("detail", []):
        if not line.startswith("@@T|"):
            continue
        _, name, pubs, subs, typ = (line.split("|", 4) + ["", "", "", "", ""])[:5]
        entry = topics.setdefault(name, {"name": name, "type": typ, "publishers": None,
                                         "subscribers": None})
        if typ and not entry.get("type"):
            entry["type"] = typ
        entry["publishers"] = _count(pubs)
        entry["subscribers"] = _count(subs)

    for entry in topics.values():
        entry["health"], entry["health_text"] = _classify(entry)

    ordered = sorted(topics.values(), key=lambda t: t["name"])
    broken = [t for t in ordered if t["health"] in ("dead_input", "dead_output")]
    withheld = [t for t in broken if t["name"] in SYSTEM_TOPICS]
    orphans = broken if include_system else [t for t in broken if t["name"] not in SYSTEM_TOPICS]
    return {
        "ros_distro": distro,
        "rmw": rmw,
        "nodes": nodes,
        "topics": ordered,
        "counts": {
            "nodes": len(nodes),
            "topics": len(ordered),
            "orphans": len(orphans),
            "system_excluded": 0 if include_system else len(withheld),
        },
        "orphans": [{"name": t["name"], "type": t["type"], "health": t["health"],
                     "detail": t["health_text"],
                     "publishers": t["publishers"], "subscribers": t["subscribers"]}
                    for t in orphans],
    }


def _count(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def _classify(entry: dict) -> tuple[str, str]:
    pubs, subs = entry.get("publishers"), entry.get("subscribers")
    if pubs is None or subs is None:
        return "unmeasured", HEALTH_TEXT["unmeasured"]
    if subs > 0 and pubs == 0:
        return "dead_input", HEALTH_TEXT["dead_input"]
    if pubs > 0 and subs == 0:
        return "dead_output", HEALTH_TEXT["dead_output"]
    return "live", HEALTH_TEXT["live"]


def graph(target: Target, *, include_system: bool = False) -> tuple[dict | None, Result]:
    result = run(target, GRAPH_SCRIPT, timeout=max(target.timeout, 20), label="graph")
    if not result.ok and "@@END" not in result.stdout:
        return None, result
    return parse_graph(result.stdout, include_system=include_system), result


# ---------------------------------------------------------------------------- rate

def _rate_script(topic: str, seconds: int, window: int) -> str:
    # `ros2 topic hz` ignores SIGTERM, so a plain `timeout N` never returns.
    return (
        f"timeout -s KILL {int(seconds)} ros2 topic hz --window {int(window)} "
        f"{shlex.quote(topic)} 2>&1 | tail -8"
    )


def _info_script(topic: str) -> str:
    return f"ros2 topic info {shlex.quote(topic)} 2>&1"


def _publisher_count(text: str) -> int | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Publisher count:"):
            raw = stripped.split(":", 1)[1].strip()
            return int(raw) if raw.isdigit() else None
    return None


def parse_rate(text: str) -> dict:
    rates = [float(m) for m in re.findall(r"average rate:\s*([0-9.]+)", text)]
    stats = {}
    for line in text.splitlines():
        for key in ("min", "max"):
            match = re.search(rf"\b{key}:\s*([0-9.]+)", line)
            if match:
                stats[key] = float(match.group(1))
    tail = text.strip().splitlines()[-3:]
    if not rates:
        lower = text.lower()
        # Jazzy prints this and exits 0 when a topic has subscribers and no publisher.
        missing_publisher = "does not appear to be published yet" in lower
        silent = missing_publisher or "no new messages" in lower or not text.strip()
        data = {"hz": 0.0, "samples": 0, "silent": silent, "stats": stats, "raw_tail": tail}
        if missing_publisher:
            data["hint"] = "missing publisher: nobody is publishing this topic"
        return data
    return {"hz": rates[-1], "samples": len(rates), "silent": False, "stats": stats,
            "raw_tail": tail}


def _unpublished(topic: str) -> dict:
    return {
        "hz": 0.0,
        "samples": 0,
        "silent": True,
        "stats": {},
        "raw_tail": [],
        "topic": topic,
        "measured_seconds": 0,
        "hint": f"missing publisher: {topic} is unpublished",
    }


def rate(target: Target, topic: str, seconds: int = 6, window: int = 30) -> tuple[dict, Result]:
    """Measure the receive rate over a real window.

    The window is a MESSAGE count, not a time: at 10 Hz a window of 50 needs five
    seconds to fill, so a short measurement reports a half-filled average and reads
    low. `window` defaults to 30 so the number converges inside a 3 s measurement.
    The result carries min/max/std_dev so a stalling publisher is visible in the
    tool output instead of being averaged away.
    """
    topic = validate_topic(topic)
    seconds = max(3, min(20, int(seconds)))
    info = run(target, _info_script(topic), timeout=5, label=f"info {topic}")
    if _publisher_count(info.stdout or "") == 0:
        return _unpublished(topic), info
    result = run(target, _rate_script(topic, seconds, window), timeout=seconds + 8,
                 label=f"rate {topic}")
    # Empty stdout is silent even when SSH wrote a warning on stderr.
    data = parse_rate(result.stdout or "")
    data["topic"] = topic
    data["measured_seconds"] = seconds
    data["window"] = window
    if data["silent"] and "hint" not in data:
        data["hint"] = (
            "no messages arrived in the window — either the topic has no live publisher "
            "(run ros2_graph and check the publisher count) or its QoS does not match"
        )
    return data, result


# -------------------------------------------------------------------------- sample

def sample(target: Target, topic: str, seconds: int = 6) -> tuple[dict, Result]:
    topic = validate_topic(topic)
    seconds = max(2, min(20, int(seconds)))
    script = (
        f"timeout {seconds} ros2 topic echo --once --no-arr --truncate-length 80 "
        f"{shlex.quote(topic)} 2>&1 | head -100"
    )
    result = run(target, script, timeout=seconds + 8, label=f"sample {topic}")
    text = (result.stdout or "").strip()
    body = "\n".join(
        line for line in text.splitlines()
        if line.strip() and "truncated" not in line.lower()
    )
    return {
        "topic": topic,
        "received": bool(body) and not body.startswith("WARNING"),
        "message": body[:4000],
        "bytes": len(body),
        "hint": None if body else "nothing published in the window; check the publisher count first",
    }, result


# -------------------------------------------------------------------------- camera

SNAPSHOT_BODY = r'''
import base64
import sys
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage

topic, seconds = sys.argv[1], float(sys.argv[2])
rclpy.init()
node = Node("hermes_ros2_snapshot")
box = {}


def handle(msg):
    box["data"] = bytes(msg.data)
    box["format"] = msg.format
    box["stamp"] = f"{msg.header.stamp.sec}.{msg.header.stamp.nanosec:09d}"


node.create_subscription(CompressedImage, topic, handle, 1)
deadline = time.time() + seconds
while rclpy.ok() and "data" not in box and time.time() < deadline:
    rclpy.spin_once(node, timeout_sec=0.2)
node.destroy_node()
rclpy.shutdown()

if "data" not in box:
    print("@@ERR no CompressedImage within %.1fs on %s" % (seconds, topic))
    sys.exit(3)
print("@@FORMAT " + str(box["format"]))
print("@@STAMP " + box["stamp"])
print("@@BYTES %d" % len(box["data"]))
print("@@B64 " + base64.b64encode(box["data"]).decode("ascii"))
'''


def snapshot(target: Target, topic: str, seconds: int = 8) -> tuple[dict, Result]:
    """Grab one camera frame. Raw `sensor_msgs/Image` is refused with a pointer to republish."""
    topic = validate_topic(topic)
    seconds = max(2, min(20, int(seconds)))
    script = f"python3 - {shlex.quote(topic)} {seconds} <<'HERMES_SNAPSHOT'\n{SNAPSHOT_BODY}\nHERMES_SNAPSHOT"
    result = run(target, script, timeout=seconds + 12, label=f"snapshot {topic}")
    stdout = result.stdout or ""
    if "@@ERR" in stdout:
        message = stdout.split("@@ERR", 1)[1].strip().splitlines()[0]
        return {"topic": topic, "ok": False, "error": message,
                "hint": "this tool reads sensor_msgs/CompressedImage; publish a compressed "
                        "topic (image_transport republish) or use ros2_topic_sample instead"}, result
    if "@@B64 " not in stdout:
        return {"topic": topic, "ok": False,
                "error": (result.error() if not result.ok else "no image payload returned")}, result

    payload = stdout.split("@@B64 ", 1)[1].strip()
    meta = {}
    for line in stdout.split("@@B64 ", 1)[0].splitlines():
        if line.startswith("@@FORMAT "):
            meta["format"] = line[len("@@FORMAT "):].strip()
        elif line.startswith("@@STAMP "):
            meta["stamp"] = line[len("@@STAMP "):].strip()
        elif line.startswith("@@BYTES "):
            meta["bytes"] = int(line[len("@@BYTES "):].strip() or 0)
    return {"topic": topic, "ok": True, "encoding": "base64", "data": payload,
            **meta}, result


# ----------------------------------------------------------------------- host info

HOST_SCRIPT = r"""
echo "@@DISTRO ${ROS_DISTRO:-unknown}"
echo "@@RMW ${RMW_IMPLEMENTATION:-default}"
echo "@@UPTIME $(uptime -p 2>/dev/null || uptime)"
echo "@@HOST $(hostname 2>/dev/null)"
echo "@@NODES $(ros2 node list 2>/dev/null | wc -l)"
echo "@@TOPICS $(ros2 topic list 2>/dev/null | wc -l)"
echo "@@ROS2 $(ros2 --version 2>/dev/null || echo unknown)"
"""


def host_status(target: Target) -> tuple[dict, Result]:
    result = run(target, HOST_SCRIPT, timeout=min(target.timeout, 30), label="host_status")
    info: dict = {"reachable": False, "mode": target.mode, "target": target.label}
    for line in (result.stdout or "").splitlines():
        if not line.startswith("@@"):
            continue
        key, _, value = line[2:].partition(" ")
        info[key.lower()] = value.strip()
    info["reachable"] = bool(result.ok and info.get("distro") not in (None, "", "unknown"))
    if not info["reachable"]:
        info["error"] = result.error()
    info["seconds"] = round(result.seconds, 2)
    return info, result
