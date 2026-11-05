# Demo video — 84 seconds, one argument

Deadline: **2026-11-11 17:00 EST**. The submission needs a video under 90 s on X tagging
@NousResearch. This plan targets **84 s** so there is room to trim, not to pad.

## The one thing the video has to prove

Judging favours plugins that "do more than a prompt or a skill could". So the video is not a tour of
features. It makes **one argument**, in this order:

> Every ROS 2 dashboard you have used tells you a latched topic is dead. It is not dead. Here is a
> tool that tells the difference, and shows its evidence.

Everything that does not serve that sentence is cut, including things the plugin does well.

## Why this argument and not "it reads a graph"

"A graph view" is a screenshot anyone can make. The discriminator is a **classification nobody gets
right**, and the video shows the tool being right where a naive check is wrong:

| Naive reading | What the tool says | Evidence on screen |
|---|---|---|
| `/session/status` 1 pub / 0 sub → dead output | `state` — latched, value retained | hover returns `{"session": "demo", "state": "armed"}` with no publisher traffic |
| `/chatter` 1/1 → "connected, so it works" | `live` at a **measured** 10.0 msg/s | dashes move at a rate that matches the number |
| `/depth/points/compressed` 0 pubs / 4 subs | `dead_input` | subscribers waiting on nothing |
| `/board/temperature` 1 pub / 0 sub, VOLATILE | `dead_output` at 1.0 msg/s | data flowing into a bin |

The last two rows exist so the video cannot be accused of "everything is fine". The tool reports
three real orphans and says so.

## Ground truth for every number in the video

Measured on the demo container (`ssh ros2demo`, ros:jazzy-ros-base), nodes running `demo/nodes.py all`:

- graph read: **7.5 s**, **7 nodes**, **8 topics**, **3 orphans**, **2 latched**, 1 system excluded
- `/chatter` **10.0/s** live · `/camera/image_raw/compressed` **2.0/s** live
- `/session/status` **1/0, 0.0/s, TRANSIENT_LOCAL → state**, retained JSON replays on hover
- `/depth/points/compressed` **0/4 → dead_input**
- `/board/temperature` **1/0, 1.0/s, VOLATILE → dead_output**
- camera snapshot: **16,374-byte JPEG**; on the real board a raw `mono8 640×480 Image` becomes a
  **49,244-byte JPEG** decoded on the robot
- `ros2_docs "why transient local durability"`: **0.41 s**, excerpt cited `ros2/design@93a415b`

Do not re-type these from memory on shoot day — re-run and read them back.

## Shot list

| # | Time | On screen | Action | Burned-in caption |
|---|---|---|---|---|
| 1 | 0:00–0:06 | Hermes Desktop, ROS 2 page, topology already drawn | still frame, no motion | "A latched topic. 1 publisher. 0 subscribers." |
| 2 | 0:06–0:09 | same | cursor moves to the `session/status` node | "…marked dead by every dashboard." |
| 3 | 0:09–0:16 | page | click **Refresh** — topology redraws | "One call reads the whole graph: 7 nodes, 8 topics, 3 orphans" |
| 4 | 0:16–0:28 | page | hover `/session/status`, then **click to pin**; card shows the retained JSON | "Retained value, replayed. TRANSIENT_LOCAL → `state`, not `orphan`." |
| 5 | 0:28–0:36 | page | hover `/chatter`; dashes speed up; card shows a String sample | "Motion is measured: 10.0 msg/s — not 'it's connected, so it works'" |
| 6 | 0:36–0:44 | page | hover `/depth/points/compressed` then `/board/temperature` | "0 publishers, 4 subscribers. 3 real orphans — it says so." |
| 7 | 0:44–0:56 | page | **Grab frame** → JPEG appears | "One call: 640×480 JPEG. A raw mono8 Image is decoded on the robot." |
| 8 | 0:56–1:10 | Hermes chat | type: `Call ros2_docs: why does transient-local durability matter?` → answer with `ros2/design@93a415b` | "Answers carry the repo and commit they came from. 0.41 s." |
| 9 | 1:10–1:24 | end card | plugin name, 6 tools, install line | `hermes plugin install ros2-console` · "6 tools · page · 3 skills · docs layer" |

84 s total. If it runs long, cut beat 6 to 4 s and beat 8 to 10 s. Never cut beat 4 — it is the
entire submission.

## Pre-flight (do this in order)

1. `hermes gateway restart` — the page needs the current build (`state`/`silent`, `/sample`, `ros2_docs`).
2. ⌘K → **Reload desktop plugins** → open ROS 2 once, confirm 3 orphans and 2 latched.
3. Start the demo nodes: `docker exec -d ros2demo bash -lc 'source /opt/ros/jazzy/setup.bash && exec python3 /work/demo/nodes.py all'`
4. Wait 8 s, then `ros2 topic list` in the container and confirm `/session/status` exists.
5. Turn on Do Not Disturb. Close every window that shows a path, token, IP, or another venture's name.
6. Terminal and app font at **≥15 px**; the video is watched on a phone.
7. Record at 2560×1440, 30 fps, cursor visible (macOS: ⇧⌘5 → Record Selected Portion).

## What must not appear on screen

This ships publicly, so the recording is a publication:

- no IP addresses (use the `ros2demo` / `example_boardq` SSH aliases only)
- no `example_ws`, `example_org`, `example_prefix`, `example_cell`, or any commissioning/cell detail
- no `~/.hermes/config.yaml`, no keys, no `source /opt/ros/... &&` prompts with tokens
- no browser tabs, no Notion/Linear, no wa.me

If a real-board shot is wanted for credibility, one 5 s beat of the camera frame on `example_boardq` is
enough, and the topic namespace must be read before recording.

## Editing rules

- The 7.5 s graph read gets a **2× speed badge**, visible, with the real number on screen. Do not
  hide the wait — a judge who has run ROS 2 knows introspection is slow, and faking speed is the one
  unforgivable edit.
- Burn in captions: X autoplays muted.
- No music. Narration is optional; if used, keep it under 40 words.
- End card holds 6 s so it can be screenshotted as a thumbnail.

## The claim the video makes, in one sentence (for the post text)

> Most ROS 2 dashboards call a latched topic dead. ros2-console reads durability and message rates, so
> `state`, `silent` and `dead_output` are different answers — with the evidence behind each one.

## Deliverables

- `ros2-console-demo.mp4` — 84 s, 1080p, captions burned in
- end-card still for the post thumbnail
- the post text above, tagged @NousResearch
- catalog entry with a real `sha` (requires the public push — separate approval)

## Risks

| Risk | Mitigation |
|---|---|
| Gateway not restarted → page shows old build, no `state` health | beat 4 becomes impossible; check pre-flight step 1 first |
| Demo container recreated → SSH host key changes, `BatchMode` refuses | re-accept with one manual `ssh ros2demo`; this already bit the live gate once |
| Hover sample takes 4–6 s and looks frozen | pre-warm by hovering the topic once before recording; the 30 s cache makes the take instant |
| Text too small on a phone | 15 px minimum, and crop to the page rather than the whole screen |
