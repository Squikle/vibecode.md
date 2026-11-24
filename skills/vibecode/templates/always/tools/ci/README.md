# The local CI gate (`tools/ci/`)

One call per sha: it runs what the diff needs on a clean checkout of that sha, reruns each red alone, and prints **one
status line**. Stdlib Python only; Windows, macOS and Linux. The project's stack lives in `ci.json`, not in the code.

## How it is called

```
python tools/ci/gate.py plan <sha>              # what would run, and why (no run)
python tools/ci/gate.py run <sha>               # the gate (foreground, one call, long timeout)
python tools/ci/gate.py status <sha>            # the last run's line again
python tools/ci/gate.py ship <sha> --batch <id> --what "<what>" --try "<try>" [--dry-run]
python tools/ci/gate.py run --only unit tests/test_x.py   # a lane's quiet run: <= 15 lines, never shippable
```

- **Status line:** `GATE <sha8> <VERDICT> 2m52s | types ok | unit 3593/0 | e2e 151/5 (known 4, alone 1, new 0) | shipped: no`.
  Each new red follows on its own line: part, test id, `alone g/n`, first error line, log.
- **Verdicts** (exit 0 for the first two, else 1):
  - `GREEN`: nothing red.
  - `KNOWN-RED`: every red is listed in `known-reds.json`, or green alone 2 of 2 (3 of 3 for a listed `flake`).
    An unlisted red green alone 2 of 2 is named **new-flaky** (in the line, the json and the ship's line): whoever ships
    lists it in `known-reds.json` as a `flake` with its `docs/QUESTIONS.md` row.
  - `NEW-RED`: a red the batch owns.
  - `BUSY`: a `heavy` part was skipped (a listed process runs) or its lock stayed held `lock_minutes`. Run again later.
  - `ERROR`: the gate or a runner broke (no JUnit report, a timeout, exit non-zero with no red, no `cmd` set). Read the log.
- **Options:** `--parts a b` (these parts whatever the diff), `--all`, `--base <rev>` (what the diff is measured against;
  default `origin/main`), `--no-remote` (this machine only), `--keep` (keep the export). Unknown arguments are an error.
- **Results:** `.work/ci/<sha8>.json` (parts, reds, skipped, `line`, `shipped`) and the logs in `.work/ci/<sha8>/`, in the
  main checkout. Read the line or `status`; open a log only for a red you fix, and then its tail.

## `ci.json`: the parts

The `bootstrap` batch fills each `cmd`. A part:

| Key | Meaning |
|---|---|
| `name`, `cmd` | the part and its shell command, run in the sha's export (a detached worktree under `.work/ci/src/`) |
| `paths` | globs (fnmatch, `*` crosses folders) of changed files that make it run; `["*"]` = always |
| `junit` | glob of the JUnit XML it writes (relative to the export). Without it the part is one test: exit 0 or red |
| `rerun` | the command for one red alone: `{id}`, `{name}`, `{classname}`, `{file}` from the report |
| `files_cmd`, `match` | `--only` with files: a file matching `match` runs `files_cmd` with `{files}` |
| `timeout` | minutes; past it the part's process tree is killed and the part is ERROR |
| `lock` | a machine lock name (the same `.work/<name>.lock` as `tools/work/lock.ps1` / `lock.sh`); waits up to `lock_minutes` |
| `heavy` | BUSY instead of a run while a process in `busy.processes` runs (a game, a render, another benchmark) |
| `docker` | run inside the compose service (`docker.compose`, `docker.service`), the export mounted at `/src` |
| `remote` | run on the second machine over ssh (`remote`: `name`, `ssh`, `repo`, `git`); the sha is pushed there first |

Top level: `base`, `lock_minutes` (50), `setup` (once per export, e.g. installing dependencies), `docs_only` (globs of a
diff that redeploys nothing), `ship.deploy` (the command that redeploys the owner's app), `ship.health` (a URL that
must answer 200 after it). This machine's overrides go in `.work/ci/machine.json` (git-ignored, merged over `ci.json`):
`name`, `busy`, `remote`, `ship`, `timeouts` per part.

Most runners write JUnit with one flag: pytest `--junitxml`, vitest/jest/Playwright a `junit` reporter, `go test` via
`go-junit-report`, cargo-nextest `--profile ci`. Write it under `.work/junit/` so it never lands in git.

## Ship

`ship` only after a **GREEN or KNOWN-RED gate of that very sha**, from the worktree that has `main` checked out at it:
- It refuses with `NOT-GATED` (no full gate of this sha; an `--only` run is not one), `NOT-GREEN`, `MOVED` (origin/main
  moved: merge it and gate again; no auto-merge), `ROADMAP` (the batch's `docs/ROADMAP.md` row is not ✅; `--no-roadmap`).
- Then, in order: the merge log (`tools/work/merge-log.py` into `docs/MERGES.md`) and its commit, the push, the vault (if
  `tools/vault/` exists), the deploy (not for a docs-only diff) and its health check, the remote's fast-forward.
- One line: `SHIP <sha8> OK pushed=.. deploy=yes|no vault=w/m remote=ok|skip [new-flaky=..]` or
  `SHIP <sha8> FAIL <step>: <reason>`. A FAIL leaves the steps after it to do by hand, in that order.

## Known reds

`known-reds.json` lists the reds a merge may carry: `part`, `test` (the JUnit id `<classname>::<name>`, or with `title`
the file or class plus part of the test's name, so a moved line still matches), `kind` (`red` or `flake`), `machines`
(`["any"]` or names), `why`, `source`. **Each one also has its `docs/QUESTIONS.md` row**; the librarian drops both once
the test has been green for two full gates.

## Set up a machine

**Docker-native (the default for a new project):** the gate's parts run in one `ci` image, the same on every host.
1. Install Docker Desktop (or Docker Engine) and Python 3.11 or later (`python3` on macOS and Linux).
2. Add a `Dockerfile.ci` with the toolchain and a `docker-compose.ci.yml` beside it:
   ```yaml
   services:
     ci:
       build: { context: ., dockerfile: Dockerfile.ci }
       profiles: [ci]
   ```
   Build once: `docker compose -f docker-compose.ci.yml --profile ci build ci`. Mark the parts `"docker": true`.
3. A part that needs the host (a GPU, a real browser window, a perf suite on the reference machine) stays a host part.

**macOS:** run it as `python3 tools/ci/gate.py ...`. Over ssh, Docker Desktop's keychain can block `docker`: set
`DOCKER_CONFIG` to a config without `credsStore` for those runs. Locks there are folders (`.work/<name>.lock.d`).

**A second machine:** set `remote` in `.work/ci/machine.json` (`{"name": "mac", "ssh": "<host alias>", "repo": "<path>",
"git": "<host alias>:<path>"}`) and mark the parts that run there `"remote": true`. The ship fast-forwards its checkout.
