"""The catalog file matches plugin.yaml and the entry schema."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REQUIRED = (
    "name",
    "repo",
    "sha",
    "description",
    "maintainer",
    "tier",
    "category",
    "requires_hermes",
    "version",
    "capabilities",
)


def test_catalog_entry_matches_the_plugin():
    catalog = yaml.safe_load(Path("catalog/ros2-console.yaml").read_text())
    plugin = yaml.safe_load(Path("plugin.yaml").read_text())
    missing = [key for key in REQUIRED if key not in catalog]
    assert missing == []
    assert catalog["capabilities"]["provides_tools"] == plugin["provides_tools"]
    assert re.fullmatch(r"[0-9a-fA-F]{40}", catalog["sha"])
    assert len(catalog["description"]) <= 200
    assert catalog["tier"] == "community"
    assert catalog["category"] == "tools"
    assert catalog["requires_hermes"] == ">=0.22.0"
    assert catalog["version"] == plugin["version"]
    assert isinstance(catalog["version"], str)
