# Handover: the state of the work and how a batch is built

<!-- if:solo -->**Start reads** (at most 60 KB): `AGENTS.md`, `docs/HANDOVER.md`, `docs/ARCHITECTURE.md`.
**For the job:** the batch's spec and the specs it depends on; the one `docs/architecture/<area>.md` of its area.

<!-- endif -->## 1. Built so far

| Batch | What it gave |
|---|---|
| (`integrate.py` adds a row per merged batch) | |

## 2. Open batches

`docs/BACKLOG.md`, in order. Read only your batch's spec and the specs it depends on. A row marked `needs grooming` is not built. A name no longer in the backlog is built: skip it.

## 3. The batch loop

1. **Start:** `git fetch`; <!-- if:team -->your worktree's branch is cut by the coordinator<!-- endif --><!-- if:solo -->merge `origin/main` into `main` if it moved, then `git checkout -b <id>`<!-- endif -->. Read the spec. Write a short plan in `docs/plans/<id>.md` (a task list, 40 to 60 lines) and keep a ledger in `.work/<id>/progress.md`: what is done, what is next, commits, numbers, and **a `NOW:` line** (what you are doing this minute; update it at each step). The ledger is how a fresh session resumes after a cutoff, and how the coordinator tells a silent session from a busy one.
2. **Build, task by task,** one commit per task. Logic test-first (seen red). Every number a named constant. **A rule of "What must be true" with no test that failed before the change doesn't ship:** each rule gets a test seen red, or a report line on why no test can hold it (the reviewer checks that one by hand).
3. **Cheap checks:** types, unit tests, and the API tests if you touched the API.
4. **Docs:** `docs/ARCHITECTURE.md` (the map), `docs/DECISIONS.md` (a new decision numbered `NEW`; `integrate.py` numbers it), `docs/QUESTIONS.md`, `docs/MEASUREMENTS.md`; `git mv docs/specs/<id>.md docs/specs/done/`. Delete the plan.
5. **Report** in `.work/<id>/report.md`: `## <id>`, **What changed**, **Try it** (steps for them), **Taken without asking** (each with the file or constant to change), **Skipped**, **Gate** (numbers; whoever runs the gate fills them), and one "Built so far" row. Commit.
<!-- if:reviewers -->5b. **Review before the slow tests** ({{REVIEW_STEP}}): <!-- if:team -->answer the coordinator `READY FOR REVIEW <id> base=<commit you cut from> head=<commit>`, go on with your next batch up to its own READY, then end your session<!-- endif --><!-- if:solo -->spawn a fresh reviewer subagent on `{{M_REVIEW}}` with `docs/workflow/reviewer-brief.md`, the id, base and head<!-- endif -->. The review lands in `.work/<id>/review.md` (round 2 `review-2.md`, and so on; the latest counts).
   - **No blocker or major** (`GREEN`, `GREEN WITH NOTES`): the **reviewer** fixes its own minors and nits in <!-- if:team -->the executor's<!-- endif --><!-- if:solo -->your<!-- endif --> worktree (a trivial major too, such as a one-line test fix), runs the gate (step 6) and answers `READY TO MERGE <id> head=<sha>`. <!-- if:team -->The executor is not resumed.<!-- endif -->
   - **A real blocker or major** goes back to <!-- if:team -->the executor, who knows the design (the coordinator resumes it, or starts a fresh one with a pointer brief)<!-- endif --><!-- if:solo -->you, who know the design<!-- endif -->: fix it, or answer it in one line under "Builder's answers" with your reason, and answer READY FOR REVIEW again. A **fresh** reviewer then checks only the delta (`git diff <old head>...<new head>`) and writes `review-2.md`. A rewrite or a change the owner sees always gets that second look.
