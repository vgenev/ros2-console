"""ROS 2 introspection: the scripts sent to the target and the parsers for them.

Everything here is stdlib — the plugin runs `ros2` on the target and reads its
text output, so it works on any distro version from Foxy to Kilted without a
Python dependency that has to match the robot's. Only the yuyv decoder imports
numpy, at call time, inside the shipped script.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Host-side default for the traffic window. The script that runs on the robot has
# its own TRAFFIC_WINDOW (it is embedded in that script's source, not in this
# module); this mirrors it for parsing a capture that predates provenance fields.
MEASURED_WINDOW_DEFAULT = 2.0

import inspect
import re
import shlex

from .transport import Result, Target, ensure_complete, run

TOPIC_RE = re.compile(r"^/[A-Za-z0-9_/]+$")

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
# measured rate instead of by wiring. Topics that can exceed the byte budget
# are skipped before subscribe, and a first message over the budget is dropped
# from the count — neither is reported as 0.0/s.
TRAFFIC_WINDOW = 2.0
TRAFFIC_BYTE_BUDGET = 1000000
# Short form: "pkg/msg/Name" and "pkg/Name" both become "pkg/Name".
_HEAVY_TYPES = {
    "sensor_msgs/Image",
    "sensor_msgs/CompressedImage",
    "sensor_msgs/PointCloud2",
    "sensor_msgs/PointCloud",
    "sensor_msgs/LaserScan",
    "nav_msgs/OccupancyGrid",
    "audio_common_msgs/AudioData",
}

def _short_type(typ):
    parts = (typ or "").split("/")
    if len(parts) >= 3 and parts[-2] == "msg":
        return parts[0] + "/" + parts[-1]
    if len(parts) >= 2:
        return parts[0] + "/" + parts[-1]
    return typ or ""

def _type_skip_reason(typ):
    short = _short_type(typ)
    leaf = short.rsplit("/", 1)[-1]
    heavy = (
        short in _HEAVY_TYPES
        or leaf.endswith("Image")
        or leaf.endswith("PointCloud2")
        or leaf.endswith("OccupancyGrid")
    )
    if not heavy:
        return None
    return (
        "message type %s can carry more than the %d-byte sampling budget, "
        "so its rate was not measured" % (typ, TRAFFIC_BYTE_BUDGET)
    )

def _measured_bytes(msg):
    # Sum len() of the message's byte and sequence fields.
    total = 0
    names = []
    try:
        names = list(msg.get_fields_and_field_types())
    except Exception:
        names = []
    if not names:
        for slot in getattr(msg, "__slots__", ()):
            names.append(slot[1:] if str(slot).startswith("_") else slot)
    for name in names:
        try:
            value = getattr(msg, name)
        except Exception:
            continue
        if isinstance(value, (bytes, bytearray, list, tuple)):
            total += len(value)
    return total

rates = {}
counts = {}
skipped = {}

def _on_traffic(msg, topic_name):
    # Destroying a subscription from inside its own callback can block the
    # executor, so an over-budget topic is only un-counted and the node is
    # destroyed at the end of the window. This is a deliberate trade: one
    # window of deserialisation, never a hung graph call.
    if topic_name not in counts:
        return
    if counts[topic_name] == 0:
        measured = _measured_bytes(msg)
        if measured > TRAFFIC_BYTE_BUDGET:
            counts.pop(topic_name, None)
            skipped[topic_name] = (
                "first message measured %d bytes, over the %d-byte sampling budget"
                % (measured, TRAFFIC_BYTE_BUDGET)
            )
            return
    counts[topic_name] = counts.get(topic_name, 0) + 1

try:
    from rosidl_runtime_py.utilities import get_message
    from rclpy.qos import QoSProfile
except Exception:
    get_message = None
if get_message is not None:
    for name, typ, pubs, subs, pinfo, sinfo in rows:
        if pubs <= 0 or name in ("/rosout", "/parameter_events"):
            continue
        reason = _type_skip_reason(typ)
        if reason:
            skipped[name] = reason
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
                (lambda msg, t=name: _on_traffic(msg, t)),
                qos,
            )
        except Exception:
            counts.pop(name, None)
    if counts:
        end = time.time() + TRAFFIC_WINDOW
        while time.time() < end:
            rclpy.spin_once(node, timeout_sec=0.05)
    for name, count in counts.items():
        if name in skipped:
            continue
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
        # The rate is emitted with the evidence that produced it: how many
        # messages, over what window, and when. ROS 2 publishes no telemetry, so
        # a number without these fields is an assertion, not a measurement.
        print("@@R|%s|%.3f|%d|%.1f|%.0f" % (
            name, rate, counts.get(name, 0), TRAFFIC_WINDOW, time.time()))
    if name in skipped:
        print("@@U|%s|%s" % (name, skipped[name]))
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
# "The shell ran" is not "ROS answered". A reachable host with an unsourced or
# missing ROS environment runs this script perfectly and returns nothing, which a
# parser cannot tell apart from a healthy robot with no nodes. Discovery therefore
# has to announce itself: no marker means failure, with the reason attached.
REQUESTED_DISTRO=__REQUESTED_DISTRO__
echo "@@DISTRO ${ROS_DISTRO:-unknown}"
echo "@@RMW ${RMW_IMPLEMENTATION:-default}"
if ! command -v ros2 >/dev/null 2>&1; then
  echo "@@DISCOVERY FAIL ros2 is not on PATH on this host: the ROS 2 environment for distro '$REQUESTED_DISTRO' is not installed or could not be sourced (ROS_DISTRO is '${ROS_DISTRO:-unset}')"
  echo "@@END"
  exit 3
fi
echo "@@NODES"
NODES_OUT=$(ros2 node list 2>&1)
NODES_RC=$?
printf '%s\n' "$NODES_OUT" | head -300
if [ "$NODES_RC" -ne 0 ]; then
  NODES_ERR=$(printf '%s\n' "$NODES_OUT" | head -1)
  echo "@@DISCOVERY FAIL ros2 node list exited $NODES_RC on distro '${ROS_DISTRO:-unknown}': ${NODES_ERR:-no error text}"
  echo "@@END"
  exit 3
fi
echo "@@TOPICS"
TOPICS_OUT=$(ros2 topic list -t 2>&1)
TOPICS_RC=$?
printf '%s\n' "$TOPICS_OUT" | head -500
if [ "$TOPICS_RC" -ne 0 ]; then
  TOPICS_ERR=$(printf '%s\n' "$TOPICS_OUT" | head -1)
  echo "@@DISCOVERY FAIL ros2 topic list exited $TOPICS_RC on distro '${ROS_DISTRO:-unknown}': ${TOPICS_ERR:-no error text}"
  echo "@@END"
  exit 3
fi
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
  printf '%s\n' "$FAST" | grep -E '^(@@T|@@E|@@D|@@R|@@U)\|'
else
  for t in $(ros2 topic list 2>/dev/null | head -300); do
    info=$(ros2 topic info "$t" 2>/dev/null)
    pub=$(printf '%s\n' "$info" | sed -n 's/^Publisher count: *//p' | head -1)
    sub=$(printf '%s\n' "$info" | sed -n 's/^Subscription count: *//p' | head -1)
    typ=$(printf '%s\n' "$info" | sed -n 's/^Type: *//p' | head -1)
    echo "@@T|$t|${pub:-?}|${sub:-?}|$typ"
  done
fi
echo "@@DISCOVERY OK"
echo "@@END"
""".replace("__INTROSPECT__", GRAPH_INTROSPECT)

