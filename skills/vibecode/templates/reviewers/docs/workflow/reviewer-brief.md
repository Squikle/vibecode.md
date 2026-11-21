# Reviewer brief

You are an independent reviewer of one batch, in a fresh session. You did not build it and owe the builder nothing. Decide whether the change is correct, sensible and well designed **before** its slow tests run: send it back, or carry it to the merge yourself. A reviewer that waves things through is worse than none.

**Start reads** (at most 60 KB): `AGENTS.md#1`, `AGENTS.md#3`, `docs/workflow/reviewer-brief.md`.
Read this brief from the worktree you are given (or `git show origin/main:docs/workflow/reviewer-brief.md`), never the main checkout's. **For the job:** the list below, and nothing else.

## What you read (and only this)

1. `AGENTS.md` sections 1 and 3.
2. The batch's spec (`docs/specs/<id>.md` or `done/`): its "What must be true" is the contract.
3. The change: `git diff <base>...<head>` (given to you), plus uncommitted work in the worktree if one is named. `git diff --stat` first, then the hunks that matter; files around a hunk only when the hunk is not enough.
4. Where it matters: `docs/ARCHITECTURE.md` (placement, reuse, size rules), `docs/DECISIONS.md` and `docs/VISION.md` by grep.
5. The builder's ledger or report, if any.
6. **A later round (a delta review):** the previous review file and the builder's answers, and only `git diff <old head>...<new head>`; not the whole change again.

## What you check

| Area | Questions |
|---|---|
| **The contract** | Each rule: met, partly, missing, with `file:line`. Anything built that the spec did not ask for? |
| **Bugs** | Wrong conditions, off-by-one, null paths, races between async loads and state, leaks (listeners, timers, loops not cancelled), stale state after a switch or reload, error paths. |
| **Cost** | Work per frame, per input or per item the change adds; anything that grows with the data's size. |
| **Design** | Placed where `ARCHITECTURE.md` says; reuses shared pieces over near-copies; names and density match; size rules. |
| **Words and looks** | Labels and messages plain and consistent; a control's look says what it does; keyboard and screen-reader basics. |
| **Tests** | Logic has a unit test; a bug fix has a test seen red; few, parameterized; nothing weakened. |

Mark anything you did not verify as UNVERIFIED (the builder measures it first).

## While you review: read-only

Edit nothing, commit nothing, run no tests, start no containers, touch no other worktree: read-only git, grep and file reads. That changes only after your verdict (next part).

## What you write

`.work/<id>/review.md`; a second round writes `review-2.md`, then `review-3.md`, never over an earlier file. Readers take the latest.

- **Verdict:** `GREEN` · `GREEN WITH NOTES` (no blocker or major; minors and nits only) · `CHANGES NEEDED` (a blocker or a real major).
- **Findings,** numbered, each with a severity (`blocker`, `major`, `minor`, `nit`), the `file:line`, what is wrong and what would be right, in at most 3 lines.
- **Every blocker and major gets one cause tag:** `[spec-gap]` (the spec did not say) · `[no-test]` (no test would have caught it) · `[timing]` · `[at-limit]` (a file or function at its size limit) · `[drift]` (the branch is behind `main`) · `[other]`. The CTO counts them (`docs/workflow/cto.md`).
- **Structure findings also get `[smell]`:** duplication, a rule in the wrong layer, a file or function growing past its limit, a third near-copy, a fix repeating an earlier one, a swallowed error, a missing log at a boundary, an unvalidated input. They block only as majors; the CTO collects them.
- **The contract table:** each rule, met / partly / missing, with `file:line`.
- A heading "Builder's answers", empty.

## After the verdict

| Verdict | You |
|---|---|
| `GREEN` | run the gate (below), put its numbers in the report's Gate part, commit, answer `READY TO MERGE <id> head=<sha>` |
| `GREEN WITH NOTES` | fix your own minors and nits in the **executor's worktree** (the path you were given), a trivial major too (a one-line test fix), one commit, then the same as `GREEN` |
| `CHANGES NEEDED` | stop; edit nothing. The finding goes back to the executor, who knows the design; a fresh reviewer then checks only the delta (`review-2.md`) |

**The gate** is `docs/HANDOVER.md` section 3, step 6, by the commands of its section 4<!-- if:e2e --> (e2e files under the lock, short, named)<!-- endif -->. A red: run it alone; a red your own fix caused is yours. If a fix of yours turns out bigger than you thought, revert it and mark that finding `major`.

Answer in at most 8 lines: `READY TO MERGE <id> head=<sha>` or the verdict, the count per severity, and the top three findings in one line each.
