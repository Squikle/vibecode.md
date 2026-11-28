# The vault: append-only logs as a zero-LLM database

**TL;DR: agents append one line per event to plain files they write anyway; a script (no model) turns those lines and
the docs into an Obsidian vault with one note per event and table views over the notes' properties. Building it costs
0 model tokens; writing it costs only each line's output tokens; no agent ever reads it. On by default.**

- Code and how-to for a deployed project: `templates/always/tools/vault/README.md` (log format, views, refresh).
- Rows in [OPTIMIZATIONS.md](../../OPTIMIZATIONS.md): #28 (the vault), #54 (edits merged, Now), #59 (checked before a
  merge), #70 (Now's signs of life), #71 (logs, views, gate history, on by default).

## Why it is almost free

| Step | Who pays | Cost |
|---|---|---|
| **Write** an event | the agent that did the work, in the same step | the line's output tokens: ~100 for a ~400-character line (ESTIMATE: ~4 characters per token) |
| **Write** a machine event (a gate run, a red test) | a script | 0 |
| **Build** the vault | `vaultify.py`, on each merge (git hook) and every ~10 min (shell automation) | 0 model tokens; one measured run: 757 events from ~450 KB of a real ledger in ~1 s |
| **Read** it | the owner, in Obsidian | 0 model tokens: no agent reads the vault |

**Where "almost free" breaks** (measured in the source project, so each has a rule):

1. **An append as its own turn** re-sends the whole context (~80k tokens for a coordinator) for one line. Rule: append
   in the step that did the work, never as a turn of its own.
2. **A log read at every start.** A log that agents read stops being free: a fresh coordinator reading its ledger's last
   ~40 lines pays ~4k tokens (ESTIMATE). Rule: logs are written by agents and read by the vault; agents read state
   files (the checkpoint), which stay compacted.
