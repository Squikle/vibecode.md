# Token optimizations

Every cut the workflow makes in tokens or time, what it fixes, and what it saves. All of them were found and measured in the project this workflow was distilled from. A number is **MEASURED** (with its source) or an **ESTIMATE** (with the math). Where neither exists it says **not measured**.

Status: ✅ shipped in the template · 🔧 in progress in the source project · planned.
Template paths are relative to `skills/vibecode/templates/`.

## Sources

| Key | What | Scope |
|---|---|---|
| **[A]** | Per-session tool audit (2026-11-23), the ancestor of `tools/work/session-audit.py` | 16 sessions: 5 integrators, 3 reviewers, 4 librarians, 1 planner, 3 builders. 1,534 turns, **156.02M** context tokens |
| **[CI]** | Local CI study (2026-11-23) | 7 integrator sessions, 12 merges, 22.07M cache-read tokens |
| **[CF]** | Context study (2026-11-21) | Code-index server vs `grep` vs outline, on the same lookups; doc growth per day |
| **[CP]** | Compaction study (2026-11-22) | One long coordinator session (18.33M cache reads), replayed by a model of its real per-request growth. The replay hit the real total within +2.8% |
| **[Q]** | The source project's merged workflow changes (2026-11-23) | File sizes before and after |
| **[OPS]** | The source project's coordinator notes | Cache price and lifetime as observed |

**How to read [A].** `ctxSum` is the provider's own count: input + cache read + cache write, summed over every turn. `reRead` is an estimate: a tool output's chars/4, times the turns after it. It ignores compaction, so treat it as an **upper bound**.

## The optimizations

