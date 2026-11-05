"""Offline tests for the documentation knowledge layer.

The corpus itself is never bundled and never read from the network here: these
tests build a tiny corpus in a temp dir and check the parsing, ranking and
provenance behaviour that the tool depends on.
"""

from __future__ import annotations

from pathlib import Path

from ros2_console import docs


GRAPHIFY_OUTPUT = """
Graph: graphify-out/graph.json (3718 nodes) | Traversal: BFS depth=2 | 33 nodes found

NODE Durability QoS Policy [src=design/articles/qos.md loc=None community=]
NODE Reliability QoS Policy [src=design/articles/qos.md loc=None community=]
NODE QoS overrides [src=ros2-docs/source/QoS-Overrides.md loc=None community=]
non-node chatter that must be ignored
"""


def test_graphify_output_yields_unique_source_files_in_ranked_order():
    files = docs.parse_graphify(GRAPHIFY_OUTPUT)
    assert files == ["design/articles/qos.md", "ros2-docs/source/QoS-Overrides.md"]


def test_excerpt_is_prose_not_a_table_row():
    """A markdown table matches every query term and reads terribly."""
    text = """---
title: "Durability"
source_repo: "ros2/ros2_documentation"
---

| Publisher | Subscription | Compatible |
|---|---|---|
| Volatile | Volatile | yes |
| Transient local | Volatile | yes |

Durability decides whether a late subscriber still receives samples that were
published before it joined, which is why transient local keeps the last value
alive for the newcomer.
"""
    excerpt = docs.extract_excerpt(text, ["durability", "transient", "late"])
    assert "|" not in excerpt
    assert "late subscriber" in excerpt


def test_excerpt_skips_code_fences():
    text = """# Executors

```text
Executor -\\> SingleThreadedExecutor [dir = back]
```

A single-threaded executor runs every callback on one thread, so a blocking
callback stops timers and subscriptions from ever being serviced.
"""
    excerpt = docs.extract_excerpt(text, ["executor", "single", "thread"])
    assert "dir = back" not in excerpt
    assert "single-threaded executor" in excerpt


def test_frontmatter_provenance_is_read_back():
    meta, body = docs.read_frontmatter(
        '---\ntitle: "X"\nsource_repo: "ros2/design"\nsource_commit: "93a415b"\n---\n\nbody\n'
    )
    assert meta["source_repo"] == "ros2/design"
    assert meta["source_commit"] == "93a415b"
    assert body.strip() == "body"


def test_unconfigured_docs_path_explains_itself():
    payload = docs.query("", "why patch size matters")
    assert payload["ok"] is False
    assert "docs_path" in payload["error"]
    assert "CC-BY-4.0" in payload["hint"]


def test_a_local_corpus_is_searched_with_provenance(tmp_path: Path, monkeypatch):
    corpus = tmp_path / "corpus"
    (corpus / "guide").mkdir(parents=True)
    (corpus / "guide" / "lifecycle.md").write_text(
        "---\n"
        'title: "Node lifecycle"\n'
        'source_repo: "ros2/design"\n'
        'source_commit: "abc1234"\n'
        "---\n\n"
        "# Node lifecycle\n\n"
        "A managed node moves through unconfigured, inactive, active and finalized,\n"
        "and each transition is triggered by a dedicated lifecycle service.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(docs, "_grep_files", lambda base, terms, timeout: ["guide/lifecycle.md"])
    payload = docs.query(str(tmp_path), "managed node lifecycle transition", limit=2)
    assert payload["ok"] is True
    assert payload["mode"] == "grep"
    result = payload["results"][0]
    assert result["source_repo"] == "ros2/design"
    assert result["source_commit"] == "abc1234"
    assert "unconfigured, inactive, active" in result["excerpt"]


def test_documents_without_the_terms_are_dropped(tmp_path: Path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "relevant.md").write_text(
        "---\ntitle: r\nsource_repo: r/r\nsource_commit: c\n---\n\n"
        "Durability transient local keeps the last sample for late subscribers.\n",
        encoding="utf-8",
    )
    (corpus / "noise.md").write_text(
        "---\ntitle: n\nsource_repo: n/n\nsource_commit: c\n---\n\n"
        "Inverse kinematics for a six axis arm uses a gradient descent solver.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        docs, "_grep_files", lambda base, terms, timeout: ["noise.md", "relevant.md"]
    )
    payload = docs.query(str(tmp_path), "transient local durability", limit=2)
    names = [r["path"] for r in payload["results"]]
    assert names == ["relevant.md"]
