"""Bundled skills: valid frontmatter, registered by name, and a whole-tree
boundary against client material.

The last test is a boundary, not a style check: this plugin ships to a public
catalog, so no tracked file may carry another project's or a customer's
material. It scans every tracked text file, not just `skills/` — the strings it
rejects sat in tests and a comment while the old guard read only skills/ and
reported clean.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SELF = Path(__file__).resolve()

# Hashes, not words. A deny-list spelled out in the tree IS the disclosure — a
# guard that names the client material it forbids publishes it in the same
# commit it protects. Each entry is sha256 of one lowercase token that must
# never appear in a tracked file; the guard hashes every token it reads.
FORBIDDEN_SHA256 = frozenset({
    "0066e522e4422330be8c766d722e11bfd4039d5b29b45169a5da10aa47e39b18",
    "436a4d345640f1b4333dd5730bb58a2974dce201b19349e95da634ccd5891b32",
    "4a1a1c98936e3f372bee3ccbae203b3c1f683a6dba5fa9cc0bf0cab989a3b717",
    "6b065c28f5e849ddc8edcdb8986a7f186dd053c684858f8ec1744c3667aabf83",
    "8a4bbb4c769876bf349a2a53bcff3358cdcc4b959be0832872dad0a2059cf954",
    "a56100fc4a21ccff0083ace08e2d2cddbef44b04ec5117852ed5e8ebab6227e2",
})


def _is_forbidden(token: str) -> bool:
    return hashlib.sha256(token.encode()).hexdigest() in FORBIDDEN_SHA256


def _skill_dirs():
    return sorted(p for p in SKILLS.iterdir() if p.is_dir() and (p / "SKILL.md").exists())


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    block = text.split("---\n", 2)[1]
    return yaml.safe_load(block)


def test_every_skill_has_frontmatter_matching_its_directory():
    dirs = _skill_dirs()
    assert len(dirs) >= 3, "expected at least three bundled skills"
    for directory in dirs:
        meta = _frontmatter(directory / "SKILL.md")
        assert meta["name"] == directory.name
        assert isinstance(meta["description"], str) and len(meta["description"]) > 40
        assert meta["description"].startswith("Use when"), "description must read as a trigger"


def test_register_skills_each_bundled_skill(monkeypatch):
    import importlib.util
    import sys
    import types

    # Load the plugin entry as a package so its relative import resolves, the way
    # Hermes loads it — a standalone spec would fail on `from .ros2_console import`.
    package = types.ModuleType("ros2console_under_test")
    package.__path__ = [str(Path(".").resolve())]
    sys.modules["ros2console_under_test"] = package
    spec = importlib.util.spec_from_file_location(
        "ros2console_under_test.__init__", "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["ros2console_under_test.__init__"] = module
    spec.loader.exec_module(module)

    tools_calls, skills_calls = [], []

    class Ctx:
        def register_tool(self, **kwargs):
            tools_calls.append(kwargs["name"])

        def register_skill(self, name, path):
            skills_calls.append((name, Path(path).name))

    module.register(Ctx())
    # Count follows the manifest rather than a hand-kept number: adding a tool
    # should not silently pass a test that no longer checks anything.
    from ros2_console import tools as tools_module

    assert len(tools_calls) == len(tools_module.TOOLS)
    assert set(tools_calls) == {tool.name for tool in tools_module.TOOLS}
    assert [name for name, _ in skills_calls] == [d.name for d in _skill_dirs()]
    assert all(filename == "SKILL.md" for _, filename in skills_calls)


def _tracked_text_files():
    """Every tracked file that decodes as text, minus this test itself."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        name = raw.decode()
        path = ROOT / name
        if path.resolve() == SELF or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8000]:
            continue  # binary asset: prose does not leak from here
        try:
            yield name, data.decode("utf-8")
        except UnicodeDecodeError:
            continue


def test_no_tracked_file_carries_client_or_project_material():
    offenders = []
    for name, text in _tracked_text_files():
        for token in set(re.findall(r"[a-z0-9_]+", text.lower())):
            if _is_forbidden(token):
                # Report the FILE and a token hash — printing the token here
                # would re-publish the very word the guard exists to keep out.
                offenders.append((name, hashlib.sha256(token.encode()).hexdigest()[:12]))
    assert offenders == [], f"client material leaked into the tree: {offenders}"
