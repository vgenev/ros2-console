"""Tool handlers stay JSON on failure, and the graph cap matches its edges."""

from __future__ import annotations

import json

from ros2_console.graph import GRAPH_INTROSPECT, GRAPH_SCRIPT, parse_graph
from ros2_console.transport import Result


def _result(stdout: str = "", *, ok: bool = True, exit_code: int = 0, stderr: str = "") -> Result:
    return Result(ok=ok, stdout=stdout, stderr=stderr, exit_code=exit_code, target="robot")


def _graph_text(count: int) -> str:
    topics = []
    detail = []
    for index in range(count):
        name = f"/demo/topic_{index:03d}"
        topics.append(f"{name} [std_msgs/msg/String]")
        detail.append(f"@@T|{name}|1|1|std_msgs/msg/String")
        detail.append(f"@@E|{name}|P|/talker|std_msgs/msg/String")
    return (
        "@@DISTRO jazzy\n"
        "@@RMW default\n"
        "@@NODES\n"
        "/talker\n"
        "@@TOPICS\n"
        + "\n".join(topics)
        + "\n@@DETAIL\n"
        + "\n".join(detail)
        + "\n@@DISCOVERY OK\n"
        "@@END\n"
    )


def test_topic_sample_keeps_the_remote_error_for_a_missing_topic(monkeypatch):
    from ros2_console import graph as g
    from ros2_console.tools import _topic_sample

    monkeypatch.setattr(
        g, "run",
        lambda *a, **k: _result("@@ERR topic /nope does not exist\n"),
    )
    payload = json.loads(_topic_sample({"topic": "/nope", "host": "robot"}))
    assert payload["ok"] is False
    assert payload["error"] == "topic /nope does not exist"


def test_topic_sample_reports_an_unreachable_host(monkeypatch):
    from ros2_console import graph as g
    from ros2_console.tools import _topic_sample

    ssh = "ssh: connect to host 10.0.0.9 port 22: No route to host"
    monkeypatch.setattr(
        g, "run",
        lambda *a, **k: _result("", ok=False, exit_code=255, stderr=ssh),
    )
    payload = json.loads(_topic_sample({"topic": "/chatter", "host": "robot"}))
    assert payload["ok"] is False
    assert ssh in payload["error"]


def test_topic_sample_without_a_payload_marker_is_not_a_crash(monkeypatch):
    from ros2_console import graph as g
    from ros2_console.tools import _topic_sample

    monkeypatch.setattr(g, "run", lambda *a, **k: _result("ros2 said hello\n", ok=True))
    payload = json.loads(_topic_sample({"topic": "/chatter", "host": "robot"}))
    assert payload["ok"] is False
    assert "no message payload" in payload["error"]


def test_graph_over_the_cap_drops_edges_for_hidden_topics(monkeypatch):
    from ros2_console import graph as g
    from ros2_console.tools import _graph

    monkeypatch.setattr(g, "run", lambda *a, **k: _result(_graph_text(100)))
    payload = json.loads(_graph({"host": "robot"}))
    shown = {topic["name"] for topic in payload["topics"]}
    assert payload["counts"]["topics"] == 100
    assert payload["counts"]["topics_returned"] == 80
    assert payload["counts"]["topics_truncated"] == 20
    assert shown
    assert {edge["topic"] for edge in payload["edges"]} <= shown


def test_graph_at_the_cap_is_not_truncated(monkeypatch):
    from ros2_console import graph as g
    from ros2_console.tools import _graph

    monkeypatch.setattr(g, "run", lambda *a, **k: _result(_graph_text(80)))
    payload = json.loads(_graph({"host": "robot"}))
    assert payload["counts"]["topics_truncated"] == 0
    assert payload["counts"]["topics_returned"] == 80
    assert payload["counts"]["topics"] == 80


def test_unmeasured_marker_reaches_health_text():
    reason = (
        "message type sensor_msgs/msg/Image can carry more than the "
        "1000000-byte sampling budget, so its rate was not measured"
    )
    text = (
        "@@DISTRO jazzy\n"
        "@@RMW default\n"
        "@@NODES\n"
        "/cam\n"
        "@@TOPICS\n"
        "/cam/image [sensor_msgs/msg/Image]\n"
        "@@DETAIL\n"
        "@@T|/cam/image|1|1|sensor_msgs/msg/Image\n"
        f"@@U|/cam/image|{reason}\n"
        "@@DISCOVERY OK\n"
        "@@END\n"
    )
    parsed = parse_graph(text)
    entry = parsed["topics"][0]
    assert entry["health"] == "unmeasured"
    assert "rate_hz" not in entry
    assert reason in entry["health_text"]
    assert entry["health_text"].startswith("rate not measured — ")
    assert parsed["counts"]["rate_skipped"] == 1


def test_traffic_skip_marker_survives_the_shell_wrapper():
    assert "TRAFFIC_BYTE_BUDGET" in GRAPH_INTROSPECT
    assert "@@U|" in GRAPH_INTROSPECT
    assert "@@D|@@R|@@U" in GRAPH_SCRIPT
    # Destroying a subscription from inside its callback can hang the executor.
    assert "destroy_subscription" not in GRAPH_INTROSPECT
