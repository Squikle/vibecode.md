# Machines

| Machine | What | Role | Test workers |
|---|---|---|---|
| A (this one) | {{LOCAL_MACHINE}} | planner, coordinator, executors, integrator<!-- if:perf -->, the reference for performance budgets<!-- endif --> | cores − 2 |
<!-- if:remote -->| B (`{{REMOTE_HOST}}`) | {{REMOTE_MACHINE}} ({{REMOTE_OS}}), repo at `{{REMOTE_PATH}}` | e2e runs for A's builders; builder lanes | cores − 2 |
<!-- endif -->

Notes from the setup: {{NOTES}}

<!-- if:remote -->
## The second machine

**One-time setup** (the coordinator does it once `bootstrap` is merged):

1. On B: `git init {{REMOTE_PATH}}`; from A: `git push {{REMOTE_HOST}}:{{REMOTE_PATH}} main:refs/heads/coord-main` (B has no credentials for the hosted remote; A pushes to it). On B: `git -C {{REMOTE_PATH}} checkout -b main coord-main`.
2. On B: install the dependencies once in `{{REMOTE_PATH}}` (its own `node_modules`, venv, ...). Worktrees on B link to them.
3. Copy `tools/work/lock.sh` to B (it is in the repo after step 1) and `chmod +x` it.
4. **Docker over ssh on macOS:** the login keychain is closed in an ssh session, so pulls fail on `credsStore`. Make `~/.docker-ssh/config.json` = `~/.docker/config.json` without `credsStore`/`credHelpers`, link `cli-plugins`, `contexts` and `buildx` into it, and run docker with `DOCKER_CONFIG=~/.docker-ssh` (public images only). `remote-run.ps1` sets it.
5. Check: `pwsh -File tools/work/remote-run.ps1 -Id smoke -Cmd "<one fast e2e file>"`.

**Keeping B current:** `git push {{REMOTE_HOST}}:{{REMOTE_PATH}} origin/main:refs/remotes/origin/main` after each merge; B's lanes read specs with `git show origin/main:docs/specs/<id>.md`. Branches come back with `git fetch {{REMOTE_HOST}}:{{REMOTE_PATH}} <b>:<b>`.

**Builder lanes on B** run as `opencode run --auto -m <provider/model#variant> --title <lane> "<prompt>"` in a worktree there, logging to `/tmp/lane-<name>.log`, and report to `{{REMOTE_PATH}}/.work/status.md` (`docs/workflow/remote-brief.md`). They use the same account as A: one usage cap for all.
<!-- endif -->
