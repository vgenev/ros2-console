"""Bundled skills: valid frontmatter, registered by name, and a whole-tree
boundary against client material.

The last test is a boundary, not a style check: this plugin ships to a public
catalog, so no tracked file may carry another project's or a customer's
material. It scans every tracked text file, not just `skills/` — the strings it
rejects sat in tests and a comment while the old guard read only skills/ and
reported clean.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SELF = Path(__file__).resolve()

# Spell the strings out here and nowhere else: the guard has to fail on these
# exact words, so this tuple is the one place in the tree that carries them.
FORBIDDEN = ("example_cell", "example_ws", "example_prefix", "example_org", "example_board", "example_contact")


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
        lowered = text.lower()
        for word in FORBIDDEN:
            # Word-start match, so a topic such as `/demo/<word>` is caught while
            # an unrelated word that merely contains the letters is not.
            if re.search(rf"(?<![a-z0-9]){re.escape(word)}", lowered):
                offenders.append((name, word))
    assert offenders == [], f"client material leaked into the tree: {offenders}"
