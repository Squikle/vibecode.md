# Handover: the state of the work and how a batch is built

## 1. Built so far

| Batch | What it gave |
|---|---|
| (the integrator<!-- if:solo --> / the builder<!-- endif --> adds a row per merged batch) | |

## 2. Open batches

`docs/BACKLOG.md`, in order. Read only your batch's spec and the specs it depends on. A row marked `needs grooming` is not built. A name no longer in the backlog is built: skip it.

## 3. The batch loop

1. **Start:** `git fetch`; <!-- if:team -->your worktree's branch is cut by the coordinator<!-- endif --><!-- if:solo -->merge `origin/main` into `main` if it moved, then `git checkout -b <id>`<!-- endif -->. Read the spec. Write a short plan in `docs/plans/<id>.md` (a task list, 40 to 60 lines) and keep a ledger in `.work/<id>/progress.md`: what is done, what is next, commits, numbers. The ledger is how a fresh session resumes after a cutoff.
2. **Build, task by task,** one commit per task. Logic test-first (seen red). Every number a named constant.
3. **Cheap checks:** types, unit tests, and the API tests if you touched the API.
<!-- if:reviewers -->3b. **Review before the slow tests** ({{REVIEW_STEP}}): <!-- if:team -->answer the coordinator `READY FOR REVIEW <id> base=<commit you cut from> head=<commit>` and go on with your next batch while it runs<!-- endif --><!-- if:solo -->spawn a fresh reviewer subagent on `{{M_REVIEW}}` with `docs/workflow/reviewer-brief.md`, the id, base and head<!-- endif -->. The review lands in `.work/<id>/review.md`. Address every finding: fix it, or answer it in one line under "Builder's answers" with your reason. Minor findings need no second review; a major one may (<!-- if:team -->the coordinator decides<!-- endif --><!-- if:solo -->yours to judge: a rewrite or a change the owner sees gets one<!-- endif -->).
<!-- endif -->4. **The gate:** unit and API in full<!-- if:e2e -->, then under the lock the e2e files of every area the diff touches<!-- if:perf --> and the performance suite<!-- endif --><!-- endif -->; then `git diff <base>...HEAD` against the spec's "What must be true", rule by rule. A red test: run it alone. Passes alone 3 of 3: a flake, note it in `docs/QUESTIONS.md`. Otherwise it is yours: fix it, test first. If a red forces a major change (a new approach, a rewrite, a change the owner sees), stop and say so<!-- if:team --> to the coordinator<!-- endif --> before going on.
5. **Docs:** `docs/ARCHITECTURE.md` (the map), `docs/DECISIONS.md` (a new decision numbered `NEW`; the integrator numbers it), `docs/QUESTIONS.md`, `docs/MEASUREMENTS.md`; `git mv docs/specs/<id>.md docs/specs/done/`. Delete the plan.
6. **Report** in `.work/<id>/report.md`: `## <id>`, **What changed**, **Try it** (steps for them), **Taken without asking** (each with the file or constant to change), **Skipped**, **Gate** (numbers), and one "Built so far" row.
<!-- if:solo -->7. **Merge** into `main` with `--no-ff`, run the gate on `main`, push, deploy, add the "What's new" part to `docs/ROADMAP.md`.
<!-- endif --><!-- if:team -->7. **Done:** answer the coordinator `DONE <id> head=<sha>` with the gate numbers in at most 5 lines. The integrator merges.
<!-- endif -->

## 4. Running and testing

The `bootstrap` batch fills this section with exact commands.

| What | Command |
|---|---|
| Run the app for the owner | |
| Types | |
| Unit (one file / all) | |
| API (one file / all) | |
<!-- if:e2e -->| e2e, one file, under the lock | `pwsh -File tools/work/lock.ps1 -Name e2e -Who <id> -Cmd "<bring the test stack up>; <run the named files>"` |
<!-- endif --><!-- if:perf -->| Performance suite (reference machine only) | |
<!-- endif --><!-- if:remote -->| e2e on the second machine | `pwsh -File tools/work/remote-run.ps1 -Id <id> -Cmd "<same as above, bash>"` (tests the committed HEAD) |
<!-- endif -->| The gate on `main` | |

<!-- if:e2e -->**Loading the machines:** each machine runs one locked e2e run at a time, with the test runner's workers set from `docs/MACHINES.md` (about cores minus 2). Builders run only their files; the integrator runs the touched areas. A test stack is shared by all worktrees of a machine: every locked command starts by bringing it up from the caller's tree. Never take a stack down while others may use it.
<!-- endif -->

## 5. What to update when a batch is merged

The backlog row goes; a "Built so far" row and the report's section come in; `NEW` decisions get numbers; the spec is in `done/`; `docs/ROADMAP.md` gets a dated "What's new" part and its marks.

## 6. Unattended runs, cutoffs and wrap-up

- **Unattended:** never wait for a person. Decide what is cheap to get wrong and record it; skip a part whose guess is costly and note it; skip a batch only when nothing of it can be built.
- **A usage cutoff or a crash:** a fresh session resumes from `git status` and `.work/<id>/progress.md`; never from memory.
- **"Wrap up":** commit the work in progress (`WIP: ...`), write the next steps in the ledger, start nothing new<!-- if:team -->; the coordinator writes `.work/RESUME.md` with every session's head and next step<!-- endif -->.
