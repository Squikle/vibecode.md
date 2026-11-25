# The coordinator, the rest

The details behind `docs/workflow/coordinator.md`'s pointers. Read a section when it points here (`python tools/work/find.py --section docs/workflow/coordinator-more.md#<n>`), once; never this whole file at the start. Its rules hold as if they were in `coordinator.md`.

## 1. Session care

- **Merges and reviews before new builders:** a `READY TO MERGE` gets its integrator and a `READY FOR REVIEW` its reviewer before you spawn a new executor. A finished batch left waiting drifts from `main`; a new one only adds load.
- **Keep-alive for a child waiting idle** (its answer given, its next job not ready yet, such as an executor whose review may come back): a one-word `keep-alive` every **50-55 minutes**, answered `ok` with no tools. In the source project the cache stayed warm through gaps of 5-60 minutes (7-10 % cold) and was always cold after 60; one poke re-reads the child's context at the cached price. No job coming: retire it.
- **Retire, don't resume, a long-idle builder:** cold (over ~1 hour) or over 125k is a fresh session with a pointer brief (worktree, head, where it stopped, what is left), never a resume of the old chat.
- **The ledger's life:** `.work/<id>/progress.md` is deleted once its batch is MERGED (after its `SHIP .. OK`) or retired. A stale one misleads the next resume.
- **A usage-limit pause:** each child stops its runs (frees the locks), makes a WIP commit (no stash) and answers `PAUSED <id> <sha> <where>`; `keep-alive` gets `ok` with no tools; `resume` restarts the work.

## 2. Merge paths

- **The row path:** the reviewer's `READY TO MERGE <id> head=` → you, in the main worktree: a dry run `git merge-tree --write-tree HEAD <branch>` (lists conflicts, changes nothing; a conflict: an integrator) → `integrate.py <branch> <ids...>` → `gate.py run <merge>` (foreground) → on `GREEN`/`KNOWN-RED`, `gate.py ship <merge> --batch <id> --what ".." --try ".."` (`integrator-brief.md` steps 2-6, the roadmap's "What's new" too). An integrator only on `CONFLICTS LEFT`, `NEW-RED`, `SHIP .. FAIL` or `ERROR` twice. `BUSY`/`WAITED`/`ERROR`: run again later. `MOVED`: merge `origin/main` there and gate again. `ROADMAP`: the batch's row is not marked; fix the row, gate again.
- **The rowless path:** a docs or tools pass with no rows, report or `NEW` decision whose head already holds `origin/main` skips `integrate.py`: `gate.py run <head>` → `git merge --ff-only <head>` in the main worktree → `gate.py ship <head>`. Ship needs the main worktree at the gated sha (`FAIL tree`). An ff that refuses has changed nothing: stop, never `reset`.
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

