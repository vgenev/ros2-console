"""Plugin settings, resolved the way Hermes stores them.

Reads `plugins.entries.ros2-console.settings.*` through the public config
loader (documented as available to plugin backends). Every read is defensive:
a malformed entry falls back to the manifest default instead of breaking a
tool call.
"""

from __future__ import annotations

from typing import Any

PLUGIN_ID = "ros2-console"

DEFAULTS: dict[str, Any] = {
    "host": "",
    "distro": "jazzy",
    "setup": "",
    "timeout": 15,
}

MIN_TIMEOUT = 5
MAX_TIMEOUT = 120


def stored() -> dict[str, Any]:
    """Values the user has saved, ignoring anything unreadable."""
    try:
        from hermes_cli.config import load_config_readonly
    except Exception:  # pragma: no cover - config module always present in-gateway
        return {}
    try:
        cfg = load_config_readonly() or {}
    except Exception:
        return {}
    plugins = cfg.get("plugins") if isinstance(cfg, dict) else None
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    entry = entries.get(PLUGIN_ID) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    for key in DEFAULTS:
        # `settings` is current; `config` is the pre-migration subtree.
        for subtree in ("settings", "config"):
            sub = entry.get(subtree)
            if isinstance(sub, dict) and sub.get(key) not in (None, ""):
                out[key] = sub[key]
                break
    return out


def clamp_timeout(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = int(DEFAULTS["timeout"])
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, seconds))


def resolve(overrides: dict | None = None) -> dict[str, Any]:
    """Manifest defaults, then saved settings, then this call's overrides."""
    settings = dict(DEFAULTS)
    settings.update(stored())
    for key, value in (overrides or {}).items():
        if key in settings and value not in (None, ""):
            settings[key] = value
    settings["timeout"] = clamp_timeout(settings.get("timeout"))
    settings["host"] = str(settings.get("host") or "").strip()
    settings["distro"] = str(settings.get("distro") or "").strip()
    settings["setup"] = str(settings.get("setup") or "").strip()
    return settings