# A topic with a type but no publisher is invalid input for its subscribers; a
# topic with a publisher and no subscriber is an output nobody consumes. Both
# look "up" in `ros2 node list` — that is the whole reason this check exists.

HEALTH_TEXT = {
    "live": "publishers and subscribers connected and traffic measured above 0 Hz",
    "dead_input": "has subscribers but nobody publishes — subscribers wait forever",
    "dead_output": "has publishers but nobody subscribes — output is being discarded",
    "unmeasured": "publisher/subscriber counts unavailable (permissions or a slow host)",
    "state": "latched state topic: publishes on change and a late subscriber still receives "
             "the current value, so no subscriber yet is not a fault",
    "silent": "endpoints are connected but no message arrived during the sample window — "
              "wiring is fine, traffic is not",
}

# `live` is a claim about data flowing, so it needs a measurement, not just wiring.
# Two ways to have no measurement: the counts themselves are missing, or the
# endpoints are there but no rate sample came back for the topic.
UNMEASURED_RATE_TEXT = (
    "endpoints are connected but the rate was not measured — no traffic sample was "
    "returned for this topic, so this is not evidence that data is flowing"
)
UNMEASURED_ENDPOINTS_TEXT = (
    "no publisher and no subscriber is connected to this topic and nothing was "
    "measured — it is not live"
)

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


