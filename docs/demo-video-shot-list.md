# Demo video — shot list, narration, subtitles

A recording under 90 s for the submission post: the argument it makes, the beats,
the spoken lines and the burned-in captions. Filmed against a live board over SSH.

## The one argument (post + end card)

> **Your robot is talking. Hermes tells you who's listening.**

Subline, in the post:

> Hermes connects to a live robot and shows you what's actually flowing — and what
> isn't. Every number on screen is measured, and says how.

Nothing in the video claims anything about other tools. We never measured other
software, so it does not get characterised.

## The register

A person who has spent time with a robot, describing what they can now see.
Competence, not enthusiasm; understatement over superlative. No "seamless",
"supercharge", "empower", "unlock". Every claim is visible in the 86 seconds.

## The filming target

A live board over SSH, its real graph, nothing staged. This file deliberately does
not name the topics that appear on screen: the shot list is public, the robot's
namespace is not. Each beat describes its topic by what it proves, so the recording
can follow the real graph without this document carrying the names.

The pane is left unfiltered, or filtered only if the graph is too dense to read at
phone size — either way the view is declared on screen, not fabricated.

## Beats

| # | Time | On screen | Action | Burned-in caption | Narration |
|---|---|---|---|---|---|
| 1 | 0:00–0:05 | pane, topology already drawn | still | "A live robot, read by the agent" | "This is a live robot. Nothing in this window was drawn by hand." |
| 2 | 0:05–0:11 | a topic with one publisher, no subscribers, latched | hover it | "One publisher. Nobody listening. It still holds a value." | "One publisher, nobody listening — and it still holds its last message." |
| 3 | 0:11–0:18 | pane | click **Refresh** | "One call, the whole graph" | "One call reads the whole graph." |
| 4 | 0:18–0:26 | a live topic, metrics strip, evidence column | hover, let the rate settle | "Counted, not guessed — the rate and its window" | "The rate isn't an assumption. It's counted, and it says over what window." |
| 5 | 0:26–0:33 | one `dead_input` and one `dead_output` topic | hover both | "These two are actually broken" | "These two are broken, and it says so instead of showing a clean picture." |
| 6 | 0:33–0:41 | **Grab frame** → JPEG | click, wait for the frame | "One call, one frame" | "One call gives me a frame, decoded on the robot." |
| 7 | 0:41–0:49 | publish row, an actuation-shaped topic | type, send, refusal appears | "Write path: off by default. Actuators stay off." | "Writing is off by default — and an actuator stays off even when it isn't." |
| 8 | 0:49–0:59 | publish row, a display topic | send → `received · 1 sent · echo …` | "Sent isn't success. Received is." | "When it does write, something has to receive it. Sent isn't success." |
| 9 | 0:59–1:14 | chat with the agent: ask what's broken on this robot, then ask a docs question | answer arrives with the measured numbers; the docs answer cites `repo@commit` | "Ask it what's broken" → "Answers carry the repo and commit they came from" | "You can just ask it. It reads the graph to answer, and when it answers from the docs it tells you which docs." |
| 10 | 1:14–1:20 | LED panel B-roll, phone | cut to panel, art visible | "Same tool, a display that was never a dashboard" | "Same tool, on a display nobody designed as a dashboard." |
| 11 | 1:20–1:26 | end card | hold 6 s | "Your robot is talking. Hermes tells you who's listening." | (silence) |

86 s. If it runs long, cut beat 10 first, then beat 6. **Never cut beats 7–9** — a
refusal, a delivery proof, and the agent answering from the graph are what no other
entry will show.

## Subtitle rules

- The caption column above is the subtitle text, near-verbatim.
- One line, ≤ 45 characters, lower third, 4 % margin.
- Sentence case, no exclamation marks, no drop shadow over dense areas.
- Burned in: the feed autoplays muted, and the subtitles must stand alone.

## Pre-flight

1. `hermes gateway restart`, then quit and reopen the desktop app.
2. Pane host = the board over SSH; filter off; `allow` set to the topic used in beat 8.
3. Confirm the graph has at least one latched topic with no subscribers, one live topic,
   and one orphan of each kind — beats 2, 4 and 5 depend on it.
4. Confirm the actuation-shaped name is refused and the display topic returns `received`.
5. Confirm `ros2_docs` answers and cites a repo and commit (the corpus is 412 markdown
   files with a graphify index, so the answer arrives with `source_repo`/`source_commit`).
6. Do Not Disturb on. Close every window showing a path, a token, or a customer name.
7. Font ≥ 15 px, window 2560×1440, 30 fps, cursor visible.
8. Record **no microphone** — narration is added in the edit.

## Editing (done by the agent, not the operator)

1. VO generated per beat, placed at each beat's start.
2. Captions burned from the same table, timed to the VO.
3. The graph-read wait stays at real speed with a `2×` badge and the measured seconds
   on screen — a judge who has run ROS 2 knows introspection is slow, and faking
   speed is the one unforgivable edit.
4. No music under the narration.
5. End card holds 6 s and is exported again as the post thumbnail.
6. Delivery: `ros2-console-demo.mp4`, H.264, 1080p, under 90 s, captions burned.

## Deliverables

- raw screen capture from the operator, 1440p
- `ros2-console-demo.mp4` from the agent — under 90 s, 1080p, VO + captions
- end-card still for the post thumbnail
- the post text, tagging @NousResearch
- catalog entry with a real `sha` (requires the public push — separate approval)
