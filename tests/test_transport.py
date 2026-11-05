"""Transport hardening: the ssh destination and the distro are model-controllable.

A tool argument, a `/settings` POST body and `/graph?host=` all reach
`transport.Target`, so the destination is checked against an allow-list before
an argv exists. A destination that starts with `-` is parsed by ssh as an
*option* (`-oProxyCommand=...` runs a command on this machine), and the distro
is interpolated into `/opt/ros/<distro>/setup.bash`, where `shlex.quote` stops
shell injection but not `../..`.

Every rejection test asserts that `subprocess.run` was never called: the point
is that ssh is not reached, not merely that an error is raised somewhere.
"""

from __future__ import annotations

import json
import types

import pytest

from ros2_console import graph, tools, transport
from ros2_console.transport import OUTPUT_CAP, Result, Target, run

# A hostile destination table plus the destinations that must keep working.
HOSTILE_DESTINATIONS = [
    # ssh reads this as an option and runs the command locally.
    "-oProxyCommand=touch /tmp/ros2-console-pwned",
    "-oLocalCommand=id",
    "-l",
    "-",
    "-host",
    "host with space",
    "host;id",
    "user@-oProxyCommand=touch /tmp/x",
    "user@@host",
    "host\nbash",
    "$(id)",
    "`id`",
]

VALID_DESTINATIONS = [
    "ros2demo",            # ssh_config alias
    "lab-robot.local",     # hostname with a dot and a dash
    "10.0.0.5",            # IPv4 literal
    "robot@10.0.0.5",      # user@host
    "robot@ros2demo",      # user@alias
    "robot.svc@lab-robot_2",
]