DISCOVERY_OK = "@@DISCOVERY OK"
DISCOVERY_FAIL = "@@DISCOVERY FAIL"


def _discovery(text: str) -> tuple[bool, str | None]:
    """Did ROS 2 answer, or did only the remote shell run?

    A shell that runs and a ROS graph that answers produce the same empty stdout
    when the environment is not sourced, so the script announces discovery and
    this is the only thing that can tell the two apart.
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped == DISCOVERY_OK:
            return True, None
        if stripped.startswith(DISCOVERY_FAIL):
            reason = stripped[len(DISCOVERY_FAIL):].strip()
            return False, reason or "ROS 2 did not answer on this host"
    return False, ("no discovery marker in the output: the remote shell ran but ROS 2 "
                   "never answered — the ROS environment is not sourced there")


def _float_or_none(raw: str, fallback: float | None = None) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def _int_or_none(raw: str) -> int | None:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _iso_or_none(raw: str) -> str | None:
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


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
            parts = (line.split("|", 5) + [""] * 6)[:6]
            _, topic_name, rate, msgs, window, at = parts
            entry = topics.get(topic_name)
            if entry is not None and rate:
                try:
                    entry["rate_hz"] = float(rate)
                except ValueError:
                    continue
                # Provenance is part of the value: a rate nobody can attribute is
                # not evidence. Fields absent from an older capture are reported
                # as unstated rather than filled in with a plausible guess.
                entry["measured"] = {
                    "method": "subscribe+count",
                    "window_s": _float_or_none(window, MEASURED_WINDOW_DEFAULT),
                    "msgs": _int_or_none(msgs),
                    "at": _iso_or_none(at),
                }
            continue
        if line.startswith("@@U|"):
            parts = (line.split("|", 2) + ["", "", ""])[:3]
            _, topic_name, reason = parts
            entry = topics.get(topic_name)
            if entry is not None and reason:
                entry["rate_reason"] = reason.strip()
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
    answered, discovery_error = _discovery(text)
    return {
        "ros_answered": answered,
        "discovery_error": discovery_error,
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
            "rate_skipped": sum(1 for t in ordered if t.get("rate_reason")),
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
    if pubs == 0 and subs == 0:
        # Nothing is connected to it: no traffic sample can exist, so silence here
        # is an absence of measurement, not a measurement of silence.
        return "unmeasured", UNMEASURED_ENDPOINTS_TEXT
    if subs > 0 and pubs == 0:
        return "dead_input", HEALTH_TEXT["dead_input"]
    if pubs > 0 and subs == 0:
        if str(entry.get("durability") or "").upper() in LATCHED_DURABILITY:
            return "state", HEALTH_TEXT["state"]
        return "dead_output", HEALTH_TEXT["dead_output"]
    rate = entry.get("rate_hz")
    if rate is None:
        reason = entry.get("rate_reason")
        if reason:
            return "unmeasured", "rate not measured — " + reason
        # Both endpoints present but the payload carried no @@R sample (the daemon
        # fallback never measures). Wiring is not traffic: do not call this live.
        return "unmeasured", UNMEASURED_RATE_TEXT
    if rate <= 0:
        return "silent", HEALTH_TEXT["silent"]
    return "live", HEALTH_TEXT["live"]


def _graph_script(target: Target) -> str:
    return GRAPH_SCRIPT.replace("__REQUESTED_DISTRO__", shlex.quote(target.distro or "unknown"))


def graph(target: Target, *, include_system: bool = False) -> tuple[dict | None, Result]:
    """Read the graph, refusing to call an unanswered discovery an empty robot."""
    # Reads measured 11-18s on the board depending on whether the colour publisher
    # is busy, so a stored per-command timeout of 15 s cuts a healthy read in half
    # and reports it as a dead ROS environment. The read owns its own floor.
    result = run(target, _graph_script(target), timeout=max(target.timeout, 45), label="graph")
    text = result.stdout or ""
    answered, reason = _discovery(text)
    if not answered or "@@END" not in text:
        if answered:
            reason = (reason or "the graph command did not finish") + \
                     " (output ended before @@END — the run was cut short)"
        elif not ("@@DISCOVERY FAIL" in text or "@@DISTRO" in text):
            # Not even the script's own preamble came back: ssh refused, the key
            # failed, or the run timed out. The transport error is the story.
            reason = result.error()
        elif "@@END" not in text:
            # The preamble arrived and the graph never did. That is a cut-short run,
            # not an unsourced shell: only say "not sourced" when the script had
            # every chance to speak and stayed silent.
            reason = ("the read reached the distro line but the output ended before discovery "
                      "(%d B, rc=%s) — the run was cut short, not an unsourced shell"
                      % (len(text), result.exit_code))
        # `result.ok` can be true here: an unsourced shell exits cleanly. The
        # marker is the evidence, and there is none.
        result.meta["discovery_error"] = reason
        return None, result
    return parse_graph(text, include_system=include_system), result


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


# -------------------------------------------------------------------------- camera

JPEG_B64_BUDGET = 170_000  # characters of base64, inside the transport cap


def encode_jpeg(img, budget: int = JPEG_B64_BUDGET):
    """Encode at native size, flexing quality (not resolution) to fit the budget.

    The transport refuses a truncated capture, and a cut base64 block is a corrupt
    image rather than a smaller one. Resolution is what a dashboard reader needs;
    JPEG quality is the thing that can give way. 4:2:0 subsampling is dropped at the
    top rung because it is most of what makes chroma edges look muddy in a small tile.
    """
    import io

    ladder = ((92, 0), (88, 2), (82, 2), (74, 2), (64, 2))
    encoded, chosen = b"", ladder[-1][0]
    for quality, subsampling in ladder:
        out = io.BytesIO()
        if img.mode == "RGB":
            img.save(out, format="JPEG", quality=quality, subsampling=subsampling)
        else:
            img.save(out, format="JPEG", quality=quality)
        encoded, chosen = out.getvalue(), quality
        if len(encoded) * 4 // 3 <= budget:
            break
    return encoded, chosen


def yuyv_to_rgb(yuy2, height, width, step, max_width=None):
    """YUYV/YUY2 (4:2:2) -> (h, w, 3) uint8 RGB, downscaling BEFORE the colour maths.

    numpy only, on purpose: this source is also shipped to the robot (see SNAPSHOT_SCRIPT),
    which has numpy and Pillow but not OpenCV.

    max_width=None keeps the frame at native size, which is what a snapshot wants: the caller
    asked for one picture, not a stream, so the colour maths is paid once per request. Decimation
    exists for a caller that has to fit many frames through a slow link; it drops whole chroma
    pairs (the 4:2:2 sample unit) and therefore costs resolution and sharpness, never CPU alone.
    """
    import numpy as np

    buf = yuy2 if isinstance(yuy2, np.ndarray) else np.frombuffer(yuy2, dtype=np.uint8)
    buf = buf.reshape(-1)
    if buf.size < height * step:
        raise ValueError("truncated yuyv buffer: %d bytes for %dx%d step %d"
                         % (buf.size, height, width, step))
    pairs = buf[: height * step].reshape(height, step)[:, : (width // 2) * 4]
    pairs = pairs.reshape(height, width // 2, 4)
    col_step = row_step = 1
    while max_width and width // col_step > max_width:
        col_step *= 2
        row_step *= 2
    pairs = pairs[::row_step, ::col_step]
    h, w2 = pairs.shape[0], pairs.shape[1]
    w = w2 * 2
    y = pairs[:, :, 0::2].reshape(h, w).astype(np.int32)
    u = np.repeat(pairs[:, :, 1:2], 2, axis=2).reshape(h, w).astype(np.int32) - 128
    v = np.repeat(pairs[:, :, 3:4], 2, axis=2).reshape(h, w).astype(np.int32) - 128
    c = y - 16
    r = (298 * c + 409 * v + 128) >> 8
    g = (298 * c - 100 * u - 208 * v + 128) >> 8
    b = (298 * c + 516 * u + 128) >> 8
    return np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)


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

if is_raw:
    # The decode runs on the robot. When its python lacks numpy or PIL the
    # subscription callback dies inside spin and the run looks exactly like
    # "nothing was published"; report the missing module instead.
    missing = []
    try:
        import numpy  # noqa: F401
    except Exception:
        missing.append("numpy")
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        missing.append("PIL/Pillow")
    if missing:
        print("@@ERR cannot decode raw %s on %s: %s missing on the robot. Raw Image is "
              "converted there, so install it or capture a CompressedImage topic"
              % (msg_type, topic, " and ".join(missing)))
        sys.exit(3)


@@ENCODER@@
@@DECODER@@
def handle_compressed(msg):
    box["data"] = bytes(msg.data)
    box["format"] = msg.format
    box["stamp"] = f"{msg.header.stamp.sec}.{msg.header.stamp.nanosec:09d}"


def handle_raw(msg):
    import numpy as np
    from PIL import Image

    h, w, step = msg.height, msg.width, msg.step
    channels = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "bgra8": 4, "mono8": 1}.get(msg.encoding)
    buf = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    if buf.size < h * step:
        box["err"] = "truncated payload: %d bytes for %dx%d step %d" % (buf.size, h, w, step)
        return
    if msg.encoding == "yuyv":
        img = Image.fromarray(yuyv_to_rgb(bytes(msg.data), h, w, step), mode="RGB")  # native size
    elif channels is None:
        box["err"] = ("unsupported encoding %s (rgb8, bgr8, rgba8, bgra8, mono8, yuyv)"
                      % msg.encoding)
        return
    else:
        rows = buf[: h * step].reshape(h, step)[:, : w * channels]
        arr = rows.reshape(h, w, channels)
        if channels == 1:
            img = Image.fromarray(arr[:, :, 0], mode="L")
        else:
            if msg.encoding in ("bgr8", "bgra8"):
                arr = arr[:, :, [2, 1, 0] + ([3] if channels == 4 else [])]
            img = Image.fromarray(arr[:, :, :3], mode="RGB")
    box["data"], box["quality"] = encode_jpeg(img)
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
print("@@QUALITY %s" % box.get("quality", "?"))
print("@@B64 " + base64.b64encode(box["data"]).decode("ascii"))
'''

SNAPSHOT_SCRIPT = (
    SNAPSHOT_BODY.replace("@@DECODER@@", inspect.getsource(yuyv_to_rgb))
    .replace("@@ENCODER@@", inspect.getsource(encode_jpeg).replace(
        "JPEG_B64_BUDGET", str(JPEG_B64_BUDGET), 1))
)


def snapshot(target: Target, topic: str, seconds: int = 8) -> tuple[dict, Result]:
    """Grab one camera frame. Handles `CompressedImage` and raw `Image` (decoded on the robot)."""
    topic = validate_topic(topic)
    seconds = max(2, min(20, int(seconds)))
    script = f"python3 - {shlex.quote(topic)} {seconds} <<'HERMES_SNAPSHOT'\n{SNAPSHOT_SCRIPT}\nHERMES_SNAPSHOT"
    # the script's own wait, plus room for ssh, the shell prelude and a frame decode on
    # a board that is also publishing; measured grabs run 5-6s but the budget must
    # survive a busy box rather than time out into "no frame"
    result = run(target, script, timeout=seconds + 30, label=f"snapshot {topic}")
    # A truncated capture has no usable tail: the base64 block is cut mid-stream,
    # so decoding it yields a corrupt image or a ValueError. Say "too large".
    ensure_complete(result, f"camera snapshot of {topic}")
    stdout = result.stdout or ""
    if "@@ERR" in stdout:
        message = stdout.split("@@ERR", 1)[1].strip().splitlines()[0]
        return {"topic": topic, "ok": False, "error": message,
                "hint": "check the topic exists on this graph and is publishing; a raw Image "
                        "is decoded on the robot and needs numpy and PIL there — if the error "
                        "names one, install it or capture a CompressedImage topic"}, result
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
        elif line.startswith("@@QUALITY "):
            quality = line[len("@@QUALITY "):].strip()
            meta["quality"] = int(quality) if quality.isdigit() else None
        elif line.startswith("@@SOURCE "):
            meta["source"] = line[len("@@SOURCE "):].strip()
    return {"topic": topic, "ok": True, "encoding": "base64", "data": payload,
            **meta}, result


# ----------------------------------------------------------------------- sample

SAMPLE_BODY = r'''
import sys, time
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile

topic, seconds = sys.argv[1], float(sys.argv[2])
rclpy.init()
node = Node("hermes_ros2_sample")
box = {}

types = []
settle = time.time() + 3.0
while time.time() < settle and not types:
    types = dict(node.get_topic_names_and_types()).get(topic, [])
    rclpy.spin_once(node, timeout_sec=0.15)
if not types:
    print("@@ERR topic %s not found on this graph" % topic); sys.exit(3)
msg_type = types[0]
try:
    from rosidl_runtime_py.utilities import get_message
    cls = get_message(msg_type)
except Exception as exc:
    print("@@ERR cannot load %s: %s" % (msg_type, exc)); sys.exit(3)

# Match the publisher's QoS. A RELIABLE default subscription silently receives
# nothing from a BEST_EFFORT publisher (camera drivers publish BEST_EFFORT).
qos = QoSProfile(depth=10)
try:
    infos = node.get_publishers_info_by_topic(topic)
    if infos:
        qos.reliability = infos[0].qos_profile.reliability
        qos.durability = infos[0].qos_profile.durability
except Exception:
    pass

def cb(msg):
    box["msg"] = msg

node.create_subscription(cls, topic, cb, qos)
deadline = time.time() + seconds
while time.time() < deadline and "msg" not in box:
    rclpy.spin_once(node, timeout_sec=0.1)

if "msg" not in box:
    print("@@NOMSG"); sys.exit(0)

msg = box["msg"]
stamp = ""
try:
    stamp = "%d.%09d" % (msg.header.stamp.sec, msg.header.stamp.nanosec)
except Exception:
    pass

frame = ""
try:
    frame = msg.header.frame_id
except Exception:
    pass

if msg_type.endswith("/Image") and hasattr(msg, "height"):
    body = "%dx%d encoding=%s step=%d frame=%s" % (msg.height, msg.width, msg.encoding, msg.step, frame)
elif msg_type.endswith("/CompressedImage"):
    body = "%s %d bytes frame=%s" % (getattr(msg, "format", "?"), len(getattr(msg, "data", b"") or b""), frame)
elif msg_type.endswith("/String"):
    # Hand back the payload itself: repr() would wrap it as
    # String(data='...') and make JSON unreadable and uncopyable.
    body = str(msg.data)
else:
    body = repr(msg)

print("@@TYPE %s" % msg_type)
print("@@STAMP %s" % stamp)
limit = 20000 if msg_type.endswith("/String") else 1200
print("@@MSG %s" % " ".join(body.split())[:limit])
'''


def sample_last(target: Target, topic: str, seconds: int = 3) -> tuple[dict, Result]:
    """One message from a topic, with the publisher's QoS so BEST_EFFORT topics work."""
    topic = validate_topic(topic)
    seconds = max(2, min(15, int(seconds)))
    script = f"python3 - {shlex.quote(topic)} {seconds} <<'HERMES_SAMPLE'\n{SAMPLE_BODY}\nHERMES_SAMPLE"
    result = run(target, script, timeout=seconds + 14, label=f"sample {topic}")
    stdout = result.stdout or ""
    if "@@ERR" in stdout:
        message = stdout.split("@@ERR", 1)[1].strip().splitlines()[0]
        return {"topic": topic, "ok": False, "error": message}, result
    if "@@NOMSG" in stdout:
        return {
            "topic": topic,
            "ok": True,
            "received": False,
            "message": "",
            "hint": "nothing arrived in the window — an event-driven or idle topic",
        }, result
    if "@@MSG " not in stdout:
        return {"topic": topic, "ok": False,
                "error": result.error() if not result.ok else "no message payload returned"}, result

    meta = {}
    for line in stdout.splitlines():
        for key, field in (("@@TYPE ", "type"), ("@@STAMP ", "stamp")):
            if line.startswith(key):
                meta[field] = line[len(key):].strip()
    body = stdout.split("@@MSG ", 1)[1].strip().splitlines()[0]
    return {"topic": topic, "ok": True, "received": True, "message": body, **meta}, result


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


