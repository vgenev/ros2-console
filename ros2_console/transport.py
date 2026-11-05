"""Run shell on a ROS 2 target — locally, or over SSH — without an agent there.

Two deliberate choices:

* The script travels over **stdin** (`ssh host bash -s`), so there is no nested
  quoting layer to get wrong and no local shell interpolation of remote text.
* SSH runs with `BatchMode=yes`: if the key does not work the command fails in
  six seconds instead of hanging on a password prompt. The plugin never holds
  or asks for a credential — it uses the user's own SSH config and agent.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field

OUTPUT_CAP = 200_000  # characters kept from a single command; protects the context window
CONNECT_TIMEOUT = 6

# The ssh destination and the ROS distro are both model-controllable input: a
# tool argument, a /settings POST body, or /graph?host=. Both are checked
# against an allow-list *before* any argv or remote path is built, because
# `ssh` parses a leading "-" as an option (`-oProxyCommand=...` runs locally)
# and `shlex.quote` stops shell injection but not path traversal.
DESTINATION_RE = re.compile(
    r"^(?:(?P<user>[A-Za-z0-9._][A-Za-z0-9._-]*)@)?"
    r"(?P<host>[A-Za-z0-9][A-Za-z0-9._-]*)$"
)
DISTRO_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_destination(destination: str) -> str:
    """A hostname, IPv4 literal, ssh_config alias, or `user@host`. Empty = local."""
    if not destination:
        return ""
    if destination.startswith("-"):
        raise ValueError(
            "ssh destination refused: it begins with '-', which ssh parses as an option "
            "instead of a host (for example -oProxyCommand=... would run a command on this "
            "machine); use a hostname, an ssh_config alias, or user@host"
        )
    if not DESTINATION_RE.match(destination):
        raise ValueError(
            f"ssh destination refused: {destination!r} is not a hostname, IPv4 address, "
            "ssh_config alias or user@host — only letters, digits, '.', '_', '-' and one "
            "'@' are accepted, and the host may not start with '-'"
        )
    return destination


def validate_distro(distro: str) -> str:
    """A ROS 2 distro name. It is interpolated into `/opt/ros/<distro>/setup.bash`."""
    if not distro:
        return ""
    if not DISTRO_RE.match(distro):
        raise ValueError(
            f"distro refused: {distro!r} does not match ^[a-z][a-z0-9_]*$ (e.g. 'jazzy'); "
            "the name selects /opt/ros/<distro>/setup.bash, so a path separator would "
            "escape /opt/ros"
        )
    return distro


def ensure_complete(result: "Result", what: str = "output") -> None:
    """Refuse to hand on a payload the transport had to cut short.

    Half a base64 frame either decodes into a corrupt image or raises inside
    the decoder; either way the caller must see "too large", not a traceback.
    """
    if result.truncated:
        raise ValueError(
            f"{what} was larger than the transport output cap of {OUTPUT_CAP} characters "
            "and was truncated; refusing to use a partial payload — request less (a "
            "shorter window, fewer topics) or capture a CompressedImage topic instead"
        )


@dataclass
class Target:
    host: str = ""
    distro: str = "jazzy"
    setup: str = ""
    timeout: int = 15

    def __post_init__(self) -> None:
        # Fail at construction, so a hostile host or distro never reaches argv or
        # a remote path even if a caller only builds the Target to inspect it.
        self.host = validate_destination(str(self.host or ""))
        self.distro = validate_distro(str(self.distro or ""))

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
            distro = validate_distro(self.distro)
            lines.append(
                f"[ -f /opt/ros/{shlex.quote(distro)}/setup.bash ] && "
                f". /opt/ros/{shlex.quote(distro)}/setup.bash"
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
    # Re-validate here too: a Target can be built once and then mutated, and this
    # is the last point before an argv exists. Empty = local mode, no ssh.
    destination = validate_destination(str(target.host or ""))
    prelude = target.prelude()
    body = f"{prelude}\n{script}" if prelude else script

    if destination:
        argv = [
            "ssh", "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
            "-o", "ServerAliveInterval=5",
            "-o", "StrictHostKeyChecking=accept-new",
            # `--` ends option parsing, so no part of the destination can be read
            # as an ssh option. The allow-list above already rejects a leading
            # "-"; this is the second lock on the same door, and it costs nothing.
            "--", destination,
            "bash", "-s",
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
