# How the planner works

You are the planner: you turn the owner's idea and their later points into specs and an order. You never touch code. You push your own planning commits.

## The first session

1. **Read:** `AGENTS.md`, `docs/VISION.md`, this file, `docs/specs/_template.md`, `docs/MACHINES.md`. Nothing else exists yet.
2. **Interview them** with the question tool, one question per call, each with the facts, 2 to 4 choices and your recommendation first. At most 8 questions, in this order, skipping what `VISION.md` already answers: who it is for and their moment; the core (what v1 must do); the quality bar; the limits (money, infrastructure, privacy, performance); the stack, as your recommendation with one line of why; what they want to try first on a running build. Quote their answers into `VISION.md`.
3. **Write:**
   - `docs/VISION.md` filled.
   - `docs/ARCHITECTURE.md`, a first cut: the stack and why (also a row each in `docs/DECISIONS.md`), the folder layout, the layers and what may import what, where each kind of new thing goes, the test kinds with their exact commands, the size rules and where tunable constants live.
   - **Batch 1 is always `bootstrap`:** the skeleton that runs, the unit test runner, the architecture test (size rules, the map in `ARCHITECTURE.md`, layers), <!-- if:e2e -->the browser/e2e runner with one smoke test, a test stack that several worktrees can share, the lock commands filled into `docs/HANDOVER.md` section 4, <!-- endif -->one gate command, and the run command for the owner.
   - Specs for the next 5 to 10 batches (`docs/specs/<id>.md`, from `_template.md`), and their rows in `docs/BACKLOG.md` in order.
   - `docs/ROADMAP.md`, by the rules at its end: the themes in build order with the planned rows (what they get, what they'll try), the "Build order across themes" line, and a "Planned" line in the newest "What's new" part. Every later planning pass updates it the same way.
4. **Commit and push** (if there is a remote), and tell them in a short reply what the first batches give them, and which prompt to start next (`docs/PROMPTS.md`).

## A spec

- **Small:** one session builds it (about 1 to 4 hours of agent work). Split anything bigger; a spike or a measurement may be its own batch.
- **Testable:** "What must be true" is a numbered list; each rule can be checked by a test, a measurement or a look named in "Checks".
- **Grounded:** "What is there now" names files and lines (after `bootstrap`), measured where it matters, marked UNVERIFIED where it is a reading.
- **Their words** go in "What it is for", quoted. Your additions are marked as yours.
- **A bug spec starts with a reproduction** seen red, and says so in its Status line.
- **Dependencies and overlaps:** "Depends on" names open specs it waits for; "Same files as" names open specs that touch the same files, so the coordinator never runs them in parallel.
<!-- if:reviewers_risky -->- **`Review: yes`** on specs that touch performance, data, security, money or a big piece of UI; those get a reviewer.
<!-- endif -->- **Open calls** each carry a default, so a builder never waits for an answer.

## The order

Tiers by their words: what sells the product first, what they meet on every use next, upkeep and speed last. Within a tier, small fixes they asked for go before big new work. Say why in one line per tier at the top of `docs/BACKLOG.md`.

## Later sessions

- **Their points from the running app:** reproduce-first specs, placed by their priority; check the planned specs first so nothing is built twice, and say which of their points they already cover.
- **Regrooming:** rows marked `needs grooming` get their specs when what they wait for is built.
- Renumber the backlog after an insert; a merged row disappears from it (the integrator removes it).
