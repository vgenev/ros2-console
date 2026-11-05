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
rows = []
for name, types in sorted(node.get_topic_names_and_types()):
    typ = types[0] if types else "?"
    try:
        pinfo = node.get_publishers_info_by_topic(name)
        sinfo = node.get_subscriptions_info_by_topic(name)
        pubs, subs = len(pinfo), len(sinfo)
    except Exception:
        pinfo = sinfo = []
        pubs = subs = -1
    rows.append([name, typ, pubs, subs, pinfo, sinfo])

# An endpoint can be missing from the graph cache for a moment mid-discovery,
# which would report a healthy topic as dead. Anything suspicious gets a second
# look after another discovery interval, and the higher count wins: a false
# orphan is worse than a slow answer.
suspicious = [row for row in rows if row[2] == 0 or row[3] == 0]
if suspicious:
    time.sleep(1.0)
    for _ in range(3):
        rclpy.spin_once(node, timeout_sec=0.2)
    for row in suspicious:
        try:
            row[2] = max(row[2], len(node.get_publishers_info_by_topic(row[0])))
            row[3] = max(row[3], len(node.get_subscriptions_info_by_topic(row[0])))
        except Exception:
            pass

# Connectivity is not traffic: a topic can have both endpoints and still carry
# nothing. Count actual messages over a short window so the UI can animate by
# measured rate instead of by wiring.
TRAFFIC_WINDOW = 2.0
rates = {}
counts = {}
try:
    from rosidl_runtime_py.utilities import get_message
    from rclpy.qos import QoSProfile
except Exception:
    get_message = None
if get_message is not None:
    for name, typ, pubs, subs, pinfo, sinfo in rows:
        if pubs <= 0 or name in ("/rosout", "/parameter_events"):
            continue
        try:
            cls = get_message(typ)
        except Exception:
            continue
        qos = QoSProfile(depth=10)
        if pinfo:
            try:
                qos.reliability = pinfo[0].qos_profile.reliability
                qos.durability = pinfo[0].qos_profile.durability
            except Exception:
                pass
        counts[name] = 0
        try:
            node.create_subscription(
                cls, name,
                (lambda msg, t=name: counts.__setitem__(t, counts.get(t, 0) + 1)),
                qos,
            )
        except Exception:
            counts.pop(name, None)
    if counts:
        end = time.time() + TRAFFIC_WINDOW
        while time.time() < end:
            rclpy.spin_once(node, timeout_sec=0.05)
    for name, count in counts.items():
        rates[name] = count / TRAFFIC_WINDOW

total_pubs = 0
latched_topics = set()
for name, typ, pubs, subs, pinfo, sinfo in rows:
    for info in pinfo:
        try:
            raw = info.qos_profile.durability
            label = getattr(raw, "name", None) or str(raw)
            if str(label).split(".")[-1].upper() in ("TRANSIENT_LOCAL", "TRANSIENT"):
                latched_topics.add(name)
                break
        except Exception:
            pass
for name, typ, pubs, subs, pinfo, sinfo in rows:
    if pubs > 0:
        total_pubs += pubs
    print("@@T|%s|%s|%s|%s" % (name, pubs, subs, typ))
    if name in rates:
        rate = rates[name]
        # A latched publisher replays its stored sample to a new subscriber, so one
        # message in the window is delivery of state, not a 0.5 Hz stream. Report
        # zero traffic for it instead of inventing a rate.
        if name in latched_topics and counts.get(name, 0) <= 1:
            rate = 0.0
        print("@@R|%s|%.3f" % (name, rate))
    for role, infos in (("P", pinfo), ("S", sinfo)):
        for info in infos:
            node_name = (info.node_namespace.rstrip("/") + "/" + info.node_name).replace("//", "/")
            if "UNKNOWN" in node_name:
                node_name = "<unnamed participant>"
            print("@@E|%s|%s|%s|%s" % (name, role, node_name, getattr(info, "topic_type", typ)))
            if role == "P":
                try:
                    raw = info.qos_profile.durability
                    label = getattr(raw, "name", None) or str(raw)
                    durability = str(label).split(".")[-1].upper()
                except Exception:
                    durability = "UNKNOWN"
                print("@@D|%s|%s" % (name, durability))
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
  printf '%s\n' "$FAST" | grep -E '^(@@T|@@E|@@D|@@R)\|'
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
    "state": "latched state topic: publishes on change and a late subscriber still receives "
             "the current value, so no subscriber yet is not a fault",
    "silent": "endpoints are connected but no message arrived during the sample window — "
              "wiring is fine, traffic is not",
}

