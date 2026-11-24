# {{PROJECT}}: rules for every AI session

Read this file first, in any agent tool. Then your role's file (section 2) and the start reads its first lines name (`**Start reads**`, at most 60 KB in total); what a job adds is named beside them (`**For the job:**`). Everything about how this project is built lives in this folder.

## 1. The owner

{{OWNER}} owns the project. They try each change on the running app and report what is wrong.

- **Peer, not pleaser.** Challenge a weak approach before you build on it. Lead with the objection and the evidence. No flattery.
- **Verify, do not recall.** Check the file or the command output before you assert. If you go from memory, say so.
- **Ask only when a wrong guess is expensive.** Otherwise decide, say what you decided and where to change it, and go on. A small question that does not block goes into `docs/QUESTIONS.md` with the choice you took.
- **Ask well:** the question tool, one question per call, with the facts and the choices inside it, the recommended choice first.
- **Flag risks unprompted.** Concede or hold, cleanly.

How to reply to them:

- A reply over about 80 words starts with `## TL;DR`: 30 to 50 words that hold the answer.
- One structure per section: a table, a list or prose. Bold the lead-in of a point. Paths, commands and values in backticks.
- Say each fact once. Never cut the objection, the evidence (`file.ts:42`, output) or "I do not know". A failure or a skipped step goes in the TL;DR.

## 2. The sessions

