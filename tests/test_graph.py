"""Health flags and topic counts from captured ros2 CLI text."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ros2_console.graph import parse_graph, parse_rate

FIXTURES = Path(__file__).parent / "fixtures"
DEAD = FIXTURES / "graph_dead_input.txt"
HEALTHY = FIXTURES / "graph_healthy.txt"

CAPTURED_HEALTH = {
    "/board/temperature": "dead_output",
    "/camera/image_raw/compressed": "live",
    "/chatter": "live",
    "/depth/points/compressed": "dead_input",
    "/inspection/verdict": "dead_output",
    "/parameter_events": "dead_output",
    "/rosout": "dead_output",
}


def _envelope(detail: str, topic_line: str, rate: str | None = None) -> str:
    rate_line = f"@@R|/probe|{rate}\n" if rate else ""
    return (
        "@@DISTRO jazzy\n"
        "@@RMW default\n"
        "@@NODES\n"
        "/talker\n"
        "@@TOPICS\n"
        f"{topic_line}\n"
        "@@DETAIL\n"
        f"{detail}\n"
        f"{rate_line}"
        "@@DISCOVERY OK\n"
        "@@END\n"
    )


def test_captured_graph_counts_and_health():
    text = DEAD.read_text()
    parsed = parse_graph(text)
    by_name = {topic["name"]: topic for topic in parsed["topics"]}
    assert set(parsed["nodes"]) == {
        "/board_telemetry",
        "/depth_filter",
        "/inspection_camera",
        "/inspection_verdict",
        "/listener",
        "/talker",
    }
    for line in text.splitlines():
        if not line.startswith("@@T|"):
            continue
        _marker, name, pubs, subs, typ = line.split("|", 4)
        entry = by_name[name]
        assert entry["publishers"] == int(pubs)
        assert entry["subscribers"] == int(subs)
        assert entry["type"] == typ
        assert isinstance(entry["publishers"], int)
        assert isinstance(entry["subscribers"], int)
    for name, health in CAPTURED_HEALTH.items():
        assert by_name[name]["health"] == health
    dead = {
        name for name, health in CAPTURED_HEALTH.items()
        if health in ("dead_input", "dead_output")
    }
    system = {"/parameter_events", "/rosout"}
    orphan_names = {item["name"] for item in parsed["orphans"]}
    assert orphan_names == dead - system
    assert parsed["counts"]["orphans"] == len(orphan_names)
    assert parsed["counts"]["system_excluded"] == len(dead & system)
    assert by_name["/rosout"]["subscribers"] == 0
    assert "/rosout" not in orphan_names
    shown = parse_graph(text, include_system=True)
    assert {item["name"] for item in shown["orphans"]} == dead
    assert shown["counts"]["orphans"] == len(dead)
    assert shown["counts"]["system_excluded"] == 0
    depth = by_name["/depth/points/compressed"]
    assert (depth["publishers"], depth["subscribers"], depth["health"]) == (0, 1, "dead_input")
    board = by_name["/board/temperature"]
    assert (board["publishers"], board["subscribers"], board["health"]) == (1, 0, "dead_output")


def test_topic_info_text_supplies_the_counts():
    text = HEALTHY.read_text()
    block = text.split("@@DISTRO", 1)[0]
    pubs = subprocess.check_output(
        ["sed", "-n", r"s/^Publisher count: *//p"], input=block, text=True
    ).strip()
    subs = subprocess.check_output(
        ["sed", "-n", r"s/^Subscription count: *//p"], input=block, text=True
    ).strip()
    parsed = parse_graph(text)
    chatter = next(topic for topic in parsed["topics"] if topic["name"] == "/chatter")
    assert chatter["publishers"] == int(pubs) == 1
    assert chatter["subscribers"] == int(subs) == 1
    assert chatter["health"] == "live"


def test_rosout_with_no_subscribers_is_not_an_orphan_unless_requested():
    text = _envelope(
        "@@T|/rosout|1|0|rcl_interfaces/msg/Log",
        "/rosout [rcl_interfaces/msg/Log]",
    )
    hidden = parse_graph(text)
    rosout = hidden["topics"][0]
    assert rosout["name"] == "/rosout"
    assert rosout["subscribers"] == 0
    assert rosout["health"] == "dead_output"
    assert hidden["orphans"] == []
    assert hidden["counts"]["orphans"] == 0
    assert hidden["counts"]["system_excluded"] == 1
    shown = parse_graph(text, include_system=True)
    assert [item["name"] for item in shown["orphans"]] == ["/rosout"]
    assert shown["counts"]["orphans"] == 1
    assert shown["counts"]["system_excluded"] == 0


def test_unpublished_warning_is_silent_with_a_missing_publisher_hint():
    parsed = parse_rate(
        "WARNING: topic [/depth/points/compressed] does not appear to be published yet\n"
    )
    assert parsed["hz"] == 0.0
    assert parsed["silent"] is True
    assert "missing publisher" in parsed["hint"]


def test_unmeasured_topic_is_not_an_orphan():
    parsed = parse_graph(HEALTHY.read_text())
    spare = next(topic for topic in parsed["topics"] if topic["name"] == "/spare/status")
    assert spare["publishers"] is None
    assert spare["subscribers"] is None
    assert spare["health"] == "unmeasured"
    assert all(item["name"] != "/spare/status" for item in parsed["orphans"])
    assert parsed["counts"]["orphans"] == 0


@pytest.mark.parametrize(
    ("pubs", "subs", "rate", "expected", "rejected"),
    [
        ("0", "1", None, "dead_input", "dead_output"),
        ("1", "0", None, "dead_output", "dead_input"),
        ("2", "0", None, "dead_output", "dead_input"),
        ("0", "3", None, "dead_input", "dead_output"),
        ("?", "1", None, "unmeasured", "dead_input"),
        ("1", "?", None, "unmeasured", "dead_output"),
        # No endpoints: nothing can have been measured, so never "live".
        ("0", "0", None, "unmeasured", "live"),
        ("0", "0", "12.500", "unmeasured", "live"),
        # Endpoints without a rate sample (daemon fallback) is not traffic.
        ("1", "1", None, "unmeasured", "live"),
        # A measured positive rate is what makes a topic live.
        ("1", "1", "9.500", "live", "dead_output"),
        # Connected, measured, nothing arriving: silent, not live.
        ("1", "1", "0.000", "silent", "live"),
    ],
)
def test_inverse_health_is_rejected(pubs, subs, rate, expected, rejected):
    text = _envelope(
        f"@@T|/probe|{pubs}|{subs}|std_msgs/msg/String",
        "/probe [std_msgs/msg/String]",
        rate=rate,
    )
    parsed = parse_graph(text)
    entry = parsed["topics"][0]
    assert entry["health"] == expected
    assert entry["health"] != rejected
    orphan_names = [item["name"] for item in parsed["orphans"]]
    if expected in ("dead_input", "dead_output"):
        assert orphan_names == ["/probe"]
    else:
        assert orphan_names == []


EDGE_TEXT = """@@DISTRO jazzy
@@RMW
@@NODES
/talker
/listener
@@TOPICS
/chatter [std_msgs/msg/String]
/quiet [std_msgs/msg/String]
@@DETAIL
@@T|/chatter|1|1|std_msgs/msg/String
@@E|/chatter|P|/talker|std_msgs/msg/String
@@E|/chatter|S|/listener|std_msgs/msg/String
@@T|/quiet|1|0|std_msgs/msg/String
@@E|/quiet|P|/talker|std_msgs/msg/String
@@DISCOVERY OK
@@END
"""


def test_edges_carry_the_node_that_owns_each_endpoint():
    """A list of topics does not say who publishes them; the topology does."""
    parsed = parse_graph(EDGE_TEXT)
    edges = parsed["edges"]
    assert {
        "topic": "/chatter",
        "role": "publisher",
        "node": "/talker",
    } in edges
    assert {
        "topic": "/chatter",
        "role": "subscriber",
        "node": "/listener",
    } in edges
    assert len([e for e in edges if e["topic"] == "/quiet"]) == 1


def test_unnamed_participants_are_not_rendered_as_raw_unknown_tokens():
    text = EDGE_TEXT.replace(
        "@@E|/quiet|P|/talker|std_msgs/msg/String",
        "@@E|/quiet|P|<unnamed participant>|std_msgs/msg/String",
    )
    parsed = parse_graph(text)
    assert any(e["node"] == "<unnamed participant>" for e in parsed["edges"])


TRAFFIC_TEXT = """@@DISTRO jazzy
@@RMW
@@NODES
/cam
@@TOPICS
/cam/image [sensor_msgs/msg/Image]
/events [std_msgs/msg/String]
@@DETAIL
@@T|/cam/image|1|1|sensor_msgs/msg/Image
@@R|/cam/image|30.500
@@E|/cam/image|P|/cam|sensor_msgs/msg/Image
@@E|/cam/image|S|/viewer|sensor_msgs/msg/Image
@@T|/events|1|1|std_msgs/msg/String
@@R|/events|0.000
@@E|/events|P|/cam|std_msgs/msg/String
@@E|/events|S|/logger|std_msgs/msg/String
@@DISCOVERY OK
@@END
"""


def test_measured_traffic_reaches_the_payload():
    parsed = parse_graph(TRAFFIC_TEXT)
    by_name = {t["name"]: t for t in parsed["topics"]}
    assert by_name["/cam/image"]["rate_hz"] == 30.5
    assert by_name["/cam/image"]["health"] == "live"


def test_connected_but_silent_is_its_own_state():
    """Wiring can be perfect while nothing flows; that is a different finding."""
    parsed = parse_graph(TRAFFIC_TEXT)
    by_name = {t["name"]: t for t in parsed["topics"]}
    assert by_name["/events"]["health"] == "silent"
    assert parsed["counts"]["orphans"] == 0


# --------------------------------------------------- P0-3/P0-4: measured claims


def test_zero_publishers_and_zero_subscribers_is_never_live():
    """Nothing is connected, so no traffic sample exists: not evidence of flow."""
    text = _envelope("@@T|/idle|0|0|std_msgs/msg/String", "/idle [std_msgs/msg/String]",
                     rate="12.500")
    entry = parse_graph(text)["topics"][0]
    assert (entry["publishers"], entry["subscribers"]) == (0, 0)
    assert entry["health"] == "unmeasured"
    assert "not live" in entry["health_text"]


def test_a_publisher_without_a_rate_sample_is_unmeasured():
    """The daemon fallback reports no @@R line; wiring alone is not traffic."""
    text = _envelope("@@T|/probe|1|1|std_msgs/msg/String", "/probe [std_msgs/msg/String]")
    entry = parse_graph(text)["topics"][0]
    assert entry["health"] == "unmeasured"
    assert "rate_hz" not in entry
    assert "rate was not measured" in entry["health_text"]


def test_graph_script_announces_discovery_and_checks_ros_is_installed():
    from ros2_console.graph import GRAPH_SCRIPT

    assert "@@DISCOVERY OK" in GRAPH_SCRIPT
    assert "command -v ros2" in GRAPH_SCRIPT


def test_parse_graph_records_whether_ros_answered():
    ok = parse_graph(_envelope("@@T|/probe|1|1|std_msgs/msg/String",
                               "/probe [std_msgs/msg/String]", rate="1.000"))
    assert (ok["ros_answered"], ok["discovery_error"]) == (True, None)

    bare = parse_graph("@@DISTRO unknown\n@@RMW default\n@@NODES\n@@TOPICS\n@@DETAIL\n@@END\n")
    assert bare["ros_answered"] is False
    assert "ROS environment is not sourced" in bare["discovery_error"]

    failed = parse_graph(
        "@@DISTRO unknown\n@@RMW default\n@@NODES\n@@TOPICS\n"
        "@@DISCOVERY FAIL ros2 is not on PATH on this host\n@@END\n"
    )
    assert failed["ros_answered"] is False
    assert "ros2 is not on PATH" in failed["discovery_error"]


def _payload(stdout: str, ok: bool = True, exit_code: int = 0):
    from ros2_console.transport import Result

    return Result(ok=ok, stdout=stdout, exit_code=exit_code, target="local")


BARE_RUN = "@@DISTRO unknown\n@@RMW default\n@@NODES\n@@TOPICS\n@@DETAIL\n@@END\n"


def test_graph_call_refuses_a_run_without_a_discovery_marker(monkeypatch):
    """A shell that ran is not a ROS 2 that answered — never an empty healthy graph."""
    from ros2_console import graph as g
    from ros2_console.transport import Target

    monkeypatch.setattr(g, "run", lambda *a, **k: _payload(BARE_RUN))
    data, result = g.graph(Target())
    assert data is None
    assert "ROS environment is not sourced" in result.meta["discovery_error"]


def test_graph_tool_reports_ok_false_when_ros_never_answered(monkeypatch):
    import json

    from ros2_console import graph as g
    from ros2_console.tools import _graph

    monkeypatch.setattr(g, "run", lambda *a, **k: _payload(BARE_RUN))
    payload = json.loads(_graph({"host": "robot"}))
    assert payload["ok"] is False
    assert payload["error_kind"] == "ros_not_answered"
    assert "ROS environment is not sourced" in payload["error"]
    assert "topics" not in payload


def test_graph_tool_is_ok_only_when_ros_answered(monkeypatch):
    import json

    from ros2_console import graph as g
    from ros2_console.tools import _graph

    stdout = _envelope("@@T|/probe|1|1|std_msgs/msg/String",
                       "/probe [std_msgs/msg/String]", rate="9.500")
    monkeypatch.setattr(g, "run", lambda *a, **k: _payload(stdout))
    payload = json.loads(_graph({"host": "robot"}))
    assert payload["ok"] is True
    assert payload["ros_answered"] is True
    assert payload["topics"][0]["health"] == "live"


def test_raw_image_without_a_decoder_says_so_instead_of_no_image(monkeypatch):
    """numpy/PIL missing on the robot is a decoder problem, not a silent camera."""
    from ros2_console import graph as g
    from ros2_console.transport import Target

    stdout = ("@@ERR cannot decode raw sensor_msgs/msg/Image on /cam/image_raw: numpy "
              "missing on the robot. Raw Image is converted there, so install it or "
              "capture a CompressedImage topic\n")
    monkeypatch.setattr(g, "run", lambda *a, **k: _payload(stdout, ok=False, exit_code=3))
    data, _ = g.snapshot(Target(), "/cam/image_raw")
    assert data["ok"] is False
    assert "numpy" in data["error"]
    assert "missing on the robot" in data["error"]
    assert "no Image within" not in data["error"]


def test_raw_image_body_checks_the_decoder_before_subscribing():
    from ros2_console.graph import SNAPSHOT_BODY

    assert "cannot decode raw" in SNAPSHOT_BODY
    assert 'missing.append("numpy")' in SNAPSHOT_BODY


def test_yuyv_decodes_to_rgb_and_the_script_carries_it():
    import inspect

    import numpy as np
    import pytest

    cv2 = pytest.importorskip("cv2")
    from ros2_console.graph import SNAPSHOT_SCRIPT, yuyv_to_rgb as f

    h, w = 6, 8
    y = np.rint(np.tile(np.linspace(60, 220, w), (h, 1))).astype(np.uint8)
    pairs = np.empty((h, w // 2, 4), dtype=np.uint8)
    pairs[:, :, 0::2] = y.reshape(h, w // 2, 2)
    pairs[:, :, 1] = 128
    pairs[:, :, 3] = 128
    pairs[:3, :2, 1] = 40
    pairs[:3, :2, 3] = 220
    raw = pairs.reshape(-1).tobytes()
    ref = cv2.cvtColor(np.frombuffer(raw, np.uint8).reshape(h, w, 2), cv2.COLOR_YUV2RGB_YUY2)
    out = f(raw, h, w, 2 * w)
    assert out.shape == (h, w, 3)
    assert out.dtype == np.uint8
    assert np.abs(out.astype(int) - ref.astype(int)).max() <= 2

    h, w, step = 480, 640, 1280
    y = np.rint(np.tile(np.linspace(60, 220, w), (h, 1))).astype(np.uint8)
    pair_index = np.arange(w // 2)
    pairs = np.empty((h, w // 2, 4), dtype=np.uint8)
    pairs[:, :, 0::2] = y.reshape(h, w // 2, 2)
    pairs[:, :, 1] = np.where(pair_index % 2 == 0, 60, 200)
    pairs[:, :, 3] = np.where(pair_index % 2 == 0, 220, 60)
    raw = pairs.reshape(-1).tobytes()
    out = f(raw, h, w, step, max_width=480)
    assert out.shape == (240, 320, 3)
    assert out.shape[1] <= 480
    decimated = pairs[::2, ::2].reshape(out.shape[0], out.shape[1], 2)
    ref = cv2.cvtColor(decimated, cv2.COLOR_YUV2RGB_YUY2)
    assert np.abs(out.astype(int) - ref.astype(int)).max() <= 2
    full = cv2.cvtColor(np.frombuffer(raw, np.uint8).reshape(h, w, 2), cv2.COLOR_YUV2RGB_YUY2)
    # This arm fails if decimation moves to after the conversion.
    assert np.abs(out.astype(int) - full[::2, ::2].astype(int)).max() > 2

    assert inspect.getsource(f).strip() in SNAPSHOT_SCRIPT
    assert "@@DECODER@@" not in SNAPSHOT_SCRIPT
    assert ("unsupported encoding %s (rgb8, bgr8, rgba8, bgra8, mono8, yuyv)"
            in SNAPSHOT_SCRIPT)


def test_the_documented_encoding_list_names_yuyv():
    from pathlib import Path

    import ros2_console.tools
    from ros2_console.graph import SNAPSHOT_SCRIPT

    listed = ", ".join(("rgb8", "bgr8", "rgba8", "bgra8", "mono8", "yuyv"))
    skill = Path(__file__).parents[1] / "skills/ros2-camera-and-images/SKILL.md"
    assert listed in SNAPSHOT_SCRIPT
    assert listed in Path(ros2_console.tools.__file__).read_text()
    assert listed in skill.read_text()
