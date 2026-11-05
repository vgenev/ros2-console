# Demo video — shot list

A short recording (under 90 s) for the submission post. This file keeps only the
shot list, the argument it makes, and the production notes. It replaces the
internal planning notes that used to live in `docs/plans/`, which described a
private engagement and do not belong in a public repo.

## The one argument

> Every ROS 2 dashboard you have used tells you a latched topic is dead. It is not
> dead. Here is a tool that tells the difference, and shows its evidence.

Everything that does not serve that sentence is cut.

## Why that argument

| Naive reading | What the tool says | Evidence on screen |
|---|---|---|
| `/session/status` 1 pub / 0 sub → dead output | `state` — latched, value retained | hover returns `{"session": "demo", "state": "armed"}` with no publisher traffic |
| `/chatter` 1/1 → "connected, so it works" | `live` at a **measured** rate | dashes move at a rate that matches the number |
| `/depth/points/compressed` 0 pubs / 4 subs | `dead_input` | subscribers waiting on nothing |
| `/board/temperature` 1 pub / 0 sub, VOLATILE | `dead_output` | data flowing into a bin |

The last two rows exist so the video cannot be accused of "everything is fine":
the tool reports the real orphans and says so.

## Shot list

| # | Time | On screen | Action | Burned-in caption |
|---|---|---|---|---|
| 1 | 0:00–0:06 | desktop app, ROS 2 pane, topology already drawn | still frame, no motion | "A latched topic. 1 publisher. 0 subscribers." |
| 2 | 0:06–0:09 | same | cursor moves to the `session/status` node | "…marked dead by every dashboard." |
| 3 | 0:09–0:16 | pane | click **Refresh** — topology redraws | "One call reads the whole graph" |
| 4 | 0:16–0:28 | pane | hover `/session/status`, then **click to pin**; the card shows the retained JSON | "Retained value, replayed. TRANSIENT_LOCAL → `state`, not `orphan`." |
| 5 | 0:28–0:36 | pane | hover `/chatter`; dashes speed up; card shows a String sample | "Motion is measured — not 'it's connected, so it works'" |
| 6 | 0:36–0:44 | pane | hover `/depth/points/compressed` then `/board/temperature` | "0 publishers, 4 subscribers. Real orphans — it says so." |
| 7 | 0:44–0:56 | pane | **Grab frame** → JPEG appears | "One call: a JPEG. A raw Image is decoded on the robot." |
| 8 | 0:56–1:10 | chat | ask `ros2_docs`: why does transient-local durability matter? → answer cites repo and commit | "Answers carry the repo and commit they came from." |
| 9 | 1:10–1:24 | end card | plugin name, tool count, install line | `hermes plugins install ros2-console` |

If it runs long, cut beat 6 to 4 s and beat 8 to 10 s. Never cut beat 4 — it is the
entire submission.

## Ground truth

Every number on screen is measured, not remembered. Before recording, re-run the
graph read, the rate measurements and the camera snapshot against the demo cell
and read the values back from the tool; do not re-type them from this file.

## Pre-flight

1. `hermes gateway restart` so the pane runs the current build.
2. Reload desktop plugins, open the ROS 2 pane once, confirm the orphan count.
3. Start the demo nodes in the demo container (see `demo/README.md`).
4. Wait, then confirm the expected topics exist.
5. Turn on Do Not Disturb. Close every window that shows a path, token, IP or
   another project's name.
6. Terminal and app font at **≥15 px**; the video is watched on a phone.
7. Record at 2560×1440, 30 fps, cursor visible.

## Editing rules

- The graph read gets a visible **2× speed badge** with the real number on screen.
  Do not hide the wait — a judge who has run ROS 2 knows introspection is slow, and
  faking speed is the one unforgivable edit.
- Burn in captions: the feed autoplays muted.
- No music. Narration is optional; if used, keep it under 40 words.
- End card holds 6 s so it can be screenshotted as a thumbnail.

## Deliverables

- `ros2-console-demo.mp4` — under 90 s, 1080p, captions burned in
- end-card still for the post thumbnail
- the post text, tagged @NousResearch
- catalog entry with a real `sha` (requires the public push — separate approval)
