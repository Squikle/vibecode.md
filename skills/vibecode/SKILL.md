---
name: vibecode
description: Set up a new project folder for agent-built development (coordinator as entry point, planner with pre-groomers, CTO, librarian, executors, reviewers, integrator; parallel tests with locks; optional second machine). Only when the user runs /vibecode or names the skill.
metadata:
  opencode/autoinvoke: false
---

# vibecode: deploy the multi-agent build workflow into this folder

The user made an empty (or young) folder, opened opencode there and ran `/vibecode [idea]`. You interview them briefly, deploy the workflow's docs, briefs and scripts with one script, and hand them the first prompts. **You do not plan the product and you write no product code:** the planner session does that, started from your prompt.

Keep your own context small: never read the files under `templates/`; `scripts/deploy.py` copies and fills them. Paths below are relative to this skill's base directory (`skill://vibecode`, given when it loads; on disk `~/.config/opencode/skills/vibecode`).

## 1. The folder

- Target = the session's working directory. List it (top level only). Empty, or only a README/notes: go on.
- It holds code or docs already: say what you see in one line and ask (question tool) whether to deploy around it. `deploy.py` never overwrites a file; it lists what it skipped.

## 2. The idea

- The command's arguments are the idea. If they are empty, ask in plain text: **"What are we building? 2 to 5 sentences: what it does, for whom, why it's worth it."** Then stop and wait.
- Don't refine the idea here; the planner interviews them properly.

## 3. The interview: ONE question-tool call, these five questions

Put the recommended option first with "(Recommended)". Keep each option to one line.

| Header | Question | Options |
|---|---|---|
| Scale | How big do we go? | **Team**: you + a coordinator session running 1 integrator and 2-3 executors (Recommended) · **Fleet**: up to 6 executors, lanes on a 2nd machine · **Solo**: one builder session runs the loop itself |
| Reviewers | Review each batch before its slow tests? | **Every batch**, fresh reviewer, high effort (Recommended) · **Only risky batches** (perf, data, security, big UI) · **None** |
| Hardware | Other machines to use? | **This machine only** · **A 2nd machine over SSH** for test runs and builder lanes · **Decide later** (docs get the slot, nothing is wired) |
| Tests | How heavy will testing be? | **Unit + browser/e2e** with a shared stack and a lock (Recommended for apps with a UI) · **Unit only** (libraries, CLIs) · **e2e + a performance gate** measured on one reference machine |
| Usage | How is model use paid? | **Subscription with a usage cap** (fewer parallel sessions, cheap effort where safe) · **Pay per token** · **Local models too** (cheap reads go local) |

If they picked a second machine, ask one more question-tool call: its ssh host alias, the repo path there, its OS (options: macOS · Linux · Windows). Then probe it (below).

## 4. Probe the machines (one shell call each, short output)

- **This machine:** OS, CPU cores, RAM, GPU (`nvidia-smi --query-gpu=name,memory.total --format=csv,noheader` if present), and the versions of `git`, `docker`, `node`, `python`, `pwsh`. Note what is missing; don't install anything.
- **Second machine:** `ssh -o ConnectTimeout=8 <host> "uname -a; (nproc || sysctl -n hw.ncpu); (free -g | head -2 || sysctl -n hw.memsize); git --version; docker --version; node --version; python3 --version"`. Unreachable: say so, keep `remote` on, and mark it "not verified" in `docs/MACHINES.md`.
- **Executor count:** start from the scale (Team 3, Fleet 6, Solo 1). Lower it to cores/4 on the busiest machine, and to 3 under a usage cap. One line of reasoning in the answers file.

## 5. Models and effort

Look up the exact model IDs and variants available to them (opencode: the `models` tool; prefer their current provider first). Never invent an ID. Pick three tiers: **top** (largest current), **mid**, **small**. Then fill:

