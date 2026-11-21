# Brief for a builder lane on the second machine

You build batches of {{PROJECT}} in a git worktree on machine B (`docs/MACHINES.md`), started by `opencode run` with no one watching. The coordinator runs on machine A and reads only `{{REMOTE_PATH}}/.work/status.md`.

**Your rules are `docs/workflow/lane-brief.md`, with these changes:**

- **Specs:** your tree may predate a spec: read it with `git show origin/main:docs/specs/<id>.md` (A pushes `origin/main` into this clone). Never fetch or push yourself.
- **Dependencies:** the worktree links to `{{REMOTE_PATH}}`'s own dependency folders; never install into them.
- **Tests:** e2e through `bash tools/work/lock.sh e2e <id> <dir> '<stack up && runner named files>'`. Long runs: `nohup ... > /tmp/<id>-e2e.log 2>&1 &` and read the log's tail every few minutes.<!-- if:perf --> Performance numbers here are reference only; never write them into `docs/MEASUREMENTS.md`.<!-- endif -->
- **A red seen only here:** look for platform causes first (Cmd vs Ctrl, fonts, scale). A real UI fault: fix it. A test that assumes A's OS: fix the test without weakening it. Note either in `docs/QUESTIONS.md` as machine B.
- **Reports** go to `{{REMOTE_PATH}}/.work/<id>/report.md`.

**Status lines** (append to `{{REMOTE_PATH}}/.work/status.md`, one line each):

| When | Line |
|---|---|
| ready for review | `<id> READY FOR REVIEW base=<sha> head=<sha>` |
| gate done | `<id> DONE <branch>@<sha> <gate in a few words>` |
| stopped | `<id> STOPPED <why in one line>` |
| a major change is needed | `<id> STOPPED major change: <one line>` |
| your queue is at review | `LANE <name> PAUSED for review` (then end your run) |
| queue done | `LANE <name> FINISHED` |

You can't be messaged during a run. A reviewer fixes its own minors in your worktree and runs the gate, so you end at `PAUSED for review`. Only a real blocker or major starts you again, with the review (`{{REMOTE_PATH}}/.work/<id>/review*.md`, the highest round): fix it or answer it, write `READY FOR REVIEW` again.
