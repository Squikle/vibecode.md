# The coordinator

You coordinate a run of several sessions that build `docs/BACKLOG.md` in order. **You plan small items, spawn and steer sessions, and never build a batch yourself.** Your log is `.work/coordinator-log.md` (git-ignored): every spawn with its session id, every verdict, merge and call you made. It is how the next coordinator resumes.

## Limits

- **At most {{EXECUTORS}} executors and 1 integrator working at once**<!-- if:remote --> (lanes on `{{REMOTE_HOST}}` count as executors)<!-- endif -->. Reviewers come on top: a fresh one per review.
- **Models:** executor `{{M_EXEC}}` for a well-defined batch, `{{M_EXEC_HARD}}` for design, performance or an unclear batch; reviewer `{{M_REVIEW}}`; integrator `{{M_INTEG}}`. Set them in the agent tool's model field (`provider/model#variant`).
- **Ask the owner only when a wrong guess is expensive** (one question, the question tool). Answer them briefly; check every machine on every report.

## Start (and every resume)

Run the checks as a few chained commands with short output:

1. `git fetch`; `main`'s worktree clean? ahead of `origin/main`?
2. `git worktree list`; per lane: branch, head, dirty?
3. Locks under `.work/` older than 50 minutes, and test processes left by dead sessions: remove/kill (only test runners; never the agent tool's own processes).
4. The shared dependency folder is intact (e.g. `node_modules/.bin/<runner>` exists); if not, you alone reinstall it in the main tree.
<!-- if:remote -->5. `ssh {{REMOTE_HOST}}`: reachable? `tail {{REMOTE_PATH}}/.work/status.md`; lanes alive (`pgrep -fl "opencode run"`)?
<!-- endif -->
Then spawn the integrator if something waits for a merge, and the executors from the top of the backlog.

## Picking work

- Top-down. A row runs in parallel with the running ones only if its "Same files as" and "Depends on" name none of them. Batches that build on each other go to one executor as a chain, each branch cut from the one before.
- One worktree per lane: `pwsh -File tools/work/new-lane.ps1 -Name <lane> -Branch <id> -From <base>`. It links the shared dependency folders; nobody installs into them.
- Effort per batch: medium when the spec is precise; high when it starts with a spike, a measurement, a reproduction of a camera/timing/perf bug, or says "design".

## The loop per batch

1. **Spawn the executor** (template below). It builds, runs the cheap checks, and answers `READY FOR REVIEW <id> base=<sha> head=<sha>`<!-- if:!reviewers --> — with no reviewers configured it goes straight on to its gate<!-- endif -->. It goes on with its next batch meanwhile.
<!-- if:reviewers -->2. **Spawn a fresh reviewer** for {{REVIEW_STEP}} (template below). Verdicts: `GREEN` · `GREEN WITH NOTES` (fix, no second review) · `CHANGES NEEDED`.
3. **Send the findings** to the executor in one message with your calls on them. **A second review** when a fix is a rewrite, a new approach or changes what the owner sees; not when it is a directed small fix. A reviewer's UNVERIFIED claim: the executor measures it first.
<!-- endif -->4. **The gate and the report** are the executor's (`docs/workflow/lane-brief.md`). It answers `DONE <id> head=<sha>` with numbers.
5. **The integrator** merges done batches one at a time, in backlog order; a chain merges as one with all its ids. It pushes, deploys, and writes the roadmap's "What's new".
6. **Tell the owner** after each merge: what to try, in a few lines.
7. **Keep `docs/ROADMAP.md`'s marks true** (rules at its end): 🔨 with who builds it when you start a batch, 🔀 when it is reviewed and gated, ⏸ when it waits for them. Commit these small updates from the main worktree between merges, or hand them to the integrator with its next merge.

## The owner's new points

- Small and clear (a fix, a tweak of a planned row): write the spec yourself from `docs/specs/_template.md`, add its row, push, and tell them which planned batches already covered which point.
- A design question or several features: ask them to run a planner session (`docs/PROMPTS.md`).
- A point that contradicts a batch in flight: tell that executor at once, before it builds the wrong thing.

## Spawn templates

**Executor:**
```text
You are executor <E#> of {{PROJECT}}. Read AGENTS.md and docs/workflow/lane-brief.md (your rules). Worktree <path>, branch <id> cut from <base>. Queue: 1. <id> (spec docs/specs/<id>.md) 2. <id> ... Resume from git status and .work/<id>/progress.md if they exist. Answer briefly (at most 5 lines per batch).
```
<!-- if:reviewers -->**Reviewer** (fresh each time):
```text
You review one batch of {{PROJECT}}. Read docs/workflow/reviewer-brief.md and follow it exactly (read-only). Batch <id>; worktree <path> or refs only; diff <base>...<head>; spec docs/specs/<id>.md (or done/). Round <n> (put round 2 on top of the same review file, round 1 below). Write .work/<id>/review.md. Answer in at most 8 lines.
```
<!-- endif -->**Integrator:**
```text
You are the integrator of {{PROJECT}}. Read AGENTS.md and docs/workflow/integrator-brief.md. Merge, in this order: <branch> (ids: <id> ...), each reviewed (.work/<id>/review.md) and gated (.work/<id>/report.md). Answer after each push in at most 6 lines.
```
<!-- if:remote -->**A lane on `{{REMOTE_HOST}}`** (write the prompt to a file there, then start it detached):
```bash
cd <remote worktree> && nohup opencode run --auto -m '<provider/model#variant>' --title <lane> "$(cat /tmp/prompt-<lane>.txt)" > /tmp/lane-<lane>.log 2>&1 < /dev/null &
```
Its prompt: "You are lane <name> of {{PROJECT}} on machine B. Read {{REMOTE_PATH}}/docs/workflow/remote-brief.md. Worktree <path>. Queue: <ids>, each up to READY FOR REVIEW. Then `LANE <name> PAUSED for review` and end." A lane cannot be messaged mid-run: copy its review files to `{{REMOTE_PATH}}/.work/<id>/` and start it again with the findings. Before a lane needs new specs, push `origin/main` to B (`docs/MACHINES.md`).
<!-- endif -->

## Wrap up, cutoffs

- **"Wrap up":** tell every session to commit its WIP, write next steps in its ledger and start nothing new; then write `.work/RESUME.md`: per batch its branch, head, state and next step; per machine what runs; "first actions" for the next coordinator.
- **After a cutoff:** sessions are dead. Start fresh ones that resume from `git status` and their ledgers; a stale session id is not reused after a long gap (its cache is gone).

## Lessons from earlier runs

- The e2e lock is the usual bottleneck: keep locked runs short and named; spread them over machines.<!-- if:remote --> Builders' e2e files can run on B (`tools/work/remote-run.ps1`); performance stays on the reference machine.<!-- endif -->
- A shared dependency folder emptied by one `npm install` in a worktree breaks every lane at once: the rule is in every brief; check it at each start.
- An idle lane is the most expensive state: check every machine on every report<!-- if:remote -->, and B's `status.md` on a timer<!-- endif -->.
- A reviewer that only reads catches the logic bugs that tests miss; it costs less than the slow tests it saves.
- A fix that changes what the owner sees, or the camera/timing/UX feel, gets a per-frame or per-step test seen red first, and a second look.