`python -I -X utf8 tools/work/coord-beat.py` prints at most 25 lines: the newest gate verdicts, the machine locks, the gates waiting for a lock or slot, your children with how long each has been quiet (opencode's database read-only, else its CLI list), and the integrator queue's head. Read-only.

## 4. Clean stages

No new feature lanes → CTO health sweep → the planner orders the refactor rows (🔴 first) → executors build them (no behaviour change) → librarian pass → features again. The integrator's size line tells you when a doc is over budget (queue the librarian) or a file is over its limit (the CTO).

**A doc near its cap is trimmed, never the cap raised** (that is the owner's word only): plan a librarian docs-only lane at ~97 % of the cap.

## 5. The checkpoint and the handover

- **`.work/coordinator-now.md`** (git-ignored), yours to keep: `## Start` (what a fresh coordinator reads, in order), `## Rules` (the owner's standing word, edited in place, never appended twice; your own calls marked "mine"), `## State` (live sessions as a table, `| Role | Tag | Session | Batch | Next |`, which `tools/vault/vault_now.py` turns into the vault's Now note; machines; next moves), rewritten at every checkpoint. One-line events still go to your log. **Where it and these docs disagree, it wins**, always.
- **A fresh coordinator** gets a short prompt, never a summary of your chat: "You are the coordinator of {{PROJECT}}, taking over. Read `.work/coordinator-now.md`, then the files its Start lists, then your log's last ~40 lines. Run the heartbeat, ask the owner for `/pacer on` in this session, adopt the running children, carry on with the next moves." When the owner asks for that prompt, show it; they start the session.
- **The old coordinator goes idle first:** two coordinators both spawn.
- **Adopt the children:** a background result goes to the session that spawned it. Send each running child one line with your session id ("report to me from now on; carry on"). Until you have children of your own, run `coord-beat.py --parent <old id>`.

## 6. Talking to the owner

- **Name sessions by role and batch** ("the `<id>` reviewer"), never by an internal code name.
- **What waits on them:** the question tool, **one call for all items**, your pick first and marked "(Recommended)". Never a list in the reply they must answer by typing.
- **📌 lines** (the opt-in todo plugin reads them; plain text otherwise): one per item at a line start, `📌 [<tag>] <kind>: <text>`, kind `waits on you`, `bug` or `news`, text ≤ ~80 characters (the details go in the reply). The same tag updates it; never in a code fence; tag `a-z0-9._-`, ≤ 40. Only waits count as open; bugs and news are FYI. `📌 [<tag>] done` closes one.
- **The walk** (they press ctrl+s in `/todo`: "walk me through"): one question-tool call, one question per open wait plus one multi-select for the FYI rows to check off; then one `📌 [<tag>] done` per item their answers settled.
- **A prompt they ask for:** show it; spawn it only if they ask.
- **Their bug report:** a failing test first, then the fix; never reproduce on their own running app.
- **Ask before going beyond the ask** in config, and before anything outward (a push outside the loop, an issue, a public place). `gate.py ship` pushing `main` is part of the loop.
- Otherwise unattended: what they delegated keeps moving without them.

## 7. Cost

- **One step, many calls:** independent calls go in one step; a sequence you can plan is one script with its branches written in. Every step re-sends your whole context.
- **Lookups:** a grep when it is cheaper than a helper; else **one** `{{M_HELPER}}` helper takes all the questions at once (n questions, one cold start), spawned in a step with your other calls. A cheap helper for a non-critical question is fine.
- **No change for its own sake:** weigh a change, or say in a line why the current setup is already the best.
- **Small doc and data fixes you do yourself** (a status line, a count, a typo); this overrides "small doc fixes wait for the librarian". Read code only to route (which files, "Same files as").
- **Fill every property that is cheap to fill:** links, the `--from` cause, `From:` lines. A missing one costs a lookup later.
- **Briefs pass paths, not copies** (`docs/workflow/lane-brief.md`, `lane-more.md` by section, `reviewer-brief.md`, `integrator-brief.md`), and **while `main` is unpushed, your local main's sha**, never `origin/main`: a stale origin made a reviewer's merge a no-op.
- **Your reads:** never grep `.work` wide, name the file; a shell's `.out` by tail or a `FAIL|ERROR` grep; the role docs once per session, then pointers.
- **Audits unasked**, and verify a smell against the session itself (`session-audit.py`) before fixing it. Tools: `tokens-since.py` (what was spent since a time), `cache-ttl.py` (how long the cache stayed warm), `union.py` (merge two lists or ledgers line-wise).

## 8. Pace

- **Lane cap = the pacer's mode** when it is on (`/pacer`; the kit's `opencode-global/pacer.md`): BURST ≤ 7, GO ≤ 5, EASE ≤ 3, SLOW ≤ 2, HOLD and LIMITED none new; never over {{EXECUTORS}}. Helpers, the planner, the CTO and the librarian don't count.
- **On a `[pacer ...] MODE ...` line:** answer in 2 lines and spawn within the new cap.
- **Usage check:** at each MODE line, before a burst and between fresh spawns, read the usage once (`python -I plugins/pacer/pacer.py status` in the OpenCode config folder). The 5-hour window up more than ~15 points in 30 minutes: drop one mode.
- **Gate cap:** no fresh builder while 4 or more gates run. A gate queued on a lock or a CPU slot uses ~0 CPU; `gate.py`'s slots hold back the rest.
- **Heartbeat** (`#3`); **every third beat** a peek sweep: `session-audit.py --peek` (one line per running child).
- Parallel lanes whenever nothing blocks.

## 9. Gate and merge, the fine print

- **Who runs which gate:** once per batch, in full, `gate.py run <head> --base <sha it was cut from>`: the reviewer for a reviewed batch, after its fixes; else the executor. A red is rerun alone (`--only <file>[:line] ...`). A full gate can take hours: one foreground call, the longest timeout. Ship refuses an `--only` verdict and needs the main worktree's HEAD at the gated sha.
- **Stacks and locks:** `--config <json>` picks a machine set; each set has its own lock name. **No cancel:** kill only your own test tree and free only your own lock.
- **Keep the second test machine busy:** a gate whose browser part has waited 30+ minutes for a stack, its other parts done: run that part on the other machine (`--parts <part>` with that machine's config) and kill the waiting gate's tree. **A redundant browser part:** a full run on the other machine of the same code exists (only docs changed since): stop it; the lane gates with the other parts and cites that run's log. A new commit supersedes your own running gate: kill its tree too.
- **Mixed line endings** merge only through `tools/work/eolmerge.py`.
- **Every merge names its cause:** `merge-log.py --from '<why>'` (single quotes), else it refuses with `NO CAUSE`; `cause.py` checks one. A batch with no spec carries a `From:` line in its report (the owner's word with a date, or the parent batch).
- **Merges** stamp times from the real clock (`Get-Date` / `date`), never a guess.
- **PowerShell traps:** `"${w}:"` before a colon; `git push` writes to stderr, so check `$LASTEXITCODE`; Python as `python -I -X utf8`; LF files via `[IO.File]::WriteAllText(p, s, [Text.UTF8Encoding]::new($false))`. A long inline command that writes files goes in a script file you then run: inline ones die when the server restarts mid-call.
- **OpenCode:** its database is read-only to you; never restart or re-point the server (it is the TUI's child); plugins reload by themselves, config loads at start.
