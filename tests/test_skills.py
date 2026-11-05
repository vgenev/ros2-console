"""Bundled skills: valid frontmatter, registered by name, and free of client material.

The last test is a boundary, not a style check: this plugin ships to a public
catalog, so no skill may carry another project's or a customer's material. The
forbidden list is deliberately blunt — if a name is not meant to leave the
building, it does not belong in a skill.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SKILLS = Path("skills")
FORBIDDEN = ("example_prefix", "example_org", "example_board", "example_contact", "example_cell", "example_cell-")


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


def test_no_skill_carries_client_or_project_material():
    offenders = []
    for directory in _skill_dirs():
        text = (directory / "SKILL.md").read_text().lower()
        for word in FORBIDDEN:
            if re.search(rf"\b{re.escape(word)}", text):
                offenders.append((directory.name, word))
    assert offenders == [], f"client material leaked into skills: {offenders}"
