"""Pane routes, with the transport stubbed when the target cannot be reached."""

from __future__ import annotations

import importlib.util

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ros2_console.api import router
from ros2_console.transport import Result

PREFIX = "/api/plugins/ros2-console"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix=PREFIX)
    return TestClient(app)


def test_settings_default_to_the_manifest(client, monkeypatch):
    monkeypatch.setattr("ros2_console.api.stored", lambda: {})
    body = client.get(f"{PREFIX}/settings").json()
    assert body == {
        "host": "",
        "distro": "jazzy",
        "setup": "",
        "timeout": 15,
        "publish_allow": [],
        "source": "default",
    }


def test_post_settings_writes_and_returns_saved(client, monkeypatch):
    written = {}

    def save(values):
        written.update(values)

    monkeypatch.setattr("ros2_console.api.write_settings", save)
    monkeypatch.setattr("ros2_console.api.stored", lambda: {})
    payload = {"host": "ros2demo", "distro": "jazzy", "setup": "", "timeout": 20}
    response = client.post(f"{PREFIX}/settings", json=payload)
    assert response.status_code == 200
    assert response.json() == {**payload, "publish_allow": [], "source": "saved"}
    assert written == payload


def test_post_settings_rejects_a_bad_type(client, monkeypatch):
    called = {"n": 0}

    def save(values):
        called["n"] += 1

    monkeypatch.setattr("ros2_console.api.write_settings", save)
    response = client.post(
        f"{PREFIX}/settings",
        json={"host": "ros2demo", "distro": "jazzy", "setup": "", "timeout": "slow"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["key"] == "timeout"
    assert called["n"] == 0


def test_graph_reports_the_transport_error(client, monkeypatch):
    def fail(target, script, timeout=None, label=""):
        return Result(ok=False, stderr="ssh: Connection refused", exit_code=255, target=target.label)

    monkeypatch.setattr("ros2_console.graph.run", fail)
    response = client.get(f"{PREFIX}/graph", params={"host": "ros2demo"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "Connection refused" in body["error"]


def test_graph_includes_orphans(client, monkeypatch):
    def fake_graph(target, include_system=False):
        return {
            "ros_distro": "jazzy",
            "nodes": ["/talker"],
            "topics": [],
            "counts": {"nodes": 1, "topics": 1, "orphans": 1},
            "orphans": [{"name": "/board/temperature", "health": "dead_output"}],
        }, Result(ok=True, seconds=0.1, target=target.label)

    monkeypatch.setattr("ros2_console.graph.graph", fake_graph)
    body = client.get(f"{PREFIX}/graph", params={"host": "ros2demo"}).json()
    assert body["ok"] is True
    assert body["target"] == "ros2demo"
    assert body["orphans"][0]["name"] == "/board/temperature"
    assert body["counts"]["orphans"] == 1


def test_camera_failure_is_http_200(client, monkeypatch):
    def fail(target, script, timeout=None, label=""):
        return Result(ok=False, stderr="timed out", exit_code=255, target=target.label)

    monkeypatch.setattr("ros2_console.graph.run", fail)
    response = client.get(f"{PREFIX}/camera", params={"topic": "/camera/image_raw/compressed"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]
    assert "data_url" not in body


def test_camera_success_is_a_data_url(client, monkeypatch):
    def fake_snapshot(target, topic, seconds=8):
        return {
            "topic": topic,
            "ok": True,
            "data": "aGVsbG8=",
            "format": "jpeg",
            "bytes": 5,
            "stamp": "1.0",
        }, Result(ok=True)

    monkeypatch.setattr("ros2_console.graph.snapshot", fake_snapshot)
    body = client.get(f"{PREFIX}/camera", params={"topic": "/camera/image_raw/compressed"}).json()
    assert body["ok"] is True
    assert body["format"] == "jpeg"
    assert body["bytes"] == 5
    assert body["data_url"] == "data:image/jpeg;base64,aGVsbG8="


def test_camera_accepts_a_host_override(client, monkeypatch):
    """A regression that shipped once: the route ignored `host`, so the pane
    silently fell back to local mode and every camera call failed on a real robot."""
    seen = {}

    def fake_snapshot(target, topic, seconds=8):
        seen["host"] = target.host
        seen["mode"] = target.mode
        return {"topic": topic, "ok": True, "data": "aGVsbG8=", "format": "jpeg",
                "bytes": 5, "stamp": "1.0"}, Result(ok=True)

    monkeypatch.setattr("ros2_console.graph.snapshot", fake_snapshot)
    body = client.get(
        f"{PREFIX}/camera",
        params={"topic": "/camera/image_raw/compressed", "host": "robot@10.0.0.5"},
    ).json()
    assert body["ok"] is True
    assert seen == {"host": "robot@10.0.0.5", "mode": "ssh"}


def test_plugin_api_module_exports_the_router():
    spec = importlib.util.spec_from_file_location(
        "ros2_console_plugin_api_under_test", "dashboard/plugin_api.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.router is router


def test_sample_route_returns_the_last_message(client, monkeypatch):
    def fake_sample(target, topic, seconds=3):
        return ({"topic": topic, "ok": True, "received": True,
                 "type": "std_msgs/msg/Bool", "stamp": "12.5", "message": "Bool(data=True)"},
                Result(ok=True))

    monkeypatch.setattr("ros2_console.graph.sample_last", fake_sample)
    body = client.get(f"{PREFIX}/sample", params={"topic": "/demo/alarm"}).json()
    assert body == {
        "ok": True,
        "topic": "/demo/alarm",
        "received": True,
        "type": "std_msgs/msg/Bool",
        "stamp": "12.5",
        "message": "Bool(data=True)",
        "hint": None,
    }


def test_sample_route_names_an_idle_topic_instead_of_failing(client, monkeypatch):
    def fake_sample(target, topic, seconds=3):
        return ({"topic": topic, "ok": True, "received": False, "message": "",
                 "hint": "nothing arrived in the window"}, Result(ok=True))

    monkeypatch.setattr("ros2_console.graph.sample_last", fake_sample)
    body = client.get(f"{PREFIX}/sample", params={"topic": "/quiet"}).json()
    assert body["ok"] is True
    assert body["received"] is False
    assert body["hint"] == "nothing arrived in the window"


class _FakeCtx:
    """Stand-in for the plugin context: records what a backend write would persist."""

    def __init__(self):
        self.written = {}

    def set_config(self, key, value):
        self.written[key] = value


def test_write_settings_goes_through_the_plugin_context(monkeypatch):
    from ros2_console import runtime
    from ros2_console.api import write_settings

    ctx = _FakeCtx()
    monkeypatch.setattr(runtime, "_CTX", ctx)
    write_settings({"host": "robot.local", "timeout": 20})
    assert ctx.written == {"host": "robot.local", "timeout": 20}


def test_write_settings_without_a_context_is_a_503(client, monkeypatch):
    """No context means no honest write, so the route must refuse instead of lying."""
    from ros2_console import runtime

    monkeypatch.setattr(runtime, "_CTX", None)
    response = client.post(
        f"{PREFIX}/settings",
        json={"host": "robot.local", "distro": "jazzy", "setup": "", "timeout": 20},
    )
    assert response.status_code == 503
