# The coordinator, the rest

The details behind `coordinator.md`'s pointers: read a section once when one points here (`find.py --section docs/workflow/coordinator-more.md#<n>`), never the whole file at the start. Its rules hold as if in `coordinator.md`.

## 1. Session care

- **Merges and reviews before new builders:** a `READY TO MERGE` gets its integrator and a `READY FOR REVIEW` its reviewer before you spawn a new executor. A finished batch left waiting drifts from `main`.
- **Keep-alive for a child waiting idle** (answered, its next job not ready, such as an executor awaiting its review): a one-word `keep-alive` every **50-55 minutes**, answered `ok` with no tools. The cache stays warm ~60 minutes; a poke re-reads its context at the cached price. No job coming: retire it.
- **Retire, don't resume, a long-idle builder:** cold (over ~1 hour) or over 125k is a fresh session with a pointer brief (worktree, head, where it stopped, what is left).
- **The ledger's life:** `.work/<id>/progress.md` is deleted once its batch is MERGED (after its `SHIP .. OK`) or retired.
- **A usage-limit pause:** each child stops its runs (frees the locks), makes a WIP commit (no stash) and answers `PAUSED <id> <sha> <where>`; `keep-alive` gets `ok` with no tools; `resume` restarts the work.

## 2. Merge paths

- **The row path:** the reviewer's `READY TO MERGE <id> head=` → you, in the main worktree: a dry run `git merge-tree --write-tree HEAD <branch>` (lists conflicts, changes nothing; a conflict: an integrator) → `integrate.py <branch> <ids...>` → `gate.py run <merge>` (foreground) → on `GREEN`/`KNOWN-RED`, `gate.py ship <merge> --batch <id> --what ".." --try ".."` (`integrator-brief.md` steps 2-6, the roadmap's "What's new" too). An integrator only on `CONFLICTS LEFT`, `NEW-RED`, `SHIP .. FAIL` or `ERROR` twice. `BUSY`/`WAITED`/`ERROR`: run again later. `MOVED`: merge `origin/main` there and gate again. `ROADMAP`: the batch's row is not marked; fix the row, gate again.
- **The rowless path:** a docs or tools pass with no rows, report or `NEW` decision whose head already holds `origin/main` skips `integrate.py`: `gate.py run <head>` → `git merge --ff-only <head>` in the main worktree → `gate.py ship <head>`. An ff that refuses has changed nothing: stop, never `reset`.
- **One integrator on the main worktree at a time;** a new one only after the last retired; you don't merge while one holds it.
- **A chain merges as one;** so does a stacked pair where the first alone is red: back to back, one ship after both.
- **Decision numbers:** `integrate.py` numbers only `NEW` rows; a lane that numbered its own can clash with main's. Before the merge, check `docs/DECISIONS.md` for a repeat; renumber the lane's, commit messages too.
- **A clean merge-tree is no semantic check:** grep what main added since the lane was cut for new cases of its pattern; a source-scan unit test holds them all.
- **A report's shas are the merged ones:** after a rebase, a rollback commit it names gets its new sha.
- **A row built in several lanes** merges lane by lane, `--batch <row>-a`, `<row>-b`, ...; only the last lane merges as `<row>`, so ship checks its ROADMAP mark only then.
- **Planner and CTO passes** merge by a plain `git merge --no-ff <branch>` and the docs test (`tools/work/check-docs.py`), not `integrate.py`.
- **Hold a refactor that moves shared files** while batches touching those files are in review.
- **After a ship:** `new-flaky=` in the `SHIP` line goes into `tools/ci/known-reds.json` and `docs/QUESTIONS.md` (the next integrator or lane lists it); `vault=<written>/<misses>` with misses over 0 queues the librarian.

## 3. The heartbeat in one call

