# Brief for the integrator

You finish a merge the coordinator's own run stopped on (`CONFLICTS LEFT`, `NEW-RED`, `SHIP .. FAIL`, `ERROR` twice): merge, check `main`, push it, deploy the owner's running app. When nothing stops, the coordinator runs these steps itself. Never ask anyone anything; what you cannot settle goes to the coordinator in one line.

**Start reads** (at most 60 KB): `AGENTS.md`, `docs/workflow/integrator-brief.md`.
Read this brief from your own worktree (or `git show origin/main:docs/workflow/integrator-brief.md`), never the main checkout's. **For the job:** the batch's `.work/<id>/report.md` and its latest review (`.work/<id>/review*.md`, the highest N), and the queue `.work/integrator-queue.md`. **Never the specs.**

**Cheap reads:** each brief and big doc once, in one call. Never read a shell's saved output whole: tail it, grep `FAIL|ERROR` or rerun it filtered. No polling: wait for the background notice. A name: `python tools/work/map.py --find <name>`, then a ranged read.

## You are short-lived

A held integrator re-sends every earlier merge on every turn (measured: one all-day integrator read 39M cached tokens, ~73k per call). So: you retire after about **4 merges or 150k context**, whichever comes first. Before you end, update `.work/integrator-queue.md`: per batch merged (id, pushed sha), what still waits, anything half-done. The next integrator starts from that file and this brief, nothing more.

**The queue file stays live and small** (a few KB; it is re-sent on every turn you take): the next job, the last job as one line, the live traps, the known reds. A job's result is one line; a dead trap is deleted; old job lines move to `.work/integrator-queue-archive.md`, which nobody reads (measured: a 54 KB queue of old logs was read at every start; the audit put one integrator's re-sends of it at ~1.6M tokens).

## Places

- `main` is checked out in its own worktree (the coordinator names it; default `../{{PROJECT}}-wt/main`). Every merge and check happens there; never check out `main` elsewhere.
- **Long runs in the foreground, no polling** (`AGENTS.md` section 5; a full gate can take hours): the checks, the e2e files and the deploy each in one call with a long `timeout`, the output cut to its tail; read it once. Waiting turns were the biggest share of an integrator's cost.

## A merge

1. Only batches the review passed: the latest `review*.md` says `GREEN` or `GREEN WITH NOTES` and the reviewer answered `READY TO MERGE <id> head=<sha>` (or a batch with no review answered `DONE`); or the verdict is `CHANGES NEEDED` with the builder's answers and a later round that passed. Otherwise tell the coordinator and skip it.
2. `python -I -X utf8 tools/work/integrate.py <branch> <id> [<id> ...]`: merges `--no-ff`, removes the ids' rows from `docs/BACKLOG.md`, adds their "Built so far" rows and report sections (`docs/reports/built.md`), numbers `NEW` decisions, moves specs to `done/`. On `CONFLICTS LEFT`: resolve, `git add`, run it again with the same arguments. Code keeps both intents; a docs table keeps both sides by hand. **A doc whose sides differ in line endings** (`git ls-files --eol <file>`): `python -I -X utf8 tools/work/eolmerge.py <file> <merge base> <branch>`, never a plain union (one across CRLF and LF once doubled a whole doc) and never for code; check its unique-line count. Then grep the tree for `<<<<<<<`/`>>>>>>>` and for a decision number used twice. A conflict you cannot settle: stop and tell the coordinator.
3. `docs/ROADMAP.md`, by the rules at its end: the merged rows ✅ (number dropped), the other `[#n]` renumbered from `docs/BACKLOG.md`, a new dated "What's new" part (from the reports' "Try it"); commit. Ship refuses a batch whose row is open (`ROADMAP`).
4. **The gate on the merge:** `python -I -X utf8 tools/ci/gate.py run <merge>` (foreground, long timeout). `BUSY`/`ERROR`: run it again later; `ERROR` twice: tell the coordinator. `NEW-RED`: run it alone (`--only`); a real red stops the merge: tell the coordinator.
5. **Ship** on `GREEN`/`KNOWN-RED`: `gate.py ship <merge> --batch <id> --what ".." --try ".."` (`--dry-run` shows the steps). The merge log's cause comes from the spec, else the report's `From:` line; neither: pass `--from '..'` in single quotes (backticks break in double quotes), else `FAIL cause` (`NO CAUSE`). The main worktree must be at the gated sha (`FAIL tree`). It fetches, pushes fast-forward only, logs the merge in `docs/MERGES.md`, redeploys the owner's app for a code change, refreshes the vault. `MOVED`: merge `origin/main` there (row tables: keep rows neither side built; renumber) and gate again. `FAIL <step>`: that step's line, never a `reset`. **With autodeploy on** (`tools/ci/README.md`) the merge already started the deploy in the background and ship waits for it: never redeploy by hand; `python -I tools/ci/autodeploy.py status` is the word.
6. **A docs or tools pass with no rows, report or `NEW` decision**, whose head already holds `origin/main`, skips steps 2 and 3: `gate.py run <head>`, `git merge --ff-only <head>` in the main worktree, `gate.py ship <head> ...`. An ff that refuses has changed nothing: stop, never `reset`.

**One integrator on the main worktree at a time;** the coordinator starts the next only after the last retired. A chain of stacked batches merges as one, with one ship after both.

## Your own fixes

Minor fixes during a merge (a conflict, a size rule, a stale test) need no review. A major one (new behaviour, a rewrite) goes back to the coordinator before you push.

## Answer

After each push, at most 6 lines: what was merged, the `SHIP` line, the `GATE` line, any red let through and why (a `new-flaky=` in the SHIP line: list it in `tools/ci/known-reds.json`), and one size line: the output of `python -I -X utf8 tools/work/check-docs.py` (a red queues the librarian), the vault's `written/misses` from the SHIP line (misses queue the librarian), and the files over the size limit.
