"""The write path: every guard, and the difference between sent and received."""

from __future__ import annotations

import json

import pytest

from ros2_console import graph as obs
from ros2_console import tools
from ros2_console.transport import Result, Target


TARGET = Target(host="", distro="jazzy", setup="", timeout=15)


def refusal(allow, **args):
    args.setdefault("topic", "/demo/led/frame")
    args.setdefault("payload", "00044000")
    return tools.publish_refusal(allow, args)


# --------------------------------------------------------------------- the guards


def test_an_empty_allowlist_refuses_and_names_the_setting():
    """A fresh install can publish nothing, and the message says why."""
    why = refusal([], topic="/demo/led/frame")
    assert why and "publish_allow is empty" in why


def test_a_topic_outside_the_allowlist_is_refused_by_name():
    why = refusal(["/demo/*"], topic="/objectzero/led/frame")
    assert why and "/objectzero/led/frame" in why and "/demo/*" in why


def test_an_allowlisted_topic_is_allowed():
    assert refusal(["/demo/*"], topic="/demo/led/frame") is None


def test_actuation_is_refused_even_when_the_allowlist_would_permit_it():
    """The allowlist chooses WHICH topics are writable, never whether a servo is."""
    for topic in ("/cmd_vel", "/arm/joint_trajectory_cmd", "/servo/left", "/gripper/close"):
        why = refusal(["*"], topic=topic)
        assert why and "actuation pattern" in why, topic


def test_count_rate_and_payload_are_bounded():
    assert "count must be" in (refusal(["/demo/*"], count=0) or "")
    assert "count must be" in (refusal(["/demo/*"], count=11) or "")
    assert "rate_hz must be" in (refusal(["/demo/*"], rate_hz=20) or "")
    assert "rate_hz must be" in (refusal(["/demo/*"], rate_hz=0.1) or "")
    assert "8192 bytes" in (refusal(["/demo/*"], payload="a" * 8193) or "")


def test_a_payload_with_a_nul_byte_is_refused():
    assert "NUL" in (refusal(["/demo/*"], payload="a\x00b") or "")


def test_an_invalid_topic_is_refused_before_any_remote_call():
    why = tools.publish_refusal(["/demo/*"], {"topic": "not a topic; rm -rf /", "payload": "x"})
    assert why and "invalid ROS topic name" in why


# ---------------------------------------------------------------- delivery proof


def _result(stdout: str, ok: bool = True, exit_code: int = 0) -> Result:
    return Result(ok=ok, stdout=stdout, stderr="", exit_code=exit_code, meta={})


def test_a_received_message_is_the_only_success(monkeypatch):
    body = (
        "@@TYPE std_msgs/msg/String\n"
        "@@QOS source=existing-publisher reliability=RELIABLE durability=VOLATILE\n"
        "@@SENT 1\n@@RECEIVED 1\n@@ECHO 00044000\n"
    )
    monkeypatch.setattr(obs, "run", lambda *a, **k: _result(body))
    payload, _ = obs.publish(TARGET, "/demo/led/frame", "00044000")
    assert payload["ok"] is True
    assert payload["received"] is True
    assert payload["published"] is True
    assert payload["echoed"] == "00044000"
    assert payload["type"] == "std_msgs/msg/String"


def test_sent_but_unheard_is_a_failure_not_a_success(monkeypatch):
    """The failure this whole tool exists to avoid: an ack with no witness."""
    body = "@@TYPE std_msgs/msg/String\n@@SENT 1\n@@RECEIVED 0\n"
    monkeypatch.setattr(obs, "run", lambda *a, **k: _result(body))
    payload, _ = obs.publish(TARGET, "/demo/led/frame", "00044000")
    assert payload["ok"] is False
    assert payload["received"] is False
    assert payload["published"] is False
    assert "nothing received it" in payload["hint"]


def test_a_remote_refusal_is_reported_verbatim(monkeypatch):
    body = "@@ERR topic is not in the live graph: /demo/led/frame\n"
    monkeypatch.setattr(obs, "run", lambda *a, **k: _result(body))
    payload, _ = obs.publish(TARGET, "/demo/led/frame", "x")
    assert payload["ok"] is False
    assert "not in the live graph" in payload["error"]


def test_the_tool_handler_refuses_before_touching_the_robot(monkeypatch):
    called = {"run": 0}

    def boom(*a, **k):
        called["run"] += 1
        raise AssertionError("no remote call may happen when the write is refused")

    monkeypatch.setattr(obs, "run", boom)
    monkeypatch.setattr(tools, "settings_resolve", lambda _o: {"publish_allow": []})
    out = json.loads(tools._publish({"topic": "/demo/led/frame", "payload": "x"}))
    assert out["ok"] is False and out["refused"] is True
    assert called["run"] == 0


def test_the_tool_is_registered_with_a_write_warning():
    tool = next(t for t in tools.TOOLS if t.name == "ros2_publish")
    text = tool.schema["description"].lower()
    assert "disabled by default" in text
    assert "prove" in text or "proof" in text
    assert "actuation" in text
