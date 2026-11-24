# How the pacer works

The pacer does two jobs. It **batches** the wake-ups that arrive in a busy session, so several of them cost one model
call. It also **paces** sessions against the plan's 5-hour and 7-day usage limits. Everything is off until you turn
it on with `/pacer`.

## The parts

| Part | Files | What it does |
|---|---|---|
| server plugin | `index.js` | holds and flushes wake-ups, keeps the state, answers the pop-up over RPC |
| TUI plugin | `tui.js`, `tui-core.js`, `footer.jsx`, `rpc.js` | the `/pacer` pop-up and one footer line in the prompt |
| usage reader | `pacer.py` | reads the plan's usage every 5 min, only while pacing is on |

- All of them live in one folder, `plugins/pacer/` in the OpenCode config folder. No `cli.json` line is needed.
- The pacer runs only while OpenCode runs. It has no service and no scheduler of its own.
- The server reloads `index.js` by itself. A change to the TUI files may need a TUI restart.
- `pacer.py` needs python (`python3` off Windows; `$PACER_PYTHON` overrides). It reads the Claude Code token
  read-only and never refreshes or prints it. The usage meter is its only outside call.

## Settings

- `toggles.json` in the data folder (`$PACER_DATA`, else `~/.local/share/pacer`). Only the server writes it; the
  pop-up sends each change over RPC.
- **Per-session slots.** A slot ties pace and batch settings to one session. `/pacer on` = pace on in this
  session's slot (`main` when it has none); `/pacer batch on` = batching on; `/pacer move` takes a slot over in
  another session; `/pacer off all` clears everything.
- **Week and global** are plain toggles, both off by default. Week off = only the 5-hour window counts.
- `/pacer status` shows the slots, the usage, and per session: runs held, flushes, model calls saved.

## Batching

When a run is about to start and the batch window is still open, the run stops before any model call, so it costs
0 tokens. The message itself stays in the session; nothing is dropped. A 1 s tick watches the held items. When the
window closes, it posts one flush note, and the model reads every held item in one turn. N wake-ups = 1 model call.

**Where the window counts from** (`/pacer batch from arrival|reply`, or the pop-up's `from` row; `period` is 60 s
by default, 15..600):

| Mode | The window | After a long quiet |
|---|---|---|
| **arrival** (default) | opens at the first held item and lasts one period; whatever comes in it joins the same flush. It never restarts, so nothing waits longer than one period | a new item still waits one period |
| **reply** | counts from the later of the last turn's start and end | a new item passes at once |

Example, period 60 s:

| Time | Event | arrival | reply |
|---|---|---|---|
| 12:00:00 | the session's turn ends | | |
| 12:00:20 | child A's result arrives | held, window to 12:01:20 | held (20 s < 60 s) |
| 12:00:50 | child B's result arrives | held | held |
| 12:01:00 | | | **one** turn reads A and B |
| 12:01:20 | | **one** turn reads A and B | |

- **Always passes:** a flush note, a `/now` prompt, and OpenCode's auto-compaction continuation (that is the running
  turn going on, not a new wake).
- **`/now <prompt>`** (TUI): sends the prompt past the wait; anything held rides along in the same turn. Bare
  `/now` = flush. It sets a one-shot pass (`/pacer now`, 15 s) and then sends the prompt. While a turn runs, the
  prompt steers into it anyway.
- `batch me` (on in a new slot) makes your typed prompts wait too. `/pacer batch me off` lets them through at once.
- `/pacer flush` sends what is held now.
- Heartbeats and automations are just more held items.
- Keep the period at 240 s or less: the prompt cache may expire at 5 min. The pop-up warns at 300 and 600.

## Restart safety

- Stopping OpenCode mid-batch loses nothing. The held messages live in the session, the state is on disk, and the
  next start flushes whatever is due.
- Quitting only the TUI changes nothing: the server keeps holding and flushing.

## Pacing

`pacer.py` compares each window's usage with a straight line from its start to its reset and picks a mode. The mode
says how many lanes (parallel workers) to run.

| Mode | Lanes | When |
|---|---|---|
| GO | 5 | both windows on pace |
| EASE | 3 | a little ahead of the line |
| SLOW | 2 | well ahead of the line |
| HOLD | 0 new | 5 h at 90% or more, over 1 h left |
| BURST | 7 | under 1 h to the 5 h reset, budget left |
| LIMITED | 0 | a limit is hit (a week over 100% counts even with week off) |

- **Advice:** a passive note in the paced session when the mode changes. It wakes the session only when the new mode
  allows more lanes and the session has been idle for 10 min or more (in HOLD: after 50 min).
- **Advisory only.** It never stops or kills a session; the coordinator reads the lane cap when it starts lanes.
- **Global on** also stops runs before their model call in sessions outside every paced slot's tree, while the mode
  is HOLD or LIMITED. `/pacer global off` overrides.

## Troubleshooting

| You see | Meaning |
|---|---|
| empty footer | this session has no slot |
| toast "pacer timer off" | the footer failed to load; the pop-up still works |
| `/pacer flush` says "restart OpenCode" | standby: two builds are loaded, the older one still runs; restart to finish |
| something else | `plugin.log` in the data folder (rotates at 1 MB) |

- `opencode run "/pacer ..."` does not run the command, and the queue-submit key refuses `/pacer`. Use the pop-up.

## Tests

From the kit folder: `node --test tests/pacer/core.test.mjs tests/pacer/plugin.test.mjs tests/pacer/tui.test.mjs`
and `python -I tests/pacer/pacer_cases.py`. `node_modules/@opencode/plugin` there is a test stub only; OpenCode
provides the real module at runtime.
