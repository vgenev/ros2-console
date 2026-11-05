"""HTTP routes for the Desktop pane. Mounted at /api/plugins/ros2-console.

Sync handlers on purpose. SSH blocks, and FastAPI runs a plain def in a worker
thread instead of on the gateway event loop.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import graph as obs
from .settings import PLUGIN_ID, resolve, stored
from .tools import _graph
from .transport import Target

router = APIRouter()

_FIELDS = ("host", "distro", "setup", "timeout")


def write_settings(values: dict) -> None:
    from hermes_cli.plugins_settings import save_plugin_settings

    root = Path(__file__).resolve().parents[1]
    save_plugin_settings(PLUGIN_ID, root, values)


def _settings_body(source: str, values: dict | None = None) -> dict:
    current = resolve(values)
    return {
        "host": current["host"],
        "distro": current["distro"],
        "setup": current["setup"],
        "timeout": current["timeout"],
        "source": source,
    }


def _bad_key(body: dict) -> str | None:
    for key in _FIELDS:
        if key not in body:
            return key
        value = body[key]
        if key == "timeout":
            if isinstance(value, bool) or not isinstance(value, int):
                return key
        elif not isinstance(value, str):
            return key
    return None


def _reject(key: str) -> None:
    raise HTTPException(status_code=400, detail={"key": key, "error": f"invalid type for {key}"})


@router.get("/settings")
def get_settings() -> dict:
    return _settings_body("saved" if stored() else "default")


@router.post("/settings")
def post_settings(body: dict) -> dict:
    key = _bad_key(body)
    if key:
        _reject(key)
    payload = {name: body[name] for name in _FIELDS}
    try:
        write_settings(payload)
    except ValueError as exc:
        named = next((name for name in _FIELDS if name in str(exc)), payload and _FIELDS[0])
        raise HTTPException(status_code=400, detail={"key": named, "error": str(exc)}) from exc
    return _settings_body("saved", payload)


def _target_from(host: str, distro: str) -> Target:
    current = resolve({"host": host, "distro": distro})
    return Target(
        host=current["host"],
        distro=current["distro"],
        setup=current["setup"],
        timeout=current["timeout"],
    )


@router.get("/graph")
def get_graph(host: str = "", distro: str = "") -> dict:
    try:
        return json.loads(_graph({"host": host, "distro": distro}))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/camera")
def get_camera(topic: str = "") -> dict:
    if not topic:
        return {"ok": False, "topic": topic, "error": "topic is required"}
    try:
        data, result = obs.snapshot(_target_from("", ""), topic)
    except Exception as exc:
        return {"ok": False, "topic": topic, "error": str(exc)}
    if not data.get("ok"):
        return {"ok": False, "topic": topic, "error": data.get("error") or result.error()}
    payload = data.get("data") or ""
    return {
        "ok": True,
        "topic": topic,
        "format": data.get("format") or "jpeg",
        "bytes": data.get("bytes") or 0,
        "data_url": "data:image/jpeg;base64," + payload,
    }