# Durability that makes "nobody subscribed right now" a non-event: the publisher
# holds the last sample and delivers it when a consumer finally connects. Calling
# those orphans produces false alarms on exactly the topics that are healthy.
LATCHED_DURABILITY = {"TRANSIENT_LOCAL", "TRANSIENT"}


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

    edges: list[dict] = []
    for line in sections.get("detail", []):
        if line.startswith("@@D|"):
            parts = (line.split("|", 2) + ["", "", ""])[:3]
            _, topic_name, durability = parts
            entry = topics.get(topic_name)
            if entry is not None and durability:
                entry["durability"] = durability.upper()
            continue
        if line.startswith("@@R|"):
            parts = (line.split("|", 2) + ["", "", ""])[:3]
            _, topic_name, rate = parts
            entry = topics.get(topic_name)
            if entry is not None and rate:
                try:
                    entry["rate_hz"] = float(rate)
                except ValueError:
                    pass
            continue
        if not line.startswith("@@E|"):
            continue
        parts = (line.split("|", 4) + ["", "", "", ""])[:4]
        _, topic_name, role, node_name = parts
        edges.append({
            "topic": topic_name,
            "role": "publisher" if role == "P" else "subscriber",
            "node": node_name or "?",
        })

    for entry in topics.values():
        entry["health"], entry["health_text"] = _classify(entry)

    ordered = sorted(topics.values(), key=lambda t: t["name"])
    broken = [t for t in ordered if t["health"] in ("dead_input", "dead_output")]
    withheld = [t for t in broken if t["name"] in SYSTEM_TOPICS]
    orphans = broken if include_system else [t for t in broken if t["name"] not in SYSTEM_TOPICS]
    latched = [t for t in ordered if t["health"] == "state"]
    return {
        "ros_distro": distro,
        "rmw": rmw,
        "nodes": nodes,
        "topics": ordered,
        "edges": edges,
        "counts": {
            "nodes": len(nodes),
            "topics": len(ordered),
            "orphans": len(orphans),
            "system_excluded": 0 if include_system else len(withheld),
            "latched": len(latched),
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
        if str(entry.get("durability") or "").upper() in LATCHED_DURABILITY:
            return "state", HEALTH_TEXT["state"]
        return "dead_output", HEALTH_TEXT["dead_output"]
    if entry.get("rate_hz") == 0:
        return "silent", HEALTH_TEXT["silent"]
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
import io
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

topic, seconds = sys.argv[1], float(sys.argv[2])
rclpy.init()
node = Node("hermes_ros2_snapshot")
box = {}

# Which message type does this topic actually carry? Ask the graph instead of
# guessing: a raw `sensor_msgs/Image` and a `CompressedImage` share a topic name
# shape but need different decoding, and most robot cameras publish raw.
types = []
settle = time.time() + 4.0
while time.time() < settle and not types:
    types = dict(node.get_topic_names_and_types()).get(topic, [])
    rclpy.spin_once(node, timeout_sec=0.15)

if not types:
    # Discovery can lag on a busy host. Absence from the cache is not proof the
    # topic is missing, so still try the compressed path before giving up: the
    # subscription itself will fail fast if the topic truly is not there.
    types = ["sensor_msgs/msg/CompressedImage"]

msg_type = types[0]
if not msg_type.endswith(("/Image", "/CompressedImage")):
    print("@@ERR topic %s carries %s, not an image (CompressedImage or Image)" % (topic, msg_type))
    sys.exit(3)
is_raw = msg_type.endswith("/Image")


def handle_compressed(msg):
    box["data"] = bytes(msg.data)
    box["format"] = msg.format
    box["stamp"] = f"{msg.header.stamp.sec}.{msg.header.stamp.nanosec:09d}"


def handle_raw(msg):
    import numpy as np
    from PIL import Image

    h, w, step = msg.height, msg.width, msg.step
    channels = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "bgra8": 4, "mono8": 1}.get(msg.encoding)
    if channels is None:
        box["err"] = "unsupported encoding %s (rgb8, bgr8, rgba8, bgra8, mono8)" % msg.encoding
        return
    buf = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    if buf.size < h * step:
        box["err"] = "truncated payload: %d bytes for %dx%d step %d" % (buf.size, h, w, step)
        return
    rows = buf[: h * step].reshape(h, step)[:, : w * channels]
    arr = rows.reshape(h, w, channels)
    if channels == 1:
        img = Image.fromarray(arr[:, :, 0], mode="L")
    else:
        if msg.encoding in ("bgr8", "bgra8"):
            arr = arr[:, :, [2, 1, 0] + ([3] if channels == 4 else [])]
        img = Image.fromarray(arr[:, :, :3], mode="RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    box["data"] = out.getvalue()
    box["format"] = "jpeg"
    box["source"] = "%s %dx%d %s" % (msg.encoding, w, h, msg_type.split("/")[-1])
    box["stamp"] = f"{msg.header.stamp.sec}.{msg.header.stamp.nanosec:09d}"


if is_raw:
    from sensor_msgs.msg import Image

    node.create_subscription(Image, topic, handle_raw, qos_profile_sensor_data)
else:
    from sensor_msgs.msg import CompressedImage

    node.create_subscription(CompressedImage, topic, handle_compressed, qos_profile_sensor_data)

deadline = time.time() + seconds
while rclpy.ok() and "data" not in box and "err" not in box and time.time() < deadline:
    rclpy.spin_once(node, timeout_sec=0.2)
node.destroy_node()
rclpy.shutdown()

if "err" in box:
    print("@@ERR " + box["err"])
    sys.exit(3)
if "data" not in box:
    print("@@ERR no %s within %.1fs on %s" % (msg_type.split("/")[-1], seconds, topic))
    sys.exit(3)
print("@@SOURCE " + str(box.get("source", box["format"])))
print("@@FORMAT " + str(box["format"]))
print("@@STAMP " + box["stamp"])
print("@@BYTES %d" % len(box["data"]))
print("@@B64 " + base64.b64encode(box["data"]).decode("ascii"))
'''


def snapshot(target: Target, topic: str, seconds: int = 8) -> tuple[dict, Result]:
    """Grab one camera frame. Handles `CompressedImage` and raw `Image` (decoded on the robot)."""
    topic = validate_topic(topic)
    seconds = max(2, min(20, int(seconds)))
    script = f"python3 - {shlex.quote(topic)} {seconds} <<'HERMES_SNAPSHOT'\n{SNAPSHOT_BODY}\nHERMES_SNAPSHOT"
    result = run(target, script, timeout=seconds + 12, label=f"snapshot {topic}")
    stdout = result.stdout or ""
    if "@@ERR" in stdout:
        message = stdout.split("@@ERR", 1)[1].strip().splitlines()[0]
        return {"topic": topic, "ok": False, "error": message,
                "hint": "check the topic exists on this graph and is publishing; raw Image is "
                        "decoded on the robot when numpy and PIL are present"}, result
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
        elif line.startswith("@@SOURCE "):
            meta["source"] = line[len("@@SOURCE "):].strip()
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
