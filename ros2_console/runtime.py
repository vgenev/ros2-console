"""The plugin context, captured at register() so backend routes can use it.

`ctx.set_config` is the documented public way for a plugin backend to persist a
setting. Keeping the context here instead of re-importing core internals is what
keeps this plugin inside the catalog's "public surfaces only" rule.
"""

from __future__ import annotations

from typing import Any

_CTX: Any = None


def set_ctx(ctx: Any) -> None:
    global _CTX
    _CTX = ctx


def get_ctx() -> Any:
    return _CTX


def save_setting(key: str, value: Any) -> None:
    """Write one setting through the public context API.

    Raises RuntimeError when the backend runs without a context (a direct import
    outside the gateway) so the caller can answer 503 instead of pretending the
    write succeeded.
    """
    ctx = _CTX
    if ctx is None or not hasattr(ctx, "set_config"):
        raise RuntimeError("no plugin context: settings can only be saved in-gateway")
    ctx.set_config(key, value)
