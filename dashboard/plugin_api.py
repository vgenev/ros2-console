"""Dashboard mount point. Hermes loads this file and reads `router`."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ros2_console.api import router

__all__ = ["router"]
