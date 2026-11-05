"""Answer architecture questions from a local ROS 2 documentation corpus.

The corpus is *not* shipped with this plugin: the ROS 2 documentation, nav2,
moveit2 and ros2_control docs are third-party work (Apache-2.0, BSD-3-Clause and
CC-BY-4.0), and normalising them into markdown makes a derivative. So the user
points `docs_path` at their own local copy and this module reads it, quoting
back with the upstream repo, path and commit that each excerpt came from.

Two strategies, in order:
  graphify  - a knowledge graph over the corpus, if `docs_path` has one
  ripgrep   - plain text search over markdown, when there is no graph

Everything here is stdlib and runs on the Hermes host, not on the robot.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
GRAPHIFY_NODE_RE = re.compile(r"^NODE\s+(?P<name>.+?)\s+\[src=(?P<src>[^\s\]]+)")
STOP_WORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "is", "are", "how", "what",
    "why", "does", "do", "and", "or", "with", "between", "from", "it", "its",
}

MAX_EXCERPT = 900
DEFAULT_LIMIT = 4


def _terms(query: str) -> list[str]:
    words = [w for w in re.split(r"[^A-Za-z0-9_]+", query.lower()) if w]
    kept = [w for w in words if w not in STOP_WORDS and len(w) > 1]
    return kept or words


def parse_graphify(stdout: str) -> list[str]:
    """Source files named by a `graphify query` run, in the order it ranked them."""
    seen: list[str] = []
    for line in stdout.splitlines():
        match = GRAPHIFY_NODE_RE.match(line.strip())
        if not match:
            continue
        src = match.group("src").strip().strip("`'\"")
        if not src or src in seen:
            continue
        seen.append(src)
    return seen


def read_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, text[match.end():]


def _title(text: str, meta: dict[str, str], fallback: str) -> str:
    if meta.get("title"):
        return meta["title"]
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _paragraphs(body: str) -> list[str]:
    """Prose paragraphs only.

    Tables, code fences and directive lines are dropped: an excerpt made of a
    table row ("| Volatile | Volatile |") or a mermaid arrow tells the reader
    nothing, even though it may match every query term.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush():
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            flush()
            continue
        if in_fence:
            continue
        if not stripped:
            flush()
            continue
        if stripped.startswith("#"):
            flush()
            continue
        if stripped.startswith("|") or set(stripped) <= {"-", "|", ":", " "}:
            flush()
            continue
        if stripped.startswith(".. ") or re.match(r"^:[a-z][-a-z]*:", stripped):
            flush()
            continue
        if "\\>" in stripped or "-\\>" in stripped:
            flush()
            continue
        current.append(stripped)
    flush()
    return paragraphs


def doc_score(text: str, terms: list[str]) -> int:
    low = text.lower()
    return sum(low.count(term) for term in terms)


def extract_excerpt(text: str, terms: list[str], limit: int = MAX_EXCERPT) -> str:
    """The paragraph that best matches the query, with neighbours when short."""
    _, body = read_frontmatter(text)
    paragraphs = _paragraphs(body)
    if not paragraphs:
        return ""

    scores = [sum(p.lower().count(t) for t in terms) for p in paragraphs]
    best = max(range(len(paragraphs)), key=lambda i: scores[i])
    if scores[best] == 0:
        return paragraphs[0][:limit]

    chosen = [best]
    total = len(paragraphs[best])
    for neighbour in (best + 1, best - 1, best + 2, best - 2):
        if total >= limit // 2:
            break
        if 0 <= neighbour < len(paragraphs):
            chosen.append(neighbour)
            total += len(paragraphs[neighbour])
    body_text = "\n\n".join(paragraphs[i] for i in sorted(set(chosen)))
    if len(body_text) > limit:
        body_text = body_text[:limit].rsplit(" ", 1)[0] + " …"
    return body_text


def _run(cmd: list[str], cwd: str, timeout: int) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return proc.returncode == 0, proc.stdout or ""


def _resolve(docs_path: str, relative: str) -> Path | None:
    """Corpus files are recorded relative to the corpus root, which may sit one
    level below the directory that holds the graph."""
    base = Path(docs_path)
    for candidate in (base / relative, base / "corpus" / relative):
        if candidate.is_file():
            return candidate
    return None


