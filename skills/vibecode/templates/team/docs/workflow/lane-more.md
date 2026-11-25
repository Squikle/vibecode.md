# Lane brief, the rest

The details behind `docs/workflow/lane-brief.md`'s pointers. Read a section when the brief points to it (`python tools/work/find.py --section docs/workflow/lane-more.md#<n>`), once; never this whole file at the start. Its rules hold as if they were in the brief.

## 1. Reading big things
- **Every role reads each brief and big doc once, in one call** (the source project's audits: a brief and its questions doc read twice; a 16.8k lane brief read twice by one lane).
- A doc over ~15 KB by part: `python tools/work/find.py --toc <doc>`, then `--section <doc>#<n>` or `find.py <id or word>`.
- A source file over ~15 KB: `python tools/work/map.py <file>` first (its outline with line numbers: 1.2 KB for a 22 KB file), `map.py --find <name>` for where a name is defined, `git grep -n -E "a|b"` for its uses, then offset/limit; never whole twice.
- A truncated shell output: rerun it filtered (`Select-String`/`grep`, `-First`/`-Tail`/`head`/`tail`), never read its saved full output whole (audits: reads of 42k and 47k).
- **rtk:** with `rtk` on PATH, prefix `git log` (~58% smaller) and `git status` (~46%): `rtk git log -20`. Not for `git diff`/`show`, the gate or pipes. Without it, the plain command.

## 2. Your worktree
- Its first branch exists and is checked out. A lane's batches build on each other: each later one from the branch of the batch before.
- **Shared dependency links:** an install through a link empties the main tree's folder (in the source project it broke every lane twice). Before `git worktree remove`: remove the link only (`cmd /c rmdir <link>` on Windows, `rm <link>` elsewhere).
- **The live app is the owner's:** only a merge redeploys it (`gate.py ship`). A trial stack: `docker compose -p <id> ...` and `down` in the same command; never the default project name.
- **A `.env`** a test needs is made from `.env.example` in the command that needs it and deleted in the same command; `git status` shows none before a commit.
- **Never pipe a script into `python -`/`node -`** (it hangs on stdin): write the file, then run it. Non-ASCII text piped from a shell into python can lose characters: edit those files with your file tools.
- **Git:** never change git config (a `core.bare = true` once broke the main tree). A commit that rewrites a whole file keeps its line endings. Untracked tool folders (`.serena/` and the like): never commit them.
<!-- if:remote -->- **Machine B:** `pwsh -File tools/work/remote-run.ps1 -Id <id> -Cmd "<bash: stack up; runner named files>"` tests your committed HEAD there (the gate's `remote` parts do the same). <!-- if:perf -->Performance runs stay on machine A. <!-- endif -->A red seen only on B (fonts, keys, timing): rerun it alone on A before you call it yours. B's lock held over 10 minutes: use A's.
<!-- endif -->

## 3. The loop, in detail
- **The ledger** (`.work/<id>/progress.md`): commit, gate numbers, open reds, traps, one line each (append, don't rewrite). Read it back only after a compaction or a resume. The coordinator deletes it once the batch is MERGED or retired.
- **No failing test, no ship:** each rule of "What must be true" gets a test seen red before the change, so the test proves the rule. A rule no test can hold (a look, a feel) gets one line in the report; the reviewer checks it by hand. Extend an existing test before you add a near-copy.
- **The gate** (`tools/ci/README.md`; a full one can take hours): `gate.py plan <head>` says what it runs and why. It takes the locks, reruns reds alone and prints one `GATE` line. `--all` when the diff touches what every test stands on (shared helpers, fixtures, the default view). `BUSY`: the machine is busy; run it again later. `WAITED`: a lock stayed held `wait_minutes` and nothing ran: no red, run it again later. A `--only` run takes a free stack by itself (`MOVED`); `--stack N` pins one. `ERROR` twice (the gate itself broke): run its plan by hand and say so.
- **Reds:** `KNOWN-RED` is listed (`tools/ci/known-reds.json`, `docs/QUESTIONS.md`). One that passed alone but is in no list is a new flaky: list it. Never weaken or delete a test. A major change is a new approach, a rewrite of a part, or a change of what the user sees.

## 4. Docs and the report
- Docs as HANDOVER section 3 step 4: a row of `QUESTIONS.md` or `DECISIONS.md` goes where `find.py --toc`/`--section` shows, never after a whole read; a new decision is numbered `NEW` (the merge numbers it).
- **The report** is in `<main tree>/.work/<id>/report.md` (the main tree's `.work`, not your worktree's), in HANDOVER section 3 step 5's parts, with the `GATE` line in its Gate part and one "Built so far" row. A batch with no spec carries `From: <why>` (a batch id, or the owner's dated quote `Owner YYYY-MM-DD: "<quote>"`; a fix lane names the batch it fixes): the merge log needs a cause. Docs name a session by its role and batch id.
- The branch stays as it is: the coordinator merges it.

## 5. The review
1. **Build** the batch with its tests (test-first); delete the plan, commit, write the report, answer `READY FOR REVIEW <id> base=<sha> head=<sha>`.
2. **You retire there:** on a green verdict the reviewer fixes its minors, runs the gate and answers `READY TO MERGE`; you are not resumed for that.
3. **A real blocker or major comes back to you** (`<main tree>/.work/<id>/review.md`, the highest `review-N.md`): fix it, or answer it in one line under "Builder's answers" with your reason; then `READY FOR REVIEW` again, and a fresh reviewer checks only the delta.
4. **A lane with a next batch** goes on with it while the review runs; before its own gate it merges the reviewer's `READY TO MERGE` head of the batch before, if there is one.
