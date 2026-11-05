"""Run shell on a ROS 2 target — locally, or over SSH — without an agent there.

Two deliberate choices:

* The script travels over **stdin** (`ssh host bash -s`), so there is no nested
  quoting layer to get wrong and no local shell interpolation of remote text.
* SSH runs with `BatchMode=yes`: if the key does not work the command fails in
  six seconds instead of hanging on a password prompt. The plugin never holds
  or asks for a credential — it uses the user's own SSH config and agent.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field

OUTPUT_CAP = 200_000  # characters kept from a single command; protects the context window
CONNECT_TIMEOUT = 6


@dataclass
class Target:
    host: str = ""
    distro: str = "jazzy"
    setup: str = ""
    timeout: int = 15

    @property
    def mode(self) -> str:
        return "ssh" if self.host else "local"

    @property
    def label(self) -> str:
        return self.host or "local"

    def prelude(self) -> str:
        """Shell lines that make `ros2` importable on the target."""
        lines = []
        if self.distro:
            lines.append(
                f"[ -f /opt/ros/{shlex.quote(self.distro)}/setup.bash ] && "
                f". /opt/ros/{shlex.quote(self.distro)}/setup.bash"
            )
        if self.setup:
            quoted = shlex.quote(self.setup)
            lines.append(f"[ -f {quoted} ] && . {quoted}")
        return "\n".join(lines)


@dataclass
class Result:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    seconds: float = 0.0
    timed_out: bool = False
    command: str = ""
    target: str = ""
    truncated: bool = False
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {
            "ok": self.ok,
            "target": self.target,
            "exit_code": self.exit_code,
            "seconds": round(self.seconds, 2),
            "timed_out": self.timed_out,
            "stdout": self.stdout,
        }
        if self.stderr:
            out["stderr"] = self.stderr
        if self.truncated:
            out["truncated"] = True
        out.update(self.meta)
        return out

    def error(self) -> str:
        """One human-readable line explaining a failed call."""
        if self.timed_out:
            return (
                f"timed out after {self.seconds:.1f}s on {self.target or 'target'} — "
                "a robot that is unreachable or a topic that never publishes both look like this"
            )
        detail = (self.stderr or self.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else "no output"
        return f"exit {self.exit_code} on {self.target or 'target'}: {tail}"


def _cap(text: str) -> tuple[str, bool]:
    if len(text) <= OUTPUT_CAP:
        return text, False
    return text[:OUTPUT_CAP] + "\n…[truncated]", True


def run(target: Target, script: str, timeout: int | float | None = None,
        label: str = "") -> Result:
    """Run `script` on the target with the ROS 2 environment sourced."""
    seconds = float(timeout or target.timeout)
    prelude = target.prelude()
    body = f"{prelude}\n{script}" if prelude else script

    if target.host:
        argv = [
            "ssh", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
            "-o", "ServerAliveInterval=5",
            "-o", "StrictHostKeyChecking=accept-new",
            target.host, "bash", "-s",
        ]
    else:
        argv = ["bash", "-s"]

    started = time.time()
    try:
        proc = subprocess.run(
            argv, input=body, capture_output=True, text=True, timeout=seconds,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        out, _ = _cap(out)
        return Result(ok=False, stdout=out, stderr=err or "", exit_code=124,
                      seconds=time.time() - started, timed_out=True,
                      command=label, target=target.label)
    except FileNotFoundError as exc:
        return Result(ok=False, stderr=str(exc), exit_code=127,
                      seconds=time.time() - started, command=label,
                      target=target.label)

    elapsed = time.time() - started
    stdout, truncated = _cap(proc.stdout or "")
    stderr, _ = _cap(proc.stderr or "")
    return Result(
        ok=proc.returncode == 0,
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
        seconds=elapsed,
        command=label,
        target=target.label,
        truncated=truncated,
    )
