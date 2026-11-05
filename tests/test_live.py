"""Live checks against ssh ros2demo. Skipped unless ROS2CONSOLE_LIVE=1."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from ros2_console.tools import _camera_snapshot, _topic_rate, _topic_sample

pytestmark = pytest.mark.live

HOST = "ros2demo"


@pytest.fixture(autouse=True)
def _require_live_env():
    if os.environ.get("ROS2CONSOLE_LIVE") != "1":
        pytest.skip("ROS2CONSOLE_LIVE is unset")


def _call(fn, topic: str, seconds: int) -> dict:
    raw = fn({"topic": topic, "host": HOST, "seconds": seconds})
    assert isinstance(raw, str)
    return json.loads(raw)


def test_chatter_rate_is_about_ten_hertz():
    started = time.monotonic()
    data = _call(_topic_rate, "/chatter", 5)
    elapsed = time.monotonic() - started
    # Measured 9.99-10.04 Hz over 8 consecutive runs once the demo cell stopped
    # starving the talker (2 Hz camera instead of 5 Hz). The band stays open for a
    # contended container, and the elapsed bound catches a measurement that
    # stretches into the transport cap instead of finishing its window.
    assert 8 <= data["hz"] <= 12
    assert data["silent"] is False
    assert data["window"] >= 10
    assert elapsed < 9


def test_depth_topic_is_silent_without_a_publisher():
    started = time.monotonic()
    data = _call(_topic_rate, "/depth/points/compressed", 5)
    elapsed = time.monotonic() - started
    assert data["hz"] == 0
    assert data["silent"] is True
    assert "missing publisher" in data["hint"]
    assert "/depth/points/compressed" in data["hint"]
    assert "unpublished" in data["hint"]
    assert elapsed < 3


def test_chatter_sample_contains_hello_world():
    data = _call(_topic_sample, "/chatter", 5)
    assert data["received"] is True
    assert "hello world" in data["message"]


def test_sample_of_a_silent_topic_returns_an_error():
    data = _call(_topic_sample, "/depth/points/compressed", 4)
    assert data["received"] is False
    assert data["error"]


@pytest.fixture
def frame_dir(tmp_path, monkeypatch):
    def fake_frame_path(topic: str) -> Path:
        slug = topic.strip("/").replace("/", "_") or "frame"
        return tmp_path / f"{slug}.jpg"

    monkeypatch.setattr("ros2_console.tools._frame_path", fake_frame_path)
    return tmp_path


def test_camera_snapshot_writes_a_jpeg(frame_dir):
    data = _call(_camera_snapshot, "/camera/image_raw/compressed", 6)
    assert data["ok"] is True
    assert data["format"] == "jpeg"
    assert data["stamp"]
    assert data["bytes"] > 8000
    path = Path(data["path"])
    assert path.is_file()
    assert path.parent == frame_dir
    raw = path.read_bytes()
    assert raw[:3] == b"\xff\xd8\xff"
    assert len(raw) > 8000
    assert len(raw) == data["bytes"]


def test_snapshot_of_a_non_image_topic_writes_nothing(frame_dir):
    data = _call(_camera_snapshot, "/chatter", 4)
    assert data["ok"] is False
    assert "CompressedImage" in data["error"]
    assert "Traceback" not in data["error"]
    assert list(frame_dir.iterdir()) == []