# -------------------------------------------------------------------- publishing

# The write path. Everything that decides WHETHER it may run lives host-side in
# tools.publish_refusal(); this body only answers what the robot itself knows —
# the type, the existing publisher's QoS, and whether a listener saw the message.
PUBLISH_BODY = r'''
import sys, time, json
topic, want_type, payload, count, rate, wait_s = sys.argv[1:7]
count, rate, wait_s = int(count), float(rate), float(wait_s)

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy

def qos_of(info):
    q = info.qos_profile
    rel = getattr(q.reliability, "name", str(q.reliability))
    dur = getattr(q.durability, "name", str(q.durability))
    return ("RELIABLE" if rel == "RELIABLE" else "BEST_EFFORT",
            "TRANSIENT_LOCAL" if dur == "TRANSIENT_LOCAL" else "VOLATILE")

rclpy.init()
node = Node("_hermes_publish_probe")
try:
    end = time.time() + 3.0
    types = {}
    while time.time() < end and topic not in types:
        rclpy.spin_once(node, timeout_sec=0.2)
        types = dict(node.get_topic_names_and_types())
    if topic not in types:
        print("@@ERR topic is not in the live graph: %s" % topic); raise SystemExit(0)
    graph_types = types[topic]
    node_type = graph_types[0]
    if want_type and want_type != node_type:
        print("@@ERR type mismatch: the graph says %s, the call asked for %s" % (node_type, want_type))
        raise SystemExit(0)
    print("@@TYPE %s" % node_type)

    pub_info = list(node.get_publishers_info_by_topic(topic))
    sub_info = list(node.get_subscriptions_info_by_topic(topic))
    if pub_info:
        rel, dur = qos_of(pub_info[0])
        print("@@QOS source=existing-publisher reliability=%s durability=%s" % (rel, dur))
    elif sub_info:
        # No publisher to copy: match the CONSUMER instead. A RELIABLE publisher
        # against a BEST_EFFORT subscriber delivers nothing, and the reverse is
        # just as silent, so the listener's policy decides ours.
        rel, dur = qos_of(sub_info[0])
        print("@@QOS source=existing-subscriber reliability=%s durability=%s" % (rel, dur))
    else:
        rel, dur = "RELIABLE", "VOLATILE"
        print("@@QOS source=none-present reliability=%s durability=%s" % (rel, dur))

    qos = QoSProfile(depth=10,
                     reliability=getattr(ReliabilityPolicy, rel),
                     durability=getattr(DurabilityPolicy, dur),
                     history=HistoryPolicy.KEEP_LAST)

    pkg, _, cls_name = node_type.split("/")
    try:
        mod = __import__(pkg + ".msg", fromlist=[cls_name])
        cls = getattr(mod, cls_name)
    except Exception as exc:
        print("@@ERR cannot import %s: %s" % (node_type, exc)); raise SystemExit(0)
    if not hasattr(cls, "data"):
        print("@@ERR %s carries no simple payload field; this tool writes String-style topics" % node_type)
        raise SystemExit(0)

    seen = []
    def on_msg(msg):
        seen.append(str(getattr(msg, "data", "")))

    node.create_subscription(cls, topic, on_msg, qos)
    pub = node.create_publisher(cls, topic, qos)

    msg = cls()
    msg.data = payload
    sent = 0
    for i in range(count):
        pub.publish(msg)
        sent += 1
        if i + 1 < count:
            end = time.time() + (1.0 / rate)
            while time.time() < end:
                rclpy.spin_once(node, timeout_sec=0.05)
    print("@@SENT %d" % sent)

    deadline = time.time() + wait_s
    while time.time() < deadline and not seen:
        rclpy.spin_once(node, timeout_sec=0.1)
    print("@@RECEIVED %d" % (1 if seen else 0))
    if seen:
        print("@@ECHO %s" % " ".join(seen[0].split())[:400])
finally:
    node.destroy_node()
    rclpy.shutdown()
'''


