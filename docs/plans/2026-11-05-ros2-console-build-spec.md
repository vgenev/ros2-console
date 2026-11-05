# ros2-console — build spec (Phase 1)

**Goal.** Turn the existing, partly-verified `ros2-console` Hermes plugin into a catalog-ready
submission: a Desktop pane over the same backend, live-proven tools, offline parser tests with real
must-fail cases, docs, and a catalog entry file.

**Architecture.** One unified Hermes plugin package. Python (stdlib only) runs `ros2` CLI commands on
a target host over SSH using stdin-driven scripts; a FastAPI router (`dashboard/plugin_api.py` →
`ros2_console/api.py`) exposes that to a Desktop pane written as a single uncompiled ESM file
(`desktop/plugin.js`). The agent sees five tools; the pane sees the same data through
`/api/plugins/ros2-console/*`.

**Stack.** Python 3.9+ stdlib, FastAPI (already present in the gateway), pytest for tests, RCLPY only
on the *target* side (inside the demo container), no JS build step.

**Deliverable.** A repo whose gates are green, installable by `hermes plugins install`, with a pane
that shows a live ROS 2 graph and a camera frame, plus `catalog/ros2-console.yaml` ready to paste into
a `NousResearch/hermes-agent` `plugin-catalog/` PR.

---

## Verified starting state (do not rebuild this)

Repo: `/Users/vg/hermes-ros2-console`, branch `hackathon/phase1`, one commit, clean tree.

| File | State |
|---|---|
| `plugin.yaml` | 5 tools declared, `config_schema` for host/distro/setup/timeout |
| `__init__.py` | `register(ctx)` loops `tools.TOOLS` → `ctx.register_tool(...)` |
| `ros2_console/settings.py` | defaults + saved settings via `hermes_cli.config.load_config_readonly`, `resolve()` |
| `ros2_console/transport.py` | `Target`, `run()` — `ssh -o BatchMode=yes host bash -s` with the script on stdin, output capped at 200k chars |
| `ros2_console/graph.py` | graph/rate/sample/snapshot/host scripts + parsers + `_classify()` health flags |
| `ros2_console/tools.py` | the 5 tool handlers, JSON-string returns |
| `demo/nodes.py`, `demo/testcard.jpg` | self-contained ROS 2 cell incl. two deliberately broken topics |

**Live target, already up and reachable** — an SSH config alias, not a hardcoded address:
`ssh ros2demo` (container `ros2demo`, ROS 2 Jazzy, sshd on port 2222, demo cell running).

Measured graph at handoff (`ros2 topic list` inside the container):

```
/board/temperature            Float32             1 publisher,  0 subscribers  → dead_output
/camera/image_raw/compressed  CompressedImage     1 publisher,  1 subscriber   → live
/chatter                      String              1 publisher,  1 subscriber   → live
/depth/points/compressed      CompressedImage     0 publishers, 1 subscriber   → dead_input
/inspection/verdict           String              1 publisher,  0 subscribers  → dead_output?
/parameter_events             ParameterEvent
/rosout                       Log
```

Note `/inspection/verdict`: the `Inspector` demo node subscribes to the camera and publishes verdicts,
but nothing subscribes to the verdict topic. Its true classification is for you to **measure**, not
assume — if the intended demo story needs it "live", the demo cell must gain a consumer; if it stays
`dead_output`, say so and leave the cell alone. Do not silently change `demo/nodes.py` topics.

**Hermes version in use:** v0.22.5 — `hermes plugins validate <dir>` and `hermes plugins doctor <dir>`
both exist and are the admission gates.

---

## Hard boundaries (violating any of these fails the run)

1. **Only edit files under `/Users/vg/hermes-ros2-console`.** Never touch `~/.hermes/hermes-agent`,
   `~/.hermes/config.yaml`, any `~/.hermes/profiles/*`, or any other repo.
2. **Never create a remote, push, or publish anything.** No `gh repo create`, no `git push`. The
   public repo and the catalog PR are the orchestrator's step, behind an explicit human approval.
