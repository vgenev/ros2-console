"""HTTP routes for the Desktop pane. Mounted at /api/plugins/ros2-console.

Sync handlers on purpose. SSH blocks, and FastAPI runs a plain def in a worker
thread instead of on the gateway event loop.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import graph as obs
from .settings import PLUGIN_ID, allowlist, resolve, stored
from .tools import _graph
from .transport import Target, validate_destination, validate_distro

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
        "publish_allow": current["publish_allow"],
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
    # The values are model-controllable, and `host` becomes an ssh argv and
    # `distro` a remote path: refuse them here so a hostile value is never
    # persisted and never breaks every later call.
    for name, check in (("host", validate_destination), ("distro", validate_distro)):
        try:
            check(body[name])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"key": name, "error": str(exc)}) from exc
    payload = {name: body[name] for name in _FIELDS}
    # Optional: absent means "leave the allowlist alone", never "grant everything".
    if "publish_allow" in body:
        value = body["publish_allow"]
        if not isinstance(value, (list, str)):
            raise HTTPException(status_code=400, detail={
                "key": "publish_allow",
                "error": "publish_allow must be a list of topic globs (or a comma-separated string)",
            })
        payload["publish_allow"] = allowlist(value)
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


@router.get("/sample")
def get_sample(topic: str = "", seconds: int = 3, host: str = "", distro: str = "") -> dict:
    """Last message on a topic, for the page's hover card."""
    if not topic:
        return {"ok": False, "topic": topic, "error": "topic is required"}
    try:
        data, result = obs.sample_last(_target_from(host, distro), topic, seconds)
    except Exception as exc:
        return {"ok": False, "topic": topic, "error": str(exc)}
    if not data.get("ok"):
        return {"ok": False, "topic": topic, "error": data.get("error") or result.error()}
    return {
        "ok": True,
        "topic": topic,
        "received": bool(data.get("received")),
        "type": data.get("type") or "",
        "stamp": data.get("stamp") or "",
        "message": data.get("message") or "",
        "hint": data.get("hint"),
    }


@router.get("/camera")
def get_camera(topic: str = "", host: str = "", distro: str = "") -> dict:
    if not topic:
        return {"ok": False, "topic": topic, "error": "topic is required"}
    try:
        # `host`/`distro` mirror /graph: the pane may target a host the saved
        # settings do not name yet (a new robot on the bench).
        data, result = obs.snapshot(_target_from(host, distro), topic)
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