def publish(target: Target, topic: str, payload: str, *, want_type: str = "",
            count: int = 1, rate_hz: float = 1.0, wait_s: float = 3.0) -> tuple[dict, Result]:
    """Publish to a topic and prove delivery with a listener on the same QoS.

    This function does not decide whether the write is allowed — `tools.publish_refusal`
    does, before this runs. Everything here answers only what the robot knows.
    """
    script = (
        f"python3 - {shlex.quote(topic)} {shlex.quote(want_type or '')} {shlex.quote(payload)} "
        f"{int(count)} {float(rate_hz)} {float(wait_s)} <<'HERMES_PUBLISH'\n{PUBLISH_BODY}\nHERMES_PUBLISH"
    )
    result = run(target, script, timeout=int(wait_s) + 20, label=f"publish {topic}")
    stdout = result.stdout or ""
    if "@@ERR" in stdout:
        message = stdout.split("@@ERR", 1)[1].strip().splitlines()[0]
        return {"topic": topic, "ok": False, "published": False, "error": message}, result
    if "@@RECEIVED" not in stdout:
        return {
            "topic": topic,
            "ok": False,
            "published": False,
            "error": (result.error() if not result.ok else "no delivery report returned"),
        }, result

    meta: dict = {"topic": topic, "payload": payload}
    for line in stdout.splitlines():
        if line.startswith("@@TYPE "):
            meta["type"] = line[len("@@TYPE "):].strip()
        elif line.startswith("@@QOS "):
            meta["qos"] = line[len("@@QOS "):].strip()
        elif line.startswith("@@SENT "):
            meta["sent"] = int(line[len("@@SENT "):].strip() or 0)
        elif line.startswith("@@ECHO "):
            meta["echoed"] = line[len("@@ECHO "):].strip()
    meta["received"] = stdout.split("@@RECEIVED ", 1)[1].strip().splitlines()[0] == "1"
    # A publish that no listener saw is a FAILED publish, however clean the send was.
    meta["ok"] = bool(meta["received"])
    meta["published"] = bool(meta["received"])
    if not meta["received"]:
        meta["hint"] = (
            "the message was sent but nothing received it within the window: check the topic's "
            "subscribers, or that the QoS matches theirs"
        )
    return meta, result