3. **No new Python dependencies.** stdlib only in `ros2_console/`. Tests may use `pytest`, and may use
   `fastapi.testclient` because FastAPI is already in the gateway environment. Do not add anything to
   a `python_dependencies` field or a `pyproject.toml`.
4. **Keep the five tool names and their input schemas byte-identical** — the demo video and the README
   are already written against them. You may add *output* fields, never rename or remove inputs.
5. **Tool handlers return JSON strings**, never dicts and never coroutines.
6. **`desktop/plugin.js` must be uncompiled ESM**: `import` only from `@hermes/plugin-sdk`, `react`,
   `react/jsx-runtime`; UI built with `jsx()`/`jsxs()` calls (no JSX syntax, no build step); no
   `eval`, no `new Function`, no dynamic `import()`, no prototype patching, no `document` reaching into
   app markup. It must pass `hermes plugins validate`'s desktop-surface check.
7. **No credentials.** The SSH path stays `BatchMode=yes` using the user's own SSH config/agent. No
   passwords, no `sshpass`, no keys written into the repo or plugin data.
8. **Read-only commands only.** The plugin observes (`node list`, `topic list/info/hz/echo`, one
   subscription). Never publish to a robot topic, never `ros2 param set`, never kill nodes.
9. **Work on the current branch**, `hackathon/phase1`. One commit per task, `feat:`/`test:`/`docs:`
   prefixes. Never `git reset`, never force-push, never rebase.

---

## Tasks

Each task: objective → files → interface/behavior → verify → commit.

### T1 — Prove the plugin loads and the tools register

- **Objective:** the package loads under the real plugin runtime with all five tools.
- **Files:** none (fix `__init__.py`/`plugin.yaml` only if the doctor reports a real defect).
- **Verify:** `hermes plugins doctor /Users/vg/hermes-ros2-console` exits 0 and lists
  `ros2_host_status`, `ros2_graph`, `ros2_topic_rate`, `ros2_topic_sample`, `ros2_camera_snapshot`.
- **Commit:** `chore: fix manifest/registration defects surfaced by plugin doctor` (only if it changed).

### T2 — Live probe: host status + graph (the crux gate)

- **Objective:** the parsers are exercised against a real graph, not fixtures.
- **Files:** create `scripts/probe.py`; modify `ros2_console/graph.py` where measurement proves it wrong.
- **Interface:** `python3 scripts/probe.py --target ros2demo` prints one JSON object with keys
  `host` (from `host_status`) and `graph`. Target resolution order: `--target` flag → saved setting →
  local mode. It must not require Hermes to be running.
- **Behavior:** `graph.counts.orphans` equals the number of structurally disconnected topics actually
  present; each of `/board/temperature` and `/depth/points/compressed` carries the correct
  `health` value from `{dead_input, dead_output}`; `/chatter` and `/camera/image_raw/compressed` are
  `live`; nodes include `inspection_camera`, `inspection_verdict`, `talker`, `listener`,
  `depth_filter`, `board_telemetry`. Publisher/subscriber counts are integers from `ros2 topic info`,
  never `None`, for those five topics.
- **Checkpoint:** within the first ten minutes, write the measured raw stdout into
  `docs/plans/2026-11-05-findings.md` and keep refining that file. A timeout must not erase findings.
- **Verify:** run the probe twice — the numbers must be stable; then `git status` clean.
- **Commit:** `feat: live probe script + graph parsing fixed against a real ROS 2 graph`

### T3 — Offline parser tests with a real must-fail

- **Objective:** the health classifier is pinned by tests that can actually fail.
- **Files:** create `tests/fixtures/graph_dead_input.txt`, `tests/fixtures/graph_healthy.txt`
  (capture real command output for at least one of them), `tests/test_graph.py`.