| Role | Model | Effort | Why |
|---|---|---|---|
| Planner | top (the model they like best for design) | high (xhigh for the first plan) | design and order are the expensive mistakes; takes a queue of jobs, retires at ~250k context |
| Pre-groomer (one per heavy task) | top | high | digs into one task with a small, fresh context |
| CTO (main code lead) | top | high (xhigh for a health sweep) | architecture, how heavy batches are built, refactor rows; retires at ~250k context |
| Librarian (docs only) | mid | high | contradictions, compaction, doc budgets |
| Coordinator (entry point) | mid (top over 4 lanes) | medium | routing, slots, status; product calls go to the planner, technical ones to the CTO |
| Executor, well-defined batch | top | medium | spec says what; tests say done |
| Executor, design / perf / unclear / camera-like | top | high | needs thinking; set per batch |
| Reviewer (code) | top | high | must criticise; a weak reviewer waves things through; also fixes its own notes and runs the gate |
| Reviewer, delta of docs or tools only | mid | high | a small second look at a fix |
| Integrator | mid | high | scripted merges, judgement on reds; short-lived (~4 merges or 150k), hands conflicts it can't settle to the coordinator |
| Read helper (large read, small answer) | small | low | saves the caller's context |
| Builder (solo) | top | high | builds and judges alone |

Write the IDs as `provider/model#variant` (opencode's format for agents, commands and `opencode run -m`). Under a usage cap keep it at most 3 executors and say that every machine shares one cap; after the cap's reset the owner types `continue` and the coordinator resumes each session from its progress file.

**Compaction (advise it in your answer; never edit their opencode config unasked).** Role sessions retire instead of compacting, but a long lane compacts, and early is cheaper:
- **A lane-only alias per model** in the provider's `models`: `"<model>-lane": { "modelID": "<model>", "limit": { "context": <its window>, "input": 171500 } }`. Auto-compaction fires at `input` minus `compaction.buffer`, so with `"buffer": "30%"` that is ~120k. The coordinator spawns executors, reviewers and integrators on `<provider>/<model>-lane#<variant>`; the owner's own sessions keep the plain model.
- **`"compaction": { "auto": true, "buffer": "30%", "keep": { "tokens": 4000 } }`.** opencode counts the kept tail at chars/4, Claude's tokenizer runs ~2.4 chars/token and the cut snaps back to the last user message: `keep` 6000 kept 13-18k real tokens; 4000 keeps ~7k.
- Measured by replaying one long coordinator session: auto-compaction at ~70k with `keep` 4000 cut its lifetime cache reads by 38% against compacting by hand at ~120k. **The trade-off:** earlier compaction summarizes more often, so detail is lost sooner. Default to ~120k for long lanes and the coordinator; ~70k only where a session is cheap or short-lived (small-model chats). The vibecode repo's `OPTIMIZATIONS.md` has the numbers.

## 6. Deploy

1. Write the answers to `.work/vibecode-answers.json` in the target (create `.work/`):
   ```json
   {"PROJECT": "<folder name or their name for it>", "OWNER": "<their name if known, else 'the owner'>", "IDEA": "<their words>",
    "DATE": "<today>", "SCALE": "team|fleet|solo", "EXECUTORS": 3, "REVIEWERS": "all|risky|none",
    "TESTS": "e2e|unit|perf", "USAGE": "cap|tokens|local",
    "LOCAL_MACHINE": "<one line from the probe>", "LOCAL_OS": "windows|macos|linux",
    "REMOTE": false, "REMOTE_HOST": "", "REMOTE_PATH": "", "REMOTE_OS": "", "REMOTE_MACHINE": "",
    "M_PLANNER": "", "M_COORD": "", "M_EXEC": "", "M_EXEC_HARD": "", "M_REVIEW": "", "M_REVIEW_DELTA": "<the mid model>", "M_INTEG": "", "M_HELPER": "", "M_BUILDER": "",
    "M_PREGROOM": "", "M_CTO": "", "M_DOCS": "",
    "NOTES": "<executor count reasoning, missing tools>"}
   ```
2. Run `python -I -X utf8 <skill base>/scripts/deploy.py <target> <target>/.work/vibecode-answers.json`. It prints the created and skipped files, then the prompts.
3. If the folder is not a git repository: `git init`, then commit everything with the message `Set up the vibecode workflow.` Never add a remote and never push: that's their call.

## 7. Answer them

- **TL;DR** (2 lines): what was set up, the next step.
- A table of the sessions: role, model#effort, when it starts.
- **The planner prompt** in a code block: the one thing to paste now, into a new opencode session on the planner's model. Then the coordinator prompt (or, for solo, the builder prompt), to paste after the planner says the first batches are ready. Both are printed by `deploy.py` and kept in `docs/PROMPTS.md`.
- The compaction advice (section 5) in 2-3 lines, with the alias names for their models.
- Missing tools from the probe, and anything not verified, as ⚠️ lines.
- Nothing else: no tour of the files.
