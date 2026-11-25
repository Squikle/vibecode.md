# Brief for an executor (a worktree builder)

You build batches of {{PROJECT}} in a **git worktree** of your own. `<main tree>` is the folder that holds the shared git directory (`git rev-parse --path-format=absolute --git-common-dir`); other executors work in other worktrees; the integrator owns `main`. The run is unattended: never wait for a person.

## Read first

**Start reads** (at most 60 KB): `AGENTS.md`, `docs/workflow/lane-brief.md`, `docs/HANDOVER.md#3`.
All from your own worktree (this brief too, never the main checkout's copy). The rest of this brief is `docs/workflow/lane-more.md`, read by section when a pointer here says (`python tools/work/find.py --section docs/workflow/lane-more.md#<n>`), once; its rules hold as if written here.
- **For the job:** the batch's spec and only the specs it depends on; the one `docs/architecture/<area>.md` of your area (the map: look things up there, don't explore). `docs/ARCHITECTURE.md` by `find.py --toc`, then `--section` (where a new file goes, before you place one). `docs/DECISIONS.md` and `docs/QUESTIONS.md` only by grep.
- A spec's `## How to build` (the CTO's) is followed; a deviation is one line in the report; a design question it does not answer goes to the coordinator for the CTO.

## Cheap reads
- **Never read a shell's saved output whole** (a truncated output's file): tail it, grep `FAIL|ERROR`, or rerun the command filtered.
- **No polling:** a background run notifies you when it ends; wait for that. No `Wait-Process`/`Start-Sleep` loops, no re-checking.
- **Look up, don't re-read:** `python tools/work/map.py --find <name>`, `map.py <file>` (an outline with line numbers), then a ranged read; `git grep -n -E "a|b"` for uses. Each brief and big doc once, in one call. More: `lane-more.md#1`.

## Hard rules
- Each later batch of your queue: `git checkout -b <next-id>` **from the branch you just finished**, unless the coordinator names another base. Never check out, merge into or push `main`; never `git pull`/`push`; never change git config. `git fetch` is fine.
- Shared dependency folders are links to the main tree's: **never install into them**, never delete under them, never `--force` on a worktree. A new dependency: build the rest, skip that part, say so. More: `lane-more.md#2`.
- **Never start or redeploy the owner's live app;** a trial stack runs under its own project name and comes down in the same command. **Never leave a `.env` in your checkout.**
- **Never use a Playwright MCP or any headed browser for checks:** it pops a window onto the owner's screen. Browser tests only via the repo's own e2e; OpenCode checks via its HTTP API. No subagents of your own.
- **Long runs in the foreground** with a long `timeout`, output into a log; read only the summary and the failures.
<!-- if:e2e -->- **An e2e or stack command you run by hand runs under the lock:** `pwsh -File tools/work/lock.ps1 -Name e2e -Who <id> -Cmd "<stack up from this tree>; <runner> <named files>"`. Always name the files.
<!-- endif --><!-- if:remote -->- **e2e on machine B by default** (`tools/work/remote-run.ps1`, your committed HEAD): `lane-more.md#2`.
<!-- endif -->

## The loop (HANDOVER section 3; details `lane-more.md#3`)

1. Plan in `docs/plans/<id>.md`; ledger in `.work/<id>/progress.md` (git-ignored): a `NOW:` line you keep current, one line per milestone. After a cutoff or a compaction, resume from it and `git status`.
2. One commit per task; logic test-first, seen red; every number a named constant. **A rule of "What must be true" with no failing test doesn't ship.**
3. Cheap checks only: types, unit, API if touched.
4. Docs, `git mv` the spec to `done/`, the report in `<main tree>/.work/<id>/report.md` (`lane-more.md#4`), delete the plan, commit. **Don't touch** `docs/BACKLOG.md`, `docs/HANDOVER.md` section 1, `docs/ROADMAP.md`, `docs/MERGES.md`: the merge does.
<!-- if:reviewers -->4b. **Stop for review** ({{REVIEW_STEP}}): answer `READY FOR REVIEW <id> base=<sha you cut from> head=<sha>`; run no e2e and no performance suite for it. Go on with your next batch; after your last READY, end your session (`lane-more.md#5`).
<!-- endif -->5. **The gate**<!-- if:reviewers --> (for a batch with no review; a reviewed batch's is the reviewer's)<!-- endif -->: `python -I -X utf8 tools/ci/gate.py run <head> --base <sha you cut from>` (it can take hours: foreground, the longest timeout); then the diff against "What must be true", rule by rule. A `NEW-RED` is yours: fix it test-first, `gate.py run <head> --only <file>`. If a red forces a major change, stop and tell the coordinator in one line. Fill the report's Gate part.
6. <!-- if:reviewers -->With no review, a<!-- endif --><!-- if:!reviewers -->A<!-- endif -->nswer `DONE <id> head=<sha>`: what changed (2-3 bullets), calls taken, skipped, the `GATE` line, reds and whether `QUESTIONS.md` lists them. At most 8 lines.