3. **A state file that grows like a log.** The source project's checkpoint reached 84 KB (~21k tokens per fresh
   coordinator, ESTIMATE) against its ~30 KB rule, and its lessons 67 KB. Rule: a state file is compacted (the
   coordinator's past ~25 KB, OPTIMIZATIONS #67); the story goes to an append-only log, which no agent reads.

For scale (UNVERIFIED, one project, ~64 hours): the coordinator wrote ~240 ledger lines a day, ~25k output tokens a day.
Without the vault, "which tests went red on the Mac this week" means an agent opening the gate's JSON results one by
one (the source project had ~490 of them).

## What is logged and who writes it

| File | Written by | Append-only? | In the vault |
|---|---|---|---|
| `docs/MERGES.md` | `tools/work/merge-log.py`, at each merge | ✅ | `merges/` notes; batch status `merged` |
| `docs/DECISIONS.md` | the product lead, the architect | ✅ numbered; the librarian cuts a replaced one to a pointer | `decisions/` notes |
| `docs/BACKLOG.md`, `docs/QUESTIONS.md` | the product lead and the coordinator (order); anyone (questions) | ❌ tables kept current | `batches/`, `questions/` notes |
| `docs/HANDOVER.md` | the sessions that build; the librarian trims it | ❌ the state, not the story; capped at 30 KB | mirrored under `docs/` |
| `.work/coordinator-log.md` (the ledger) | the coordinator: each spawn, verdict, merge, call | ✅ | `log/coordinator-log/` |
| `.work/log/gate.md` | `tools/ci/gate.py run`: a `GATE` line per run, `RED` / `FLAKY` per new red or flaky test | ✅ | `log/gate/`; views "Reds by test", "Flaky by test" |
| `.work/log/<name>.md` | anything local: a script, an agent | ✅ | `log/<name>/` |
| `docs/log/**/*.md` | anything the team shares (research notes, deploys) | ✅, committed; a bad line stops the merge | `log/<name>/` |
| `.work/coordinator-now.md` (the checkpoint) | the coordinator | ❌ rewritten, compacted | `Now.md` (`vault_now.py`, every 2 min) |

A log line: `[- ]YYYY-MM-DD [hh:mm] TYPE text key=value key="a value" #tag`, with an optional indented body. TYPE is a
verb or a kind (SPAWN, VERDICT, GATE, SOURCE), never a name; `key=value` becomes a typed property; a backticked batch
id links its batch note. The full grammar and what counts as a miss: the vault README, Logs.

## Open it

1. Deploy as usual: `deploy.py` copies `tools/vault/` and, in a git repository, installs its hook (`hook.py install`).
2. Commit on `main` once: the hook builds `<main checkout>/.work/vault`.
3. Obsidian 1.9 or newer: **Open folder as vault** → that folder. Start at `index.md`; the views are the `*.base` files.
4. Optional: two no-model shell automations, `vaultify.py --if-changed` every ~10 min (new log lines) and
   `vault_now.py --if-changed` every 2 min (Now).

## Your edits

You may edit any note or view. The next build merges the new version into yours 3-way (`vault_merge.py`, against the
copy it last wrote, kept in `.vaultify/base/`). Both sides changed the same lines: a **conflict**, your file kept, the
new one parked in `.vaultify/conflicts/`, the hub warns; `vaultify.py --resolve <path>` takes it back. A `.base` file
you make yourself is never touched.

## A project whose events are not merges

Research, writing, ops, data, personal planning: the coordinator (solo: the product lead) notices, and once the owner's
needs are clear proposes ONE log: its file, its TYPE words, the keys each carries, who appends, and the views that
answer the owner's questions. Two examples (in the vault README):

```
- 2026-11-26 10:40 CLAIM dense beats sparse on long queries from=survey confidence=0.7
- 2026-11-26 15:30 INCIDENT api 502s after the deploy severity=2 minutes=25 #rollback
```

## Is it a database?

| Can | Can't |
|---|---|
| filter, group and sort by any property (Bases tables; Dataview queries) | joins beyond links, aggregates past what Bases or Dataview offer |
| typed numbers and booleans; ISO dates sort as strings | edit the source through the vault: a vault edit stays in the vault |
| link an event to its batch, a batch to its merge | keep every line of a long log: the newest 2000 per log become notes (`LOG_KEEP`), the index counts all |

Good enough for "what happened, when, how often, on which machine". Not a replacement for a metrics store.

## Ideas, ranked: disk-only tracking with no model

Ranked by value for the owner per line of code. Generic = fits any project the skill deploys.

| # | Idea | Source data | Log line | Generic? | Status |
|---|---|---|---|---|---|
| 1 | Gate history: verdict and time per run, reds per test and machine, flaky tests | the gate's result JSON | `GATE verdict= sha= seconds=`, `RED test= part= machine=`, `FLAKY test=` | ✅ | ✅ shipped (#71) |
| 2 | Coordinator ledger as events | `.work/coordinator-log.md` | its own lines, TYPE a verb | ✅ | ✅ shipped (#71); the line shape is in the coordinator brief |
| 3 | Lock waits per lock and part | the gate's `waiting for` lines in `gate.log` | `LOCK lock= part= waited=` | ✅ | planned: a few lines in `gate.py` |
| 4 | Reviewer verdicts per batch and round | review files' `VERDICT:` lines | `REVIEW batch= verdict= round= blockers= majors=` | ✅ | planned: `integrate.py` appends at merge |
| 5 | ETA against actual per batch | the coordinator's spawn line and the merge | `SPAWN ... eta=`, then the merge's time | ✅ | planned: needs `eta=` on spawn lines (docs) and a view |
| 6 | Spawns, model and tokens per lane | the opt-in metrics JSONL (#63) | `USAGE lane= model= tokens=` per day | ⚠️ opt-in only | planned |
| 7 | Flake rate per test (reds over runs) | row 1's lines | derived: needs a run count per test | ✅ | planned: a counted table on the log's index note |
| 8 | Deploys and their time | autodeploy (#43) | `DEPLOY sha= seconds= ok=` | ⚠️ opt-in only | planned |
| 9 | Session audit smells | `tools/work/session-audit.py` | `AUDIT role= smell= tokens=` | ✅ | planned |
| 10 | End-to-end reds by browser and machine | Playwright summaries on a second machine | covered by row 1's `machine=`; per-browser needs the runner's report | ❌ project-specific | left to the project |
