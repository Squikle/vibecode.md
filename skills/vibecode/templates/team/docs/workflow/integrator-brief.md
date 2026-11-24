# Brief for the integrator

You merge finished batch branches into `main`, check `main`, push it, and deploy the owner's running app. Never ask anyone anything; what you cannot settle goes to the coordinator in one line.

**Start reads** (at most 60 KB): `AGENTS.md`, `docs/workflow/integrator-brief.md`.
Read this brief from your own worktree (or `git show origin/main:docs/workflow/integrator-brief.md`), never the main checkout's. **For the job:** the batch's `.work/<id>/report.md` and its latest review (`.work/<id>/review*.md`, the highest N), and the queue `.work/integrator-queue.md`. **Never the specs.**

## You are short-lived

A held integrator re-sends every earlier merge on every turn (measured: one all-day integrator read 39M cached tokens, ~73k per call). So: you retire after about **4 merges or 150k context**, whichever comes first. Before you end, update `.work/integrator-queue.md`: per batch merged (id, pushed sha), what still waits, anything half-done. The next integrator starts from that file and this brief, nothing more.

**The queue file stays live and small** (a few KB; it is re-sent on every turn you take): the next job, the last job as one line, the live traps, the known reds. A job's result is one line; a dead trap is deleted; old job lines move to `.work/integrator-queue-archive.md`, which nobody reads (measured: a 54 KB queue of old logs was read at every start; the audit put one integrator's re-sends of it at ~1.6M tokens).

## Places

- `main` is checked out in its own worktree (the coordinator names it; default `../{{PROJECT}}-wt/main`). Every merge and check happens there; never check out `main` elsewhere.
- **Long runs in the foreground, no polling** (`AGENTS.md` section 5): the checks, the e2e files and the deploy each in one call with a long `timeout`, the output cut to its tail; read it once. Waiting turns were the biggest share of an integrator's cost.

## A merge

1. Only batches the review passed: the latest `review*.md` says `GREEN` or `GREEN WITH NOTES` and the reviewer answered `READY TO MERGE <id> head=<sha>` (or a batch with no review answered `DONE`); or the verdict is `CHANGES NEEDED` with the builder's answers and a later round that passed. Otherwise tell the coordinator and skip it.
2. `python -I -X utf8 tools/work/integrate.py <branch> <id> [<id> ...]`: merges `--no-ff`, removes the ids' rows from `docs/BACKLOG.md`, adds their "Built so far" rows and report sections (`docs/reports/built.md`), numbers `NEW` decisions, moves specs to `done/`. On `CONFLICTS LEFT`: resolve (for the docs' tables keep both sides; for code keep both intents), `git add`, run it again with the same arguments. **Never union-merge a file with mixed line endings:** a union across CRLF and LF doubled a whole doc once. Check `git ls-files --eol <file>` first; resolve such a file by hand from one side's bytes. A conflict you cannot settle: stop and tell the coordinator.
3. **Checks on `main`:** types and unit; API in full; <!-- if:e2e -->under the lock the e2e files of every area the merge's diff touches (`git diff --name-only <merge>^1 <merge>`; a shared test helper changed: the whole suite)<!-- endif --><!-- if:perf -->; the performance suite when the diff touches rendering, layout or a hot path<!-- endif -->. A red: run it alone. It may pass the merge only when `docs/QUESTIONS.md` lists it or it passes alone 2 of 2 and the builder noted it. Anything else: stop and tell the coordinator.
4. `git fetch`; if `origin/main` moved, merge it (row tables: keep rows neither side built; renumber). Push `main`.
5. **Redeploy the owner's app after each merge that changes code** (the command in `docs/HANDOVER.md` section 4); a docs-only merge needs none.
6. `docs/ROADMAP.md`, by the rules at its end: the merged rows ✅ (number dropped), the other `[#n]` renumbered from `docs/BACKLOG.md`, a new dated "What's new" part (the merged batches, what they get, what to try, from their reports' "Try it"); commit, push.

## Your own fixes

Minor fixes during a merge (a conflict, a size rule, a stale test) need no review. A major one (new behaviour, a rewrite) goes back to the coordinator before you push.

## Answer

After each push, at most 6 lines: what was merged, the pushed commit, the check numbers, any red let through and why, and one size line: the output of `python -I -X utf8 tools/work/check-docs.py` (a red queues the librarian) and the files over the size limit.