- **Behavior:** a fixture where a topic has 0 publishers and 1 subscriber classifies `dead_input`; a
  topic with 1 publisher and 0 subscribers classifies `dead_output`; counts parse from the real
  `ros2 topic info` text; a topic whose counts are unavailable (`?`) classifies `unmeasured` and is
  **not** reported as an orphan. Include a parametrized case that asserts the *inverse* mapping is
  rejected, so inverting `_classify()` fails the suite.
- **Verify:** `python3 -m pytest tests -q` green. Then deliberately invert the classifier locally,
  confirm the suite goes red, and revert. Record that red-then-green in the commit body.
- **Commit:** `test: pin graph parser + health classifier with must-fail cases`

### T4 — Rate and sample, measured live

- **Objective:** the two "is it actually flowing" tools work on a real graph.
- **Files:** extend `scripts/probe.py` and `tests/test_live.py` (marked `live`).
- **Behavior:** `/chatter` reports `hz` between 7 and 12 over a 5 s window; `/depth/points/compressed`
  reports `hz == 0` with `silent == true` and a hint naming the missing publisher; sampling `/chatter`
  returns a message containing `hello world`; sampling a topic with no traffic returns
  `received: false` plus an error, never an exception.
- **Verify:** `ROS2CONSOLE_LIVE=1 python3 -m pytest tests/test_live.py -q -m live` green; the same run
  with `ROS2CONSOLE_LIVE` unset skips cleanly.
- **Commit:** `feat: live rate/sample coverage with skip-when-offline tests`

### T5 — Camera snapshot, proven on a real frame

- **Objective:** a robot camera becomes a file the agent can look at.
- **Files:** extend `tests/test_live.py`, `scripts/probe.py`.
- **Behavior:** snapshotting `/camera/image_raw/compressed` writes a file whose first three bytes are
  the JPEG magic `FF D8 FF` and whose size exceeds 8000 bytes; the returned JSON carries `path`,
  `bytes`, `format`, `stamp`. Asking the same tool for a non-image topic (e.g. `/chatter`) returns
  `ok: false` with an error naming `CompressedImage` — no traceback, no partial file left behind.
- **Verify:** live pytest green; inspect the written file's magic bytes in the test itself.
- **Commit:** `feat: camera snapshot verified end-to-end on a live compressed image topic`

### T6 — Backend HTTP API for the pane

- **Objective:** the pane has a data source with no agent in the loop.
- **Files:** create `dashboard/manifest.json`, `dashboard/plugin_api.py`, `ros2_console/api.py`,
  `tests/test_api.py`.
- **Interface (exact routes, under `/api/plugins/ros2-console`):**
  - `GET /settings` → `{host, distro, setup, timeout, source}` where `source` is `saved` or `default`.
  - `POST /settings` body `{host, distro, setup, timeout}` → writes through the plugin-settings writer
    (`hermes_cli.plugins_settings.save_plugin_settings`) and returns the effective settings; an invalid
    type returns 400 with the offending key named.
  - `GET /graph?host=&distro=` → the same shape the `ros2_graph` tool returns, including `orphans`.
  - `GET /camera?topic=` → `{ok, topic, format, bytes, data_url}` where `data_url` is a
    `data:image/jpeg;base64,…` string; failure returns `{ok: false, error}` with HTTP 200 so the pane
    can render the message.
- **Behavior:** routes never raise on an unreachable target; they return `ok: false` plus the
  transport error. No route writes outside the plugin data dir.
- **Verify:** `python3 -m pytest tests/test_api.py -q` green using `fastapi.testclient.TestClient`
  against the router object, with the transport stubbed for the failure case.
- **Commit:** `feat: pane backend — settings, graph and camera routes`

### T7 — Desktop pane

- **Objective:** a live ROS 2 console inside Hermes Desktop.
- **Files:** create `desktop/plugin.js`.
- **Interface:** registers one contribution, id `pane`, area `panes`, title `ROS 2`,
  `data: { placement: 'right', width: '360px' }`, plus a `statusBar.right` chip showing the orphan
  count. Panes read only through `ctx.rest`.