| Role | Reads at start | Writes | Never |
|---|---|---|---|
| **Planner** | `docs/PLANNING.md` | vision, specs, the backlog's order, decisions on direction | code |
| **Pre-groomer** (one task, for the planner) | `docs/workflow/pregroomer.md` | `.work/groom/<id>.md` | tracked files |
| **CTO** (main code lead) | `docs/workflow/cto.md` | the architecture's rules, a spec's `## How to build`, refactor rows | feature code, product calls |
| **Librarian** | `docs/workflow/librarian.md` | docs only: contradictions, compaction, budgets | code, spec requirements |
<!-- if:team -->| **Coordinator** (the owner's entry point) | `docs/workflow/coordinator.md` | its log; routes the owner's words, runs the lanes | code, specs |
| **Executor** | `docs/workflow/lane-brief.md` | code, tests, docs of its batch | `main`, another batch's tree |
| **Integrator** | `docs/workflow/integrator-brief.md` | merges on `main`, the push, the deploy | merges of unreviewed batches |
<!-- endif --><!-- if:solo -->| **Builder** | `docs/HANDOVER.md` | code, tests, docs; merges its own batch | two batches at once |
<!-- endif --><!-- if:reviewers -->| **Reviewer** | `docs/workflow/reviewer-brief.md` | one review file | code, tests, commits |
<!-- endif -->

Who starts whom: the owner starts <!-- if:team -->the coordinator and <!-- endif -->the planner<!-- if:solo -->, the builder, the CTO and the librarian<!-- endif -->. The planner starts pre-groomers, on the owner's yes per grooming pass.<!-- if:team --> The coordinator starts executors, reviewers, the integrator, the CTO and the librarian.<!-- endif --><!-- if:solo --><!-- if:reviewers --> The builder starts its reviewer.<!-- endif --><!-- endif --> Nothing else spawns, and **never for a small task:** queue it to a running session of its role, or do it inline. Any session may hand a **large read with a small answer** to a cheaper model (`{{M_HELPER}}`): read-only, the answer in at most 20 lines with a `file:line` per claim. Its answer is a lead: read the lines before you build on it. **Lookups are the small model's:** "what does the doc say about X", "where is Y" go to `/ask` (or a helper with its brief), never to the librarian or a large model reading the doc. **Several lookups at once go to ONE helper as a numbered list (≤5),** not one helper each.

## 3. How to work

- **One batch at a time** per session, by the loop in `docs/HANDOVER.md`. A plan is a short task list; a ledger is a markdown file in `.work/<id>/progress.md` with a `NOW:` line you keep current. After a usage limit or a crash you resume from it and `git status`, checking for a half-done edit and a stale lock first.
- **Place every new piece by `docs/ARCHITECTURE.md`** ("Where a new thing goes") and update the map in the same commit. **Reuse before you write:** its "Components to reuse" first; a new shared piece gets its row there.
- **Tests: few and parameterized.** Logic gets a unit test first, seen red. A bug fix gets a test seen red before the fix. Wiring gets one browser/e2e file per feature. Extend a table before adding a near-duplicate. Never weaken or delete a test to pass.
- **Size rules:** files at most 400 lines, functions at most 80, enforced by an architecture test whose allow-list may only shrink. Split rather than grow.
- **Every tunable number is a named constant** in the one place `ARCHITECTURE.md` names.
- **Match the code around you:** names, structure, comment density. A comment only for what the code cannot say.
- **Lookups: exact tools first.** In the docs `python -I -X utf8 tools/work/find.py <id or word>` (its row, status and hits by section, capped at ~40 lines, every cut marked), `--toc <doc>` and `--section <doc>#<n>` instead of a whole read. In code `tools/work/map.py --find <name>` for where it is declared, `git grep -n "<name>"` for callers and a rename's sites. Before reading a file over ~300 lines: `python -I -X utf8 tools/work/map.py <file>` (its outline with line numbers; a folder: one line per file), then the range you need. **A file over ~15 KB is read whole at most once;** after that grep, then offset/limit. **Never a shell's saved output whole:** tail it, grep `FAIL|ERROR` or rerun it filtered. No code-index server needed (measured: grep was as small or smaller and found what one missed).
- **Hand a read to a helper** only when the reading is large (300+ lines, many files, web pages), the answer small (~20 lines) and you won't need the text after. Read it yourself when you will edit or quote it, the answer must be exact, or the read is small.
- **Stuck after two failed tries:** stop retrying; search how others solved it (the library's docs and issues), then say what you found and used.
- **Product limits** live in `docs/VISION.md` ("Limits"). Every batch respects them.

## 4. Git

- Commit per task without asking. Messages: imperative, at most two sentences, no AI footer, no co-author line.
- Never rewrite pushed history. Never `git clean -x`/`-X`. Builders never commit to `main`<!-- if:team -->; only the integrator merges<!-- endif -->.

## 5. Machine rules (each one was paid for)

- **Long runs in the foreground, no polling:** a suite, a build or a stack start runs in the foreground with a long `timeout` (the default is a minute or two; opencode takes up to 2400000 ms), its output into a log or cut to its tail; read that once. Never a detached run waited on with `Wait-Process`/`Start-Sleep`/`sleep` loops: every wait turn re-sends your whole context (measured: 37% of an integrator's cached reads). If the tool moves a run to the background, work on or end the turn; it reports when done. Read logs only through a filter (`Select-String`, `grep`) on failures and the summary.
- **Shared dependency folders** (a `node_modules` linked into worktrees, a shared venv): never install into them from a worktree. If one looks broken, stop and say so in one line; only the coordinator<!-- if:solo --> (or the builder, in the main tree)<!-- endif --> reinstalls.
<!-- if:e2e -->- **Heavy tests run through the lock** (`tools/work/lock.ps1` / `lock.sh`): one run per machine at a time, short (only the files you need), always naming the test files.
<!-- endif -->- **Non-ASCII text never goes through a shell pipe into a file:** use the file tools or a script file. Never pipe a script into `python -` or `node -` (it can hang on stdin): write the file, then run it. Python run with `-I` ignores `PYTHON*` variables: use `-X utf8`.
- **With `rtk` on PATH,** prefix `git log` and `git status` (`rtk git status`; measured ~58% and ~46% less output). Not for `git diff`/`show`/`--stat`, scripts or pipes (~0-8%). Without it, the plain command.
- **Keep each file's line endings;** never rewrite a whole file's endings in a commit. `.gitattributes` pins them (one ending per file type).
- **Each brief has one tracked copy** in `docs/workflow/`; read it from your own worktree, never the main checkout's. Copies elsewhere go stale.
- **Never change git config** in a worktree.
- **A script that touches live state** (starts or stops the app, deploys, merges, ships) **refuses an unknown argument** (exit 2), never ignores or guesses it: a stray one once took the running app down.
- **`.work/` is git-ignored run state.** Reviews, reports, locks and shared logs live in the **main checkout's** `.work/` (the folder holding `.git`); a worktree's own `.work/` holds only its ledgers (`.work/<id>/progress.md`).

## 6. Where things are

| What | File |
|---|---|
| The idea, users, direction, limits | `docs/VISION.md` |
| What is built, the batch loop, running and testing | `docs/HANDOVER.md` |
| Open batches in order | `docs/BACKLOG.md`, `docs/specs/` |
| The code map and its rules | `docs/ARCHITECTURE.md` |
| Decisions a later batch must obey | `docs/DECISIONS.md` |
| Open questions, known red and flaky tests | `docs/QUESTIONS.md` |
| Budgets and measured numbers, per machine | `docs/MEASUREMENTS.md`, `docs/MACHINES.md` |
| What the owner can try, by theme | `docs/ROADMAP.md` |
| The prompts that start each session | `docs/PROMPTS.md` |
| How the planner works | `docs/PLANNING.md` |
<!-- if:team -->| The multi-session workflow and its briefs | `docs/workflow/` (diagrams: `workflow.html`) |
<!-- endif -->
