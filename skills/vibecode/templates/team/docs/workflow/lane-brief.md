# Brief for an executor (a worktree builder)

You build batches of {{PROJECT}} in a **git worktree** of your own. Other executors work in other worktrees; the integrator owns `main`. The run is unattended: never wait for a person.

## Read first

**Start reads** (at most 60 KB): `AGENTS.md`, `docs/workflow/lane-brief.md`, `docs/HANDOVER.md#3`, `docs/ARCHITECTURE.md`.
All from your own worktree (this brief too, never the main checkout's copy). **For the job:** the batch's spec and only the specs it depends on; the one `docs/architecture/<area>.md` of your area (the map: look things up there, don't explore). `docs/DECISIONS.md` and `docs/QUESTIONS.md` only by grep.

A spec's `## How to build` (the CTO's) is followed; a deviation is one line in the report; a design question it does not answer goes to the coordinator for the CTO.

## Your worktree

- Your first branch is checked out. Each later batch of your queue: `git checkout -b <next-id>` from the branch you just finished (a lane's batches build on each other), unless the coordinator names another base.
- Never check out, merge into or push `main`. Never `git pull`/`push`. `git fetch` is fine.
- Shared dependency folders are links to the main tree's: **never install into them** (`npm ci`, `npm install`, `pip install` into the shared venv). A batch that needs a new dependency: build the rest, skip that part, say so. If one looks broken, stop and tell the coordinator in one line.
- Commands that wait long: start them detached with a log, then wait in slices of at most 8 minutes. Read logs only through a filter.
<!-- if:e2e -->- **Every e2e or stack command runs under the lock:** `pwsh -File tools/work/lock.ps1 -Name e2e -Who <id> -Cmd "<bring the stack up from this tree>; <runner> <named files>"`. Always name the files (a runner given no files runs everything). Keep each locked run short.
<!-- endif --><!-- if:remote -->- **e2e files run on machine B by default:** `pwsh -File tools/work/remote-run.ps1 -Id <id> -Cmd "<bash: stack up; runner named files>"` tests your **committed** HEAD there. <!-- if:perf -->Performance runs stay on machine A. <!-- endif -->A red seen only on B (fonts, keys, timing): rerun it alone on A before you call it yours. If B's lock is held for over 10 minutes, use A's.
<!-- endif -->

## The loop (HANDOVER section 3), with these rules

1. Plan in `docs/plans/<id>.md`; ledger in `.work/<id>/progress.md` (git-ignored) with a `NOW:` line you keep current (the coordinator reads it to see you are alive). After any cutoff or usage limit, resume from it and `git status`; check for a half-done edit and a stale lock first.
2. One commit per task; logic test-first, seen red; every number a named constant.
3. Cheap checks only: types, unit, API if touched.
4. Docs, `git mv` the spec to `done/`, the report in `.work/<id>/report.md` (HANDOVER section 3, steps 4 and 5), delete the plan, commit. **Don't touch** `docs/BACKLOG.md`, `docs/HANDOVER.md` section 1 or `docs/ROADMAP.md`: the integrator does.
<!-- if:reviewers -->4b. **Stop for review** ({{REVIEW_STEP}}): answer `READY FOR REVIEW <id> base=<sha you cut from> head=<sha>`. Run no e2e and no performance suite for it. **Don't wait idle:** go on with your next batch up to its own READY. **After your last READY, end your session.** The reviewer fixes its own minors, runs the gate and answers `READY TO MERGE`; you are not resumed for that. **A real blocker or major comes back to you** in `.work/<id>/review.md` (the highest round N): fix it, or answer it under "Builder's answers", then READY FOR REVIEW again; a fresh reviewer checks only your delta.
<!-- endif -->5. **The gate** (for a batch with no review<!-- if:reviewers -->; a reviewed batch's is the reviewer's<!-- endif -->): full unit + API; then the e2e files of every area your diff touches<!-- if:perf -->, the performance suite<!-- endif -->; then the diff against "What must be true", rule by rule. A red: run it alone; 3 of 3 alone is a flake (note it in `docs/QUESTIONS.md`); else it is yours. If a red forces a major change, stop and tell the coordinator in one line. Fill the report's Gate part.
6. With no review, answer `DONE <id> head=<sha>`: what changed (2-3 bullets), calls taken, skipped, gate numbers, reds and whether `QUESTIONS.md` lists them. At most 8 lines.
