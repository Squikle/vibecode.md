# The coordinator, the rest

The details behind `docs/workflow/coordinator.md`'s pointers. Read a section when it points here (`python tools/work/find.py --section docs/workflow/coordinator-more.md#<n>`), once; never this whole file at the start. Its rules hold as if they were in `coordinator.md`.

## 1. Session care

- **Merges and reviews before new builders:** a `READY TO MERGE` gets its integrator and a `READY FOR REVIEW` its reviewer before you spawn a new executor. A finished batch left waiting drifts from `main`; a new one only adds load.
- **Keep-alive for a child waiting idle** (its answer given, its next job not ready yet, such as an executor whose review may come back): a one-word `keep-alive` every **50-55 minutes**, answered `ok` with no tools. In the source project the cache stayed warm through gaps of 5-60 minutes (7-10 % cold) and was always cold after 60; one poke re-reads the child's context at the cached price. A child with no job coming: retire it instead.
- **Retire, don't resume, a long-idle builder:** cold (over ~1 hour) or over 125k is a fresh session with a pointer brief (worktree, head, where it stopped, what is left), never a resume of the old chat.
- **The ledger's life:** `.work/<id>/progress.md` is deleted once its batch is MERGED (after its `SHIP .. OK`) or retired. A stale one misleads the next resume.
- **A usage-limit pause:** each child stops its runs (frees the locks), makes a WIP commit (no stash) and answers `PAUSED <id> <sha> <where>`; `keep-alive` gets `ok` with no tools; `resume` restarts the work.

## 2. Merge paths

- **The row path:** the reviewer's `READY TO MERGE <id> head=` → the integrator: `integrate.py <branch> <ids...>` in the main worktree → `gate.py run <merge>` → on `GREEN`/`KNOWN-RED`, `gate.py ship <merge> --batch <id> --what ".." --try ".."` (`docs/workflow/integrator-brief.md`). `BUSY`/`ERROR`: run again later. `MOVED`: merge `origin/main` there and gate again. `ROADMAP`: the batch's row is not marked; fix the row, gate again.
- **The rowless path:** a docs or tools pass with no rows, report or `NEW` decision whose head already holds `origin/main` skips `integrate.py`: `gate.py run <head>` → `git merge --ff-only <head>` in the main worktree → `gate.py ship <head>`. Ship needs the main worktree at the gated sha (`FAIL tree`). An ff that refuses has changed nothing: stop, never `reset`.
- **One integrator on the main worktree at a time;** a new one only after the last retired.
- **A chain merges as one;** so does a stacked pair where the first alone is red: back to back, one ship after both.
- **Hold a refactor that moves shared files** while batches touching those files are in review.
- **After a ship:** `new-flaky=` in the `SHIP` line goes into `tools/ci/known-reds.json` and `docs/QUESTIONS.md` (the next integrator or lane lists it); `vault=<written>/<misses>` with misses over 0 queues the librarian.

## 3. The heartbeat in one call

`python -I -X utf8 tools/work/coord-beat.py` prints at most 25 lines: the newest gate verdicts, the machine locks, your children with how long each has been quiet (opencode's database read-only, else its CLI list), and the integrator queue's head. Read-only; one call instead of five.

## 4. Clean stages

No new feature lanes → CTO health sweep → the planner orders the refactor rows (🔴 first) → executors build them (no behaviour change) → librarian pass → features again. The integrator's size line tells you when a doc is over budget (queue the librarian) or a file is over its limit (the CTO).
