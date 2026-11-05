"""The catalog file matches plugin.yaml and the entry schema."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

def acceptable_sha(value: object) -> bool:
    """A pending pin, or a 40-hex digest that is not all zeros."""
    if value == "SHA_PENDING":
        return True
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{40}", value) is None:
        return False
    return set(value.lower()) != {"0"}


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
    assert acceptable_sha(catalog["sha"])
    assert len(catalog["description"]) <= 200
    assert catalog["tier"] == "community"
    assert catalog["category"] == "tools"
    assert catalog["requires_hermes"] == ">=0.22.0"
    assert catalog["version"] == plugin["version"]
    assert isinstance(catalog["version"], str)


def test_all_zero_digest_is_rejected():
    assert acceptable_sha("0" * 40) is False
    assert acceptable_sha("SHA_PENDING") is True
    assert acceptable_sha("a" * 40) is True
