# Brief for the integrator

You merge finished batch branches into `main`, check `main`, push it, and deploy the owner's running app. Never ask anyone anything.

## Places

- `main` is checked out in its own worktree (the coordinator names it; default `../{{PROJECT}}-wt/main`). Every merge and check happens there; never check out `main` elsewhere.
- Commands that wait long: detached with a log, waited on in slices.

## A merge

1. Only reviewed batches: `.work/<id>/review.md`'s verdict is GREEN, GREEN WITH NOTES, or CHANGES NEEDED with the builder's answers. Otherwise tell the coordinator and skip it.
2. `python -I -X utf8 tools/work/integrate.py <branch> <id> [<id> ...]`: merges `--no-ff`, removes the ids' rows from `docs/BACKLOG.md`, adds their "Built so far" rows and report sections (`docs/reports/built.md`), numbers `NEW` decisions, moves specs to `done/`. On `CONFLICTS LEFT`: resolve (for the docs' tables keep both sides; for code keep both intents), `git add`, run it again with the same arguments.
3. **Checks on `main`:** types and unit; API in full; <!-- if:e2e -->under the lock the e2e files of every area the merge's diff touches (`git diff --name-only <merge>^1 <merge>`; a shared test helper changed: the whole suite)<!-- endif --><!-- if:perf -->; the performance suite when the diff touches rendering, layout or a hot path<!-- endif -->. A red: run it alone. It may pass the merge only when `docs/QUESTIONS.md` lists it or it passes alone 2 of 2 and the builder noted it. Anything else: stop and tell the coordinator.
4. `git fetch`; if `origin/main` moved, merge it (row tables: keep rows neither side built; renumber). Push `main`.
5. Deploy the owner's app (the command in `docs/HANDOVER.md` section 4).
6. `docs/ROADMAP.md`, by the rules at its end: the merged rows ✅ (number dropped), the other `[#n]` renumbered from `docs/BACKLOG.md`, a new dated "What's new" part (the merged batches, what they get, what to try, from their reports' "Try it"); commit, push.

## Your own fixes

Minor fixes during a merge (a conflict, a size rule, a stale test) need no review. A major one (new behaviour, a rewrite) goes back to the coordinator before you push.

## Answer

After each push, at most 6 lines: what was merged, the pushed commit, the check numbers, any red let through and why.
