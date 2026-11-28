# Planning: the rules the product lead works by

The product lead's brief is `docs/workflow/product.md`; this file holds its rules, read by section (`find.py --section "PLANNING#<heading>"`), whole only in the first session. <!-- if:team -->The coordinator reads "The order" too: it owns it.<!-- endif -->

## The first session

1. **Read** the start reads and the files "For the job" names. Nothing else exists yet.
2. **Interview them** with the question tool, one question per call, each with the facts, 2 to 4 choices and your recommendation first. At most 8 questions, in this order, skipping what `VISION.md` already answers: who it is for and their moment; the core (what v1 must do); the quality bar; the limits (money, infrastructure, privacy, performance); the stack, as your recommendation with one line of why; what they want to try first on a running build. Quote their answers into `VISION.md`.
3. **Write:**
   - `docs/VISION.md` filled.
   - `docs/ARCHITECTURE.md`, a first cut: the stack and why (also a row each in `docs/DECISIONS.md`), the folder layout, the layers and what may import what, where each kind of new thing goes, the test kinds with their exact commands, the size rules and where tunable constants live.
   - **Batch 1 is always `bootstrap`:** the skeleton that runs, the unit test runner, the architecture test (size rules, the map in `ARCHITECTURE.md`, layers), <!-- if:e2e -->the browser/e2e runner with one smoke test, a test stack that several worktrees can share, the lock commands filled into `docs/HANDOVER.md` section 4, <!-- endif -->one gate command, and the run command for the owner.
   - Specs for the next 5 to 10 batches (`docs/specs/<id>.md`, from `_template.md`), and their rows in `docs/BACKLOG.md` in order, by "The order" below.
   - `docs/kmap/modules.md`: a first row per part of the product you can already name (its id, one line, the spec ids and paths it starts from), so `tools/work/kmap.py` groups what belongs together.
   - `docs/ROADMAP.md`, by the rules at its end: the themes in build order with the planned rows (what they get, what they'll try), the "Build order across themes" line, and a "Planned" line in the newest "What's new" part. Every later planning pass updates it the same way.
4. **Commit and push** (if there is a remote), and tell them in a short reply what the first batches give them, and which prompt to start next (`docs/PROMPTS.md`).

## A spec

- **Small:** one session builds it (about 1 to 4 hours of agent work). Split anything bigger; a spike or a measurement may be its own batch.
- **Testable:** "What must be true" is a numbered list; each rule can be checked by a test, a measurement or a look named in "Checks". **A rule with no failing test doesn't ship:** a builder writes its test first and sees it red.
- **Edges:** "What must be true" ends with the template's `Edges` line, each edge answered or `not this batch`, before the spec is `ready`; the architect names an open one back to the product lead.
- **Grounded:** "What is there now" names files and lines (after `bootstrap`), measured where it matters, marked UNVERIFIED where it is a reading.
- **Their words** go in "What it is for", quoted. Your additions are marked as yours.
- **A bug spec starts with a reproduction** seen red, and says so in its Status line.
- **Dependencies and overlaps:** "Depends on" names open specs it waits for; "Same files as" names open specs that touch the same files, so the coordinator never runs them in parallel. Ids in backticks, prose after a period.
- **Links:** the header's `Links:` line names what else it relates to, from the template's closed list of types; `tools/work/kmap.py check` turns a dangling id or an unknown type red. A link you learned while writing costs one line now and saves every later reader a search.
<!-- if:reviewers_risky -->- **`Review: yes`** on specs that touch performance, data, security, money or a big piece of UI; those get a reviewer.
<!-- endif -->- **Open calls** each carry a default, so a builder never waits for an answer.
- **`Tech: light | heavy`** in the Status block: heavy when it adds a seam, crosses two areas of the map, adds a dependency or touches a file the architect lists. In each pass the architect writes a heavy spec's `## How to build` and a light one's `Fit:` line before it is built (`docs/workflow/architect.md`), then a fresh plan reviewer checks the pass against the owner's words (`docs/workflow/plan-review.md`).

## The order

**One owner:** BACKLOG's row order is the build order, and the <!-- if:team -->coordinator<!-- endif --><!-- if:solo -->product lead<!-- endif --> owns it after the first session. The product lead adds rows where it proposes them (one line of why) and owns what a row is; the architect sets `Waits on`, `Same files as` and refactor rows; <!-- if:team -->only the coordinator moves an existing row. <!-- endif -->No priority list lives anywhere else (not a checkpoint, a handover, chat).

**The owner's word, once:** when they say what comes first, later, never or different, it goes into BACKLOG in that same turn: `python -I -X utf8 tools/work/order.py word "<their words>" --rows <ids> --top | --after <id> | --before <id> | --hold | --drop`; a scope change adds `--needs "product: <what>"` and holds the row until its spec changes. The row's status gets `(owner <date>)`, `docs/log/order.md` a WORD line; both files are committed by path. `order.py check` finds a row moved against the newest word.

**Unasked, only mechanical rules:** what follows. A tier they never stated, a split, a merge or a re-scope is a judgement: one line to the product lead's inbox.

Tiers by their words: what sells the product first, what they meet on every use next, upkeep and speed last. Within a tier, small fixes they asked for go before big new work. Say why in one line per tier at the top of `docs/BACKLOG.md`.

**Built work goes first** (it outranks the order above; the coordinator's side is in `docs/workflow/coordinator.md`):

1. **A built branch outranks an unstarted row.** A batch whose branch is built (READY, reviewed, or a port of built work) is merged or ported before any lane starts a row that is not built, unless the owner says otherwise.
2. **A dependency must be real, in code:** the other row changes the same behaviour, or the merge cannot pass without it. "Tidier after the split" or "best after X" is a note for `How to build`, never a `Depends on`. A file at its line limit is solved by a new file in the batch, not by waiting.
3. **A real blocker of built work is next:** if built work waits on an unbuilt row, that row moves to the top and starts at once.
4. **A branch gets old fast:** a built branch more than 1 day or 200 commits behind `main` is flagged in the coordinator's next report with the reason it waits (`git rev-list --count <branch>..origin/main`; `git log -1 --format=%cs <branch>`); recheck that reason the same day.

## Later sessions

- **Their points from the running app:** reproduce-first specs, proposed where their priority puts them; check the planned specs first so nothing is built twice (`kmap.py near "<their words>"`), and say which of their points they already cover.
- **Regrooming:** rows marked `needs grooming` get their specs when what they wait for is built.
- A merged row disappears from the backlog (the integrator removes it). Moves go through `order.py word`; any other backlog edit is a small script that asserts each string it replaces exists exactly once, then a diff of the sorted rows against the last commit, every changed line read: a script that keys rows by their first word loses rows that share it.

## Grooming: alone or T-shaped

You groom in breadth (the bar of the T); a pre-groomer digs into one task (the stem), so your context holds the whole picture, not every detail.

1. **Ask once per pass** (question tool, multiple choice): the tasks, each with your recommendation; pre-groom the heavy or unclear ones (a new feature, a new seam, where the owner's feel matters), groom small ones yourself.
2. **One pre-groomer per chosen task** on `{{M_PREGROOM}}`, with `docs/workflow/pregroomer.md` and a brief of at most 15 lines: the owner's words verbatim, the id, the specs, decisions and files to start from (`kmap.py <id> --level 3` lists them), and what you already know does not fit. They run in parallel.
3. **Check its report** (`.work/groom/<id>.md`) against the wide view: other specs and their order, decisions, the architecture and its budgets, what is building now. Steer it by its session, one message naming what does not fit and why; two rounds at most, then finish it yourself.
4. **Write the spec yourself:** you choose the option, the rules and the tech weight, and propose its place.
5. **Close the pass** by `docs/workflow/product.md` step 5: the architect's gate, then the plan review, then `PLANNED <ids>`.