| # | What | Problem it fixes | Who gains | Savings | Status | Where in the template |
|---|---|---|---|---|---|---|
| 1 | **Long runs in the foreground, no polling.** One call with a long `timeout`, output cut to its tail and read once | Detached runs waited on with `Wait-Process`/`Start-Sleep` loops. Every wait turn re-sends the whole context and learns nothing | integrators, builders | **MEASURED [CI]:** wait-poll = **37%** of integrator cache reads (≈8.2M of 22.07M). **ESTIMATE [A]:** 96 wait calls × each session's mean context per turn = **6.71M (4.3% of all)**; integrators alone 3.32M = 20% of their role | ✅ | `always/AGENTS.md` §5, `team/docs/workflow/lane-brief.md`, `integrator-brief.md`, `remote/tools/work/remote-run.ps1` |
| 2 | **Local gate script** (`gate.py`): one call, one JSON result, a known-reds file | The integrator runs each check by hand and reads raw output. Mechanical work = **71%** of its cache reads [CI] | integrators | **ESTIMATE [CI]:** ~1.1M cache read saved per merge (~60% of 1.84M per merge). Overlaps #1 | 🔧 | (not yet) |
| 3 | **Doc budgets and start reads**, checked by a script | Docs grow: one questions file reached 151 KB at +29 KB/day [CF]. Docs read whole are re-sent on every later turn: [A] HANDOVER at 51.9k chars (0.53M reRead), ROADMAP at 42.4k (1.04M), PLANNING at 23.4k (0.58M) | everyone, every start | **MEASURED [A]:** 2.15M reRead from those 3 reads. **ESTIMATE:** a 52k-char doc held to 30 KB is ≥ 22k chars ≈ 5.5k tokens less on every later turn (chars/4) | ✅ | `always/tools/work/check-docs.py`, `always/docs/workflow/librarian.md` §3, each brief's first lines |
| 4 | **Searched docs are never read whole** (QUESTIONS, DECISIONS, ROADMAP, MEASUREMENTS) | Logs and registers keep growing; one grep hit costs a line, one read costs the whole file | everyone | not measured on its own (part of #3) | ✅ | `check-docs.py` `SEARCHED`, `librarian.md` §3 |
| 5 | **A file over ~15 KB is read whole at most once**; after that grep, then offset/limit | Same big file read whole twice: [A] a 17.6k-char source file twice in one builder session (1.38M + 1.37M reRead). [Q]: QUESTIONS.md twice (27k + 30k chars), a 24 KB client file twice, ROADMAP whole | builders, reviewers | **MEASURED [A]:** the duplicate read alone = **1.37M reRead (0.9%)** | ✅ | `always/AGENTS.md` §3 |
| 6 | **Outline before a big file** (`tools/work/map.py`): line count plus declarations with line numbers, then read the range you need | `read` is **50.8%** of all tool output in [A] (1,965k of 3,867k chars). Outline calls were 0-8 per session | builders, reviewers | **MEASURED [CF]:** outline 420 chars vs 14,462 for the whole file (**34× less**); folder outline 920 chars vs an 11,048-char area doc. **Upper bound [A]:** whole-file code reads ≥ 15k chars = 4.87M reRead (3.1%); an outline plus a range keeps only part of it | ✅ | `always/tools/work/map.py`, `always/AGENTS.md` §3 |
| 7 | **`git grep`, not a code-index server** (Serena) | The server serves the folder the tool started in, not your worktree. Its cold first call returned a false empty result after 30.3 s. Its instructions call costs ~1,900 tokens | builders, reviewers | **MEASURED [CF]:** no saving. `grep` was smaller on 2 of 3 lookups, equal on 1, and complete on all 3; it found a key in a string the server missed | ✅ | `always/AGENTS.md` §3 |
| 8 | **Hand a read to a helper only when all three hold:** the read is large, the answer is small, and you won't need the text after | A helper's brief and answer cost more than a small read; text you'll edit or quote has to be read anyway | everyone | not measured | ✅ | `always/AGENTS.md` §3 |
| 9 | **Cheapest tool first:** a script or `grep` → a small-model lookup (`/ask`) → a big model only for judgment | Role sessions (librarian, CTO) spawned to find one fact | coordinator, everyone | not measured (the gap is the provider's price ratio between the models) | ✅ | `team/docs/workflow/coordinator.md` "What goes where", `always/dot-opencode/commands/ask.md` |
| 10 | **`rtk` prefix for `git log` / `git status`** | Verbose git output | everyone | **MEASURED** (`rtk gain`, source lane brief): `git log` −58%, `git status` −46%; `git diff` −2%, `--stat` 0%, `git show` −8% (not worth it). Share: `git` output = 19.2% of tool chars [A], but the log/status part was not split out: **not measured** | ✅ | `always/AGENTS.md` §5 |
| 11 | **Integrator queue file kept live and small:** next job, last job in one line, live traps, known reds. Old lines go to an archive nobody reads | The queue file had grown to 54.5 KB, mostly old job logs, and was read at every integrator start | integrators | **MEASURED [Q]:** 54.5 KB → **3.7 KB**. **MEASURED [A]:** one integrator's reads of it = 1.58M reRead (1.0%). **ESTIMATE:** (54.5 − 3.7) KB / 4 ≈ 12.7k tokens less per turn → **0.46M-1.6M per integrator** at 36-126 turns | ✅ | `team/docs/workflow/integrator-brief.md`, `coordinator.md`, `librarian.md` §3 |
| 12 | **Split a brief's rarely-needed part into its own file**, read only before that job (e.g. browser runs and machines) | Every builder start paid for browser/machine rules a batch with no browser files never uses | builders | **MEASURED [Q]:** lane brief 16.4 KB → 10.3 KB core + 4.1 KB browser part (151 of 166 backticked names kept verbatim; the other 15 were rewordings or one stale rule dropped). **ESTIMATE:** 6.1 KB / 4 ≈ 1.5k tokens less per builder turn → at most 0.89M over [A]'s 585 builder turns (≤ 0.6%) | ✅ (as the librarian's rule; the template's lane brief is 4.1 KB, so there's nothing to split yet) | `always/docs/workflow/librarian.md` §3 |
| 13 | **Short-lived integrator** (~4 merges or 150k context), restarted from its brief and the queue file | A held integrator re-sends every earlier merge on every turn: an all-day one read **39M** cached tokens, ~73k per call (MEASURED, earlier audit) | integrators | Not measured as a cut. [A]'s integrators ran at 26-75k mean context per turn | ✅ | `team/docs/workflow/integrator-brief.md`, `coordinator.md` |
| 14 | **Merge bookkeeping by script** (`integrate.py`: merge, backlog rows, built rows, decision numbers, spec moves) | Hand-editing tables at every merge | integrators | not measured separately. The merge step is still **18%** of integrator cache reads [CI] | ✅ | `always/tools/work/integrate.py` |
| 15 | **Role sessions retire, not compact:** the planner and CTO take a queue of jobs and stop at ~200-250k (hard cap 300k) | A long planner averaged 185k context per turn over 100 turns (18.52M) [A]; a builder averaged 197k | planner, CTO | **ESTIMATE:** growth is ~linear, so a session that retires at 250k averages ~125k plus its start reads → ~30% less per turn than [A]'s planner (185k → ~130k) | ✅ | `team/docs/workflow/coordinator.md` "Sessions and context", `always/docs/PROMPTS.md` |
| 16 | **Warm over fresh:** resume a session whose last turn was under ~50 min ago; put several jobs in one turn | A fresh session re-pays its start reads at full price | everyone | **MEASURED [OPS]:** a cached read costs ~0.1× an input token. Gaps of 5-60 min found 7-10% cold; after 60 min it's cold | ✅ | `coordinator.md` "Sessions and context" |
| 17 | **Compaction: lane-only model aliases** that auto-compact at ~120k, and a 4000-token kept tail | Hand compaction at ~120-170k. `keep` 6000 actually kept 12.9-18.3k real tokens: opencode counts chars/4, Claude's tokenizer runs ~2.4 chars/token, and the cut snaps back to the last user message | long lanes, coordinator | **MEASURED [CP] (replay):** auto at ~70k with `keep` 4000 → **10.19M vs 16.75M cache reads (−38%)** against compacting by hand at ~120k. `keep` 4000 alone at 120k: −11% | ✅ advice in SKILL.md (in the source config: `keep` 4000 and the ~120k aliases; 70k is planned) | `skills/vibecode/SKILL.md` §5 |
| 18 | **Small fixed prompt** (system + tool schemas + skills list) | The part no compaction can touch | everyone | **MEASURED [CP]:** **16.7%** of one coordinator session's cache reads (3.05M of 18.33M). The cut from a trim: not measured | planned | (not yet) |
| 19 | **The reviewer finishes a green batch** (fixes its own minors, runs the gate); the builder retires at READY and is not resumed | A builder resumed only to fix nits re-reads its whole context | builders | **ESTIMATE:** one builder resume saved per green batch, at [A]'s builders' 51-122k mean context per turn. Not measured | ✅ | `reviewers/docs/workflow/reviewer-brief.md`, `team/docs/workflow/lane-brief.md` 4b |
| 20 | **Fresh reviewer per round; a delta review of docs/tools runs on the mid model** | A held reviewer carries old rounds; a top model reviewing a doc fix | reviewers | not measured | ✅ | `coordinator.md` loop, `SKILL.md` §5 |
| 21 | **T-shaped grooming:** pre-groomers dig into one heavy task each with a small fresh context | The planner's context filled up with deep dives | planner | not measured | ✅ | `always/docs/workflow/pregroomer.md`, `always/docs/PLANNING.md` |
| 22 | **`progress.md` with a `NOW:` line** per session | Resuming after a cutoff or usage limit by re-reading the chat | everyone | not measured | ✅ | `always/AGENTS.md` §3, every brief |
| 23 | **Known reds listed** (QUESTIONS.md; a red passes a merge only if listed or 2 of 2 alone) | Re-triaging the same red every merge: the full suite was never green, and **616 of 904** runs had a red [CI] | integrators, reviewers | not measured | ✅ (the source's `known-reds.json` for the gate is 🔧) | `team/docs/workflow/integrator-brief.md` §3, `lane-brief.md` step 5 |
| 24 | **Recurring token audit, unasked:** `session-audit.py` on each retired session (read only its `SMELLS:` line), `--peek` every third heartbeat; a smell seen twice becomes a fix | Waste nobody looked for: polling, a 54 KB queue read at every start, and 111 greps in one planner session went unseen for days | coordinator → everyone | No direct cut; it found #1, #5, #11 and #12 | ✅ | `always/tools/work/session-audit.py`, `team/docs/workflow/coordinator.md` "Token audit" |
| 25 | **Doc finder** (`find.py`): one call returns the matching rows/sections, not the whole-line grep hits of a folder | Folder-wide greps over the docs: [A] 0.97M and 1.44M reRead in two sessions. 111 `git grep` calls (466k chars) in one planner session (coordinator-reported; not in the [A] file) | planner, librarian, builders | **Upper bound [A]:** 2.41M reRead (1.5%) | 🔧 | (not yet) |
| 26 | **Review pack:** the reviewer gets the diff, the spec's contract and the gate JSON in one file | A reviewer gathering them in many calls | reviewers | not measured (it waits for the gate's JSON format) | planned | (not yet) |
| 27 | **Quiet test runs:** pass/fail summary plus the failures only | Raw runner output in context | integrators, builders | not measured; part of #2's 71% mechanical share | planned (UNVERIFIED which change will carry it) | (not yet) |
| 28 | **Docs vault** (generated Obsidian view of backlog, roadmap, decisions, specs) | The owner reading raw tables | the owner | not a token cut | shipped in the source, not ported (it ties doc shapes to a parser) | (not yet) |

## Before and after, one merge

**MEASURED, one pair of samples:** the first integrator after #1 and #11 shipped merged a tools + 1 test batch (no redeploy, no conflict) in **37 turns, 1.73M** context tokens summed, first context 17k, 0 poll calls. The comparable merge before them (tools + docs, no redeploy) took **45 turns, 3.37M**. That's **−49%** like for like. Credit is joint: the queue split (#11: 54.5 → 3.7 KB read at start), no polling (#1), and a job prompt naming the exact steps. One pair isn't a trend.

## Share of total

Base: [A], **156.02M** context tokens over 16 sessions.

| Role | Sessions | Turns | Context | Share |
|---|---:|---:|---:|---:|
| Builders | 3 | 585 | 60.09M | 38.5% |
| Librarians | 4 | 379 | 49.59M | 31.8% |
| Planner | 1 | 100 | 18.52M | 11.9% |
| Integrators | 5 | 303 | 16.57M | 10.6% |
| Reviewers | 3 | 167 | 11.25M | 7.2% |

What each item cuts, as a share of that total. The rows overlap (#1 sits inside #2; one source file's double read is in both #5 and #6), so **don't add them up**.

| Item | Cut | Share | Kind and math |
|---|---:|---:|---|
| #2 Local gate script | ~9.9M | ~6.4% | ESTIMATE: 60% [CI] × integrators' 16.57M. Includes #1's integrator part |
| #1 No polling | 6.71M | 4.3% | ESTIMATE: 96 wait calls × each session's mean context per turn |
| #15 Planner retires at 250k | ~5.6M | ~3.6% | ESTIMATE: 30% × the planner's 18.52M |
| #6 Outline before a big file | ≤ 4.87M | ≤ 3.1% | Upper bound: reRead of whole-file code reads ≥ 15k chars; an outline + range keeps part |
| #25 Doc finder | ≤ 2.41M | ≤ 1.5% | Upper bound: reRead of two folder-wide greps |
| #3 Doc budgets | ≤ 2.15M | ≤ 1.4% | Upper bound: reRead of three whole-doc reads; a budget keeps 30 KB of each |
| #11 Queue file kept small | 1.58M | 1.0% | MEASURED reRead of one integrator's queue reads. Up to ~2.5% if all 5 integrators read the 54.5 KB file (12.7k tokens × 303 turns) |
| #5 15 KB file read once | 1.37M | 0.9% | MEASURED reRead of one duplicate whole read |
| #12 Rarely-needed part split out | ≤ 0.89M | ≤ 0.6% | ESTIMATE: 1.5k tokens × 585 builder turns, if no batch ran a browser file |
| #7 grep, not a code-index server | 0 | 0% | MEASURED: no saving; the gain is correctness and the ~1,900-token instructions call |
| #10 rtk | — | not measured | `git` output is 19.2% of tool chars in [A]; the log/status part of it was not split out |
| #17 Compaction at ~70k | — | not measured here | −38% on one coordinator session [CP]; [A]'s sessions mostly retire rather than compact |
| #18 Small fixed prompt | — | not measured here | 16.7% of one coordinator session [CP] is fixed; how much a trim cuts is unknown |
| all others | — | not measured | no per-item data in [A] |

## Keeping this file true

When the source project merges a workflow change, port its generic part into `templates/` and add or update its row here: the number, its kind, the status. Something still in progress stays 🔧 or planned and never goes into the template.