- **Behavior:** header shows target + distro + last refresh and a manual Refresh; a banner appears when
  any topic is `dead_input`/`dead_output`, naming up to three of them with their flag; a table lists
  topic, type, publishers, subscribers, health (health colour-coded, monospace counts); a camera panel
  shows the latest frame for the configured topic with a "Grab frame" button and the image's byte size.
  Empty and error states are text, not blank panes. Polling is opt-in (a toggle), interval ≥ 5 s, and
  every timer/subscription is registered through `ctx` so it dies with the plugin.
- **Verify:** `cp desktop/plugin.js /tmp/pane-check.mjs && node --check /tmp/pane-check.mjs` exits 0;
  `hermes plugins validate /Users/vg/hermes-ros2-console` passes the desktop-surface check; state in
  your report that the visual check needs the app (orchestrator/owner does that).
- **Commit:** `feat: ROS 2 Desktop pane — graph table, orphan banner, camera panel`

### T8 — Docs and packaging

- **Objective:** a stranger can install and trust it.
- **Files:** create `README.md`, `LICENSE` (MIT, `vgenev`), `demo/README.md`, `docs/why.md`.
- **Behavior:** README states what it does in one paragraph, the 5 tools with one-line purposes, the
  install line (`hermes plugins install <owner>/ros2-console` — owner left as the placeholder
  `OWNER`), the four settings keys with defaults, the pane, the safety model (read-only commands, SSH
  with `BatchMode=yes`, no credentials held, output caps, no telemetry), the SSH config prerequisite,
  and the "raw `sensor_msgs/Image` is refused — publish a compressed topic" limitation.
  `demo/README.md` documents the exact docker command and the two intentional orphans.
  `docs/why.md` explains dead_input/dead_output in five sentences with no customer, company or product
  names — generic robotics language only.
- **Verify:** every tool name from `plugin.yaml` appears in `README.md` (`grep -c`), and
  `hermes plugins validate` stays green.
- **Commit:** `docs: README, license, demo runbook, why-this-exists`

### T9 — Catalog entry file

- **Objective:** the PR payload exists and is self-consistent.
- **Files:** create `catalog/ros2-console.yaml`, `tests/test_catalog_entry.py`.
- **Interface:** the documented entry schema — `name`, `repo`, `sha` (40-hex placeholder that the
  orchestrator replaces after the first public push), `description` (≤ 200 chars, no marketing),
  `maintainer`, `tier: community`, `category: tools`, `requires_hermes: ">=0.22.0"`, quoted `version`,
  and a `capabilities` block whose `provides_tools` is exactly `plugin.yaml`'s list.
- **Verify:** `python3 -m pytest tests/test_catalog_entry.py -q` green — the test parses both YAML
  files and asserts the tool lists are equal, all required keys present, `sha` is 40 hex, and the
  description length bound.
- **Commit:** `feat: catalog entry + consistency test`

### T10 — Full gate run

- **Objective:** one place where every gate is proven green at once.
- **Files:** create `scripts/gates.sh` (runs, in order: offline pytest, `node --check` on the pane,
  `hermes plugins validate`, `hermes plugins doctor`, live pytest) and print a PASS/FAIL table.
- **Verify:** `bash scripts/gates.sh` prints all PASS with the live target up; anything else is a FAIL
  you fix before reporting. `git status` clean, `git log --oneline` shows one commit per task.
- **Commit:** `chore: single-command gate script`

---

## Report back (required at the end of the run)

1. The gate table from `bash scripts/gates.sh`, verbatim.
2. The measured probe JSON for `/chatter` rate, the two orphans, and the snapshot byte size.
3. Anything in this spec that measurement proved **wrong** — quote the real value.
4. Every file created/modified, and `git log --oneline` for the branch.
5. What still needs the human: Desktop visual check, public repo creation, SHA pin, catalog PR.

**Source wins.** If this document disagrees with the code, the running container, or the Hermes plugin
runtime, the source wins. Flag the disagreement in your report; do not silently follow this doc, and do
not "fix" behaviour that measurement shows is already correct.