def stub_subprocess(monkeypatch):
    """Record every argv handed to subprocess.run instead of running ssh."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transport.subprocess, "run", fake_run)
    return calls


# ------------------------------------------------------------------ destination

@pytest.mark.parametrize("host", HOSTILE_DESTINATIONS)
def test_hostile_destination_is_rejected_at_construction(host, monkeypatch):
    calls = stub_subprocess(monkeypatch)
    with pytest.raises(ValueError):
        Target(host=host)
    assert calls == [], "a hostile host must be refused before any subprocess call"


@pytest.mark.parametrize("host", HOSTILE_DESTINATIONS)
def test_hostile_destination_is_rejected_before_argv(host, monkeypatch):
    """A Target can also be mutated after construction: run() re-validates."""
    calls = stub_subprocess(monkeypatch)
    target = Target()
    target.host = host
    with pytest.raises(ValueError):
        run(target, "echo hi")
    assert calls == []


def test_leading_dash_gets_its_own_message(monkeypatch):
    stub_subprocess(monkeypatch)
    with pytest.raises(ValueError) as excinfo:
        Target(host="-oProxyCommand=true")
    assert "begins with '-'" in str(excinfo.value)


@pytest.mark.parametrize("host", VALID_DESTINATIONS)
def test_valid_destination_is_accepted_and_ssh_is_reached(host, monkeypatch):
    calls = stub_subprocess(monkeypatch)
    target = Target(host=host)
    assert target.mode == "ssh"
    assert target.label == host
    result = run(target, "echo hi")
    assert result.ok is True
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "ssh"
    assert host in argv


def test_destination_is_preceded_by_end_of_options(monkeypatch):
    """`--` is the second lock: even a destination the regex let through cannot
    be parsed as an ssh option."""
    calls = stub_subprocess(monkeypatch)
    run(Target(host="robot@10.0.0.5"), "echo hi")
    argv = calls[0]
    assert "--" in argv
    assert argv.index("--") + 1 == argv.index("robot@10.0.0.5")
    assert argv[argv.index("robot@10.0.0.5") + 1:] == ["bash", "-s"]


def test_batchmode_is_not_weakened(monkeypatch):
    calls = stub_subprocess(monkeypatch)
    run(Target(host="ros2demo"), "echo hi")
    argv = calls[0]
    assert argv[1] == "-o"
    assert argv[2] == "BatchMode=yes"
    assert argv.count("BatchMode=yes") == 1


def test_empty_host_is_local_mode_and_never_calls_ssh(monkeypatch):
    calls = stub_subprocess(monkeypatch)
    target = Target(host="")
    assert target.mode == "local"
    run(target, "echo hi")
    assert calls == [["bash", "-s"]]


# ----------------------------------------------------------------------- distro

HOSTILE_DISTROS = [
    "../../etc",
    "../etc",
    "jazzy/../../etc",
    "/etc",
    "jazzy;id",
    "jazzy\nid",
    "Jazzy",
    " jazzy",
    "",
    "_jazzy",
    "jazzy$(id)",
]


@pytest.mark.parametrize("distro", HOSTILE_DISTROS)
def test_bad_distro_is_rejected(distro, monkeypatch):
    calls = stub_subprocess(monkeypatch)
    if distro == "":
        # Empty means "do not source /opt/ros/... at all"; it is not a name.
        assert Target(distro=distro).prelude() == ""
        return
    with pytest.raises(ValueError):
        Target(distro=distro)
    target = Target()
    target.distro = distro
    with pytest.raises(ValueError):
        run(target, "echo hi")
    assert calls == []


def test_known_distro_is_accepted():
    target = Target(distro="jazzy")
    assert "/opt/ros/jazzy/setup.bash" in target.prelude()


# ------------------------------------------------------- negative control (the table)

def test_negative_control_table_would_fail_without_validation(monkeypatch):
    """Disable the validators and drive the same table again.

    The hostile destinations then reach ssh, which is exactly what the positive
    table forbids — so the table fails when validation is removed, rather than
    passing for unrelated reasons.
    """
    calls = stub_subprocess(monkeypatch)
    monkeypatch.setattr(transport, "validate_destination", lambda value: value)
    monkeypatch.setattr(transport, "validate_distro", lambda value: value)

    target = Target()
    target.host = "-oProxyCommand=touch /tmp/ros2-console-pwned"
    run(target, "echo hi")
    assert calls, "with validation removed the hostile destination must reach ssh"

    assert "../../etc" in Target(distro="../../etc").prelude()
    assert "/opt/ros/../../etc/setup.bash" in Target(distro="../../etc").prelude()


# ------------------------------------------------------------- truncated payload

def over_cap_result(target="local") -> Result:
    """A synthetic frame larger than the cap: the tail of the base64 is cut."""
    return Result(
        ok=True,
        stdout="@@FORMAT jpeg\n@@B64 " + "QUJD" * (OUTPUT_CAP // 4) + "\n…[truncated]",
        truncated=True,
        target=target,
    )


def test_transport_flags_a_truncated_payload():
    text, truncated = transport._cap("x" * (OUTPUT_CAP + 1))
    assert truncated is True
    assert len(text) < OUTPUT_CAP + 100
    assert transport._cap("x" * 10) == ("x" * 10, False)


def test_over_cap_snapshot_is_a_size_error_not_a_decode_error(monkeypatch):
    monkeypatch.setattr(graph, "run", lambda *a, **k: over_cap_result())
    with pytest.raises(ValueError) as excinfo:
        graph.snapshot(Target(), "/camera/image_raw/compressed")
    message = str(excinfo.value)
    assert str(OUTPUT_CAP) in message
    assert "truncated" in message
    assert "camera snapshot of /camera/image_raw/compressed" in message


def test_camera_tool_reports_the_size_error_instead_of_traceback(monkeypatch):
    monkeypatch.setattr(graph, "run", lambda *a, **k: over_cap_result())
    payload = json.loads(
        tools._camera_snapshot({"topic": "/camera/image_raw/compressed"})
    )
    assert payload["ok"] is False
    assert str(OUTPUT_CAP) in payload["error"]
    assert "path" not in payload and "saved_bytes" not in payload


def test_complete_snapshot_still_decodes(monkeypatch):
    """Guard against the truncation check swallowing the normal path."""
    frame = b"jpeg-bytes"
    import base64

    good = Result(
        ok=True,
        stdout="@@FORMAT jpeg\n@@BYTES %d\n@@B64 %s" % (
            len(frame), base64.b64encode(frame).decode("ascii")),
    )

    monkeypatch.setattr(graph, "run", lambda *a, **k: good)
    monkeypatch.setattr(tools, "_frame_path", lambda topic: __import__("pathlib").Path(
        "/tmp/ros2-console-transport-test.jpg"))
    payload = json.loads(
        tools._camera_snapshot({"topic": "/camera/image_raw/compressed"})
    )
    assert payload["ok"] is True
    assert payload["format"] == "jpeg"
    assert payload["saved_bytes"] == len(frame)
    assert "data" not in payload, "the base64 blob is written to disk, not returned"


# ------------------------------------------------------- callers answer in JSON

def test_tool_handler_returns_json_for_a_hostile_host():
    payload = json.loads(tools._graph({"host": "-oProxyCommand=touch /tmp/x"}))
    assert payload["ok"] is False
    assert "ssh destination refused" in payload["error"]


def test_tool_camera_handler_returns_json_for_a_traversing_distro(monkeypatch):
    calls = stub_subprocess(monkeypatch)
    payload = json.loads(
        tools._camera_snapshot({"topic": "/camera/image_raw/compressed", "distro": "../../etc"})
    )
    assert payload["ok"] is False
    assert "distro refused" in payload["error"]
    assert calls == []


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ros2_console.api import router

    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/ros2-console")
    return TestClient(app)


PREFIX = "/api/plugins/ros2-console"


def test_post_settings_refuses_a_hostile_host(client, monkeypatch):
    written = {}
    monkeypatch.setattr("ros2_console.api.write_settings", lambda values: written.update(values))
    response = client.post(f"{PREFIX}/settings", json={
        "host": "-oProxyCommand=touch /tmp/x", "distro": "jazzy", "setup": "", "timeout": 15,
    })
    assert response.status_code == 400
    assert response.json()["detail"]["key"] == "host"
    assert written == {}, "a refused host must not be persisted"


def test_post_settings_refuses_a_traversing_distro(client, monkeypatch):
    written = {}
    monkeypatch.setattr("ros2_console.api.write_settings", lambda values: written.update(values))
    response = client.post(f"{PREFIX}/settings", json={
        "host": "ros2demo", "distro": "../../etc", "setup": "", "timeout": 15,
    })
    assert response.status_code == 400
    assert response.json()["detail"]["key"] == "distro"
    assert written == {}


def test_post_settings_still_accepts_a_valid_target(client, monkeypatch):
    written = {}
    monkeypatch.setattr("ros2_console.api.write_settings", lambda values: written.update(values))
    payload = {"host": "robot@10.0.0.5", "distro": "jazzy", "setup": "", "timeout": 20}
    response = client.post(f"{PREFIX}/settings", json=payload)
    assert response.status_code == 200
    assert written == payload


def test_graph_route_refuses_a_hostile_host(client):
    body = client.get(f"{PREFIX}/graph", params={"host": "-oProxyCommand=touch /tmp/x"}).json()
    assert body["ok"] is False
    assert "ssh destination refused" in body["error"]


def test_camera_route_reports_an_over_cap_frame(client, monkeypatch):
    monkeypatch.setattr(graph, "run", lambda *a, **k: over_cap_result())
    body = client.get(
        f"{PREFIX}/camera", params={"topic": "/camera/image_raw/compressed"}
    ).json()
    assert body["ok"] is False
    assert str(OUTPUT_CAP) in body["error"]
    assert "data_url" not in body