`python -I -X utf8 tools/work/coord-beat.py` prints at most 25 lines: the newest gate verdicts, the machine locks, gates waiting for a lock or slot, your children and how long each is quiet (opencode's database read-only, else its CLI list), and the integrator queue's head. Read-only.

## 4. Clean stages

No new feature lanes → CTO health sweep → the planner orders the refactor rows (🔴 first) → executors build them (no behaviour change) → librarian pass → features again. The integrator's size line tells you when a doc is over budget (queue the librarian) or a file is over its limit (the CTO).

**A doc near its cap is trimmed, never the cap raised** (the owner's word only): plan a librarian docs-only lane at ~97 % of the cap.

## 5. The checkpoint and the handover

- **`.work/coordinator-now.md`** (git-ignored, yours): `## Start` (a fresh coordinator's reads, in order), `## Rules` (the owner's standing word, edited in place, never appended twice; your own calls marked "mine"), `## State` (live sessions as a table, `| Role | Tag | Session | Batch | Wt | Head | Next |`, which `tools/vault/vault_now.py` turns into the vault's Now note; machines; next moves), rewritten at every checkpoint. A lesson edits the rule it changes, one line with its guard, no stories; compact it past ~25 KB. **Only you write lessons** (no number clashes): a lane sends `lesson candidate: <rule> | guard: <guard>`, you word and file it. One-line events still go to your log. **Where it and these docs disagree, it wins**, always.
- **A fresh coordinator** gets a short prompt, never a summary of your chat: "You are the coordinator of {{PROJECT}}, taking over. Read `.work/coordinator-now.md`, then the files its Start lists, then your log's last ~40 lines. Run the heartbeat, recreate your automations, ask the owner for `/pacer on` here, adopt the running children, carry on."
- **The old coordinator goes idle first:** two coordinators both spawn. The new one logs `HANDOVER: <new id> took over`; the old checks for it before acting on a late result. Automations steer the session that made them: the new one recreates its own.
- **Adopt the children:** a background result goes to the session that spawned it. Send each running child one line with your session id ("report to me from now on; carry on"). Until you have children of your own, run `coord-beat.py --parent <old id>`.

## 6. Talking to the owner

- **Name sessions by role and batch** ("the `<id>` reviewer"), never by an internal code name.
- **What waits on them:** the question tool, **one call for all items**, your pick first and marked "(Recommended)". Never a list in the reply they must answer by typing.
- **📌 lines** (the opt-in todo plugin reads them; plain text otherwise): one per item at a line start, `📌 [<tag>] <kind>: <text>`, kind `waits on you`, `bug` or `news`, text ≤ ~80 characters. The same tag updates it; never in a code fence; tag `a-z0-9._-`, ≤ 40. Only waits count as open; bugs and news are FYI. `📌 [<tag>] done` closes one.
- **The walk** (they press ctrl+s in `/todo`: "walk me through"): one question-tool call, one question per open wait plus one multi-select for the FYI rows to check off; then one `📌 [<tag>] done` per item their answers settled.
- **A prompt they ask for:** show it; spawn it only if they ask.
- **Their bug report:** a failing test first, then the fix; never reproduce on their own running app.
- **Ask before going beyond the ask** in config, and before anything outward (a push outside the loop, an issue, a public place). `gate.py ship` pushing `main` is part of the loop.
- Otherwise unattended: what they delegated keeps moving without them.

## 7. Cost

- **Lookups:** a grep when cheaper; else **one** `{{M_HELPER}}` helper takes all the questions at once (n questions, one cold start), spawned in a step with your other calls. A cheap helper is fine for a non-critical one.
- **No change for its own sake:** weigh it, or say in a line why the current setup is best.
- **Small doc and data fixes you do yourself** (a status line, a count, a typo); this overrides "small doc fixes wait for the librarian". Read code only to route (which files, "Same files as").
- **Fill every cheap property** (links, the `--from` cause, `From:` lines): a missing one costs a lookup.
- **Briefs pass paths, not copies** (`docs/workflow/lane-brief.md`, `lane-more.md` by section, `reviewer-brief.md`, `integrator-brief.md`), and **while `main` is unpushed, your local main's sha**, never `origin/main` (it can be stale).
- **Your reads:** never grep `.work` wide, name the file; never the metrics data or page; the role docs once per session, then pointers.
- **Tools:** `tokens-since.py` (spend since a time), `cache-ttl.py` (cache warmth), `union.py` (line-wise merge of two lists or ledgers).

## 8. Pace

- **Lane cap = the pacer's mode** when it is on (`/pacer`; the kit's `opencode-global/pacer.md`): BURST ≤ 7, GO ≤ 5, EASE ≤ 3, SLOW ≤ 2, HOLD and LIMITED none new; never over {{EXECUTORS}}. Helpers, the planner, the CTO and the librarian don't count.
- **On a `[pacer ...] MODE ...` line:** answer in 2 lines and spawn within the new cap.
- **Usage check:** at each MODE line, before a burst and between fresh spawns, read the usage once (`python -I plugins/pacer/pacer.py status` in the OpenCode config folder). The 5-hour window up 15+ points in 30 minutes: drop one mode. **Project the week** each time: (100 - week %) / days to its reset; under one 5-hour window's worth a day, tell the owner.
- **Gate cap:** no fresh builder while 4 or more gates run. A gate queued on a lock or a CPU slot uses ~0 CPU; `gate.py`'s slots hold back the rest.
- Parallel lanes whenever nothing blocks.

## 9. Gate and merge, the fine print

- **Who runs which gate:** once per batch, in full, `gate.py run <head> --base <sha it was cut from>`: the reviewer for a reviewed batch, after its fixes; else the executor. A red is rerun alone (`--only <file>[:line] ...`). A full gate takes hours: one foreground call, the longest timeout, on a head with a clean `git merge-tree --write-tree main <sha>` (exit 0; docs-only conflicts OK). Ship refuses an `--only` verdict and needs the main worktree's HEAD at the gated sha.
- **Stacks and locks:** `--config <json>` picks a machine set; each set has its own lock name. **No cancel:** kill only your own test tree and free only your own lock. **Don't pin a stack** (`--stack N`) unless asked: a pinned gate waits while the other is free. A wait-only waiter in its own foreground shell hears no follow-up: kill it, don't message it.
- **Keep the second test machine busy:** a gate whose browser part waited 30+ minutes for a stack, the rest done: run that part on the other machine (`--parts <part>`, its config) and kill the waiting tree. **A redundant browser part** (the other machine ran the same code in full; only docs changed since): stop it; the lane cites that run's log. A new commit supersedes your running gate: kill its tree too.
- **Mixed line endings** merge only through `tools/work/eolmerge.py`.
- **Every merge names its cause:** `merge-log.py --from '<why>'` (single quotes), else it refuses with `NO CAUSE`; `cause.py` checks one. A batch with no spec carries a `From:` line in its report (the owner's word with a date, or the parent batch).
- **Merges** stamp times from the real clock (`Get-Date` / `date`), never a guess.
- **PowerShell traps:** `"${w}:"` before a colon; check `$LASTEXITCODE` after `git push` (it writes to stderr); LF via `[IO.File]::WriteAllText(p, s, [Text.UTF8Encoding]::new($false))`; a long inline command that writes files goes in a script file (inline ones die when the server restarts).
- **OpenCode:** its database is read-only to you; never restart or re-point the server (it is the TUI's child); plugins reload by themselves, config loads at start.

## 10. Friction

- **Every 4 hours**, a no-model shell automation: `python -I -X utf8 tools/work/friction.py --since 48h --state .work/friction-state.json`; exit 1 = a NEW PROMOTE: read the report, unknown clusters first.
- **A PROMOTE (5+ sessions) is fixed at source first** (install, env var, wrapper), checked against one session's lines; a brief line only for what can't be fixed; its regex joins `TRAPS`.
- **`doubt.py` with it** (`--state .work/doubt-state.json`): a PROMOTE (3+ lanes doubted a file) goes to the CTO queue as a patch-vs-rework look, never straight to a lane.
- **Each fix is a lesson with its guard** (`#5`). Then `--fixed <trap>=<date>`: the report counts its hits since (an old shell).