<!-- endif -->6. **The gate:** `python -I -X utf8 tools/ci/gate.py run <head>` in the foreground with a long timeout (`tools/ci/README.md`; `gate.py plan <head>` says what it would run and why). It runs unit and API in full<!-- if:e2e -->, the e2e files of every area the diff touches under the lock<!-- if:perf -->, the performance suite<!-- endif --><!-- endif -->, reruns each red alone and prints one `GATE` line. Then `git diff <base>...HEAD` against the spec's "What must be true", rule by rule. <!-- if:reviewers -->Run by the reviewer for a reviewed batch, by the builder for one without a review. <!-- endif -->`BUSY`: another run holds the machine; run it again later. `KNOWN-RED`: listed in `tools/ci/known-reds.json`; one marked `alone` (passed alone, in no list) is a new flaky: list it there and in `docs/QUESTIONS.md`. A `NEW-RED` is the batch's: fix it, test first, then `gate.py run <head> --only <file>`. If a red forces a major change (a new approach, a rewrite, a change the owner sees), stop and say so<!-- if:team --> to the coordinator<!-- endif --> before going on.
<!-- if:solo -->7. **Merge** into `main`: `python -I -X utf8 tools/work/integrate.py <id> <id>` (`--no-ff`, the docs' bookkeeping), mark the ROADMAP rows and add its "What's new" part, commit; `gate.py run <merge>`; on `GREEN`/`KNOWN-RED`: `gate.py ship <merge> --batch <id> --what ".." --try ".."` (it pushes fast-forward only, logs the merge in `docs/MERGES.md` and deploys).
<!-- endif --><!-- if:team -->7. **Done:** answer the coordinator `DONE <id> head=<sha>` with the gate numbers in at most 5 lines<!-- if:reviewers --> (a reviewed batch: the reviewer's `READY TO MERGE` is the signal)<!-- endif -->. The coordinator merges.
<!-- endif -->

### How to build

- **The spec's `## How to build`** (the CTO's) wins; deviate only with a line in the report.
- **The smallest complete change.** Take the first that fully works: not needed at all (say so in one line) → already in this codebase (reuse it the way the code around it does) → the platform or an installed dependency → one line a reader gets at a glance → the least new code. Finish every caller, test and fixture it touches. Deletion beats addition.
- **Rules live in the domain:** a product rule is a plain function in the domain modules `docs/ARCHITECTURE.md` names, in the product's words; UI, routes and IO call it.
- **No speculative structure (YAGNI, KISS).** No option, wrapper, layer or seam for a case that does not exist; a new seam for the second case of a kind is the CTO's call. Merge near-copies at the third copy (DRY), never by coupling things that change for different reasons.
- **A bug fix fixes the cause:** reproduce it first, find every caller of what you change, fix it once in the shared code.
- **Errors and logs:** nothing swallowed; at a boundary fail with a message the user can act on, and log once there with the ids; never in a hot loop.
- **Trust boundaries:** validate what comes from files, users, agents and URLs; no shell strings built from input; paths stay inside their root.
- **A shortcut with a known limit** gets a comment: `shortcut: <the limit>, <when to upgrade>`.
- **Done means seen:** say a thing works only from a command's output in this session.

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
<!-- endif -->| Doc budgets and start reads | `python -I -X utf8 tools/work/check-docs.py` |
| The gate (any sha; ship after a green merge) | `python -I -X utf8 tools/ci/gate.py run <sha>` · `gate.py ship <sha> --batch <id> --what ".." --try ".."` (the parts' commands: `tools/ci/ci.json`) |

<!-- if:e2e -->**Loading the machines:** each machine runs **one browser suite at a time** (a lock per machine), with the test runner's workers set from `docs/MACHINES.md` (about cores minus 2), drawing on the GPU where the machine has one. Builders run only their files; the integrator runs the touched areas. A test stack is shared by all worktrees of a machine: every locked command starts by bringing it up from the caller's tree. Never take a stack down while others may use it.
<!-- endif -->

## 5. What to update when a batch is merged

The backlog row goes; a "Built so far" row and the report's section come in; `NEW` decisions get numbers; the spec is in `done/`; `docs/ROADMAP.md` gets a dated "What's new" part and its marks.

## 6. Unattended runs, cutoffs and wrap-up

- **Unattended:** never wait for a person. Decide what is cheap to get wrong and record it; skip a part whose guess is costly and note it; skip a batch only when nothing of it can be built.
- **A usage limit, a cutoff or a crash:** when the plan's usage limit stops the sessions, the owner types `continue` after the reset<!-- if:team --> and the coordinator resumes each session<!-- endif -->. A session resumes from its progress file and `git status`, never from memory: check for a half-done edit and a stale lock first.
- **"Wrap up":** commit the work in progress (`WIP: ...`), write the next steps in the ledger, start nothing new<!-- if:team -->; the coordinator writes `.work/RESUME.md` with every session's head and next step<!-- endif -->.
