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


def _envelope(detail: str, topic_line: str) -> str:
    return (
        "@@DISTRO jazzy\n"
        "@@RMW default\n"
        "@@NODES\n"
        "/talker\n"
        "@@TOPICS\n"
        f"{topic_line}\n"
        "@@DETAIL\n"
        f"{detail}\n"
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
    ("pubs", "subs", "expected", "rejected"),
    [
        ("0", "1", "dead_input", "dead_output"),
        ("1", "0", "dead_output", "dead_input"),
        ("2", "0", "dead_output", "dead_input"),
        ("0", "3", "dead_input", "dead_output"),
        ("?", "1", "unmeasured", "dead_input"),
        ("1", "?", "unmeasured", "dead_output"),
        ("1", "1", "live", "dead_output"),
    ],
)
def test_inverse_health_is_rejected(pubs, subs, expected, rejected):
    text = _envelope(
        f"@@T|/probe|{pubs}|{subs}|std_msgs/msg/String",
        "/probe [std_msgs/msg/String]",
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