def _grep_files(docs_path: str, terms: list[str], timeout: int) -> list[str]:
    if not terms:
        return []
    if not shutil.which("rg"):
        return []
    pattern = "|".join(re.escape(t) for t in terms)
    ok, out = _run(
        ["rg", "-l", "-i", "--glob", "*.md", "--glob", "!graphify-out/**", pattern, "."],
        docs_path,
        timeout,
    )
    if not ok:
        return []
    return [line.strip().lstrip("./") for line in out.splitlines() if line.strip()][:40]


def describe(docs_path: str) -> dict:
    """What the configured path actually offers, without running a query."""
    if not docs_path:
        return {"configured": False, "reason": "docs_path is empty"}
    base = Path(docs_path).expanduser()
    if not base.is_dir():
        return {"configured": False, "reason": f"{docs_path} is not a directory"}
    has_graph = (base / "graphify-out" / "graph.json").is_file()
    corpus = base / "corpus"
    root = corpus if corpus.is_dir() else base
    md_count = sum(1 for _ in root.rglob("*.md"))
    return {
        "configured": True,
        "path": str(base),
        "graph": has_graph,
        "markdown_files": md_count,
        "graphify_cli": bool(shutil.which("graphify")),
        "ripgrep": bool(shutil.which("rg")),
    }


def query(docs_path: str, question: str, limit: int = DEFAULT_LIMIT, timeout: int = 20) -> dict:
    """Search the corpus and return bounded excerpts with their provenance."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "query is required"}
    info = describe(docs_path)
    if not info.get("configured"):
        return {
            "ok": False,
            "error": "docs_path is not configured",
            "hint": (
                "Set plugins.entries.ros2-console.settings.docs_path to a local ROS 2 "
                "documentation tree (a directory of markdown, optionally with a graphify "
                "index). Nothing is bundled with the plugin: the upstream docs are "
                "Apache-2.0, BSD-3-Clause and CC-BY-4.0 work."
            ),
            "docs": info,
        }
    base = info["path"]
    terms = _terms(question)
    mode = "graphify" if (info["graph"] and info["graphify_cli"]) else "grep"
    files: list[str] = []
    if mode == "graphify":
        ok, out = _run(["graphify", "query", question, "--budget", "1200"], base, timeout)
        if ok:
            files = parse_graphify(out)
        if not files:
            mode = "grep"
    if mode == "grep":
        files = _grep_files(base, terms, timeout)
    if not files:
        return {
            "ok": True,
            "query": question,
            "mode": mode,
            "results": [],
            "note": "no document matched; try fewer or more specific words",
            "docs": info,
        }

    # Graph ranking is topical, not literal: it will happily return a moveit2
    # solver page for a QoS question. Re-rank by how often the document actually
    # contains the query terms, and drop documents with no hit at all when
    # something better exists.
    candidates = []
    for relative in files:
        path = _resolve(base, relative)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        candidates.append((doc_score(text, terms), relative, text))
    if not candidates:
        return {"ok": True, "query": question, "mode": mode, "results": [],
                "note": "matched documents were not readable", "docs": info}
    candidates.sort(key=lambda item: item[0], reverse=True)
    if candidates[0][0] > 0:
        candidates = [c for c in candidates if c[0] > 0]

    results = []
    for _score, relative, text in candidates:
        if len(results) >= max(1, min(10, int(limit))):
            break
        meta, _ = read_frontmatter(text)
        results.append(
            {
                "path": relative,
                "title": _title(text, meta, path.stem),
                "source_repo": meta.get("source_repo", ""),
                "source_path": meta.get("source_path", ""),
                "source_commit": meta.get("source_commit", ""),
                "excerpt": extract_excerpt(text, terms),
            }
        )
    return {
        "ok": True,
        "query": question,
        "mode": mode,
        "results": results,
        "note": (
            "Excerpts are normalised derivatives of upstream documentation "
            "(RST converted to markdown, headings stripped). Quote with the "
            "source_repo/source_commit shown."
        ),
        "docs": info,
    }
