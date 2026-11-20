---
name: vibecode
description: Set up a new project folder for agent-built development (planner, coordinator, executors, reviewers, integrator; parallel tests with locks; optional second machine). Only when the user runs /vibecode or names the skill.
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
| Planner | top | high (xhigh for the first plan) | design and order are the expensive mistakes |
| Coordinator | top | medium | routing and judgement calls, little writing |
| Executor, well-defined batch | top | medium | spec says what; tests say done |
| Executor, design / perf / unclear / camera-like | top | high | needs thinking; set per batch |
| Reviewer | top | high | must criticise; a weak reviewer waves things through |
| Integrator | top (mid if merges are routine) | medium | scripted merges, judgement on reds |
| Read helper (large read, small answer) | small | low | saves the caller's context |
| Builder (solo) | top | high | builds and judges alone |

Write the IDs as `provider/model#variant` (opencode's format for agents, commands and `opencode run -m`). Under a usage cap keep it at most 3 executors and say that every machine shares one cap.

## 6. Deploy

1. Write the answers to `.work/vibecode-answers.json` in the target (create `.work/`):
   ```json
   {"PROJECT": "<folder name or their name for it>", "OWNER": "<their name if known, else 'the owner'>", "IDEA": "<their words>",
    "DATE": "<today>", "SCALE": "team|fleet|solo", "EXECUTORS": 3, "REVIEWERS": "all|risky|none",
    "TESTS": "e2e|unit|perf", "USAGE": "cap|tokens|local",
    "LOCAL_MACHINE": "<one line from the probe>", "LOCAL_OS": "windows|macos|linux",
    "REMOTE": false, "REMOTE_HOST": "", "REMOTE_PATH": "", "REMOTE_OS": "", "REMOTE_MACHINE": "",
    "M_PLANNER": "", "M_COORD": "", "M_EXEC": "", "M_EXEC_HARD": "", "M_REVIEW": "", "M_INTEG": "", "M_HELPER": "", "M_BUILDER": "",
    "NOTES": "<executor count reasoning, missing tools>"}
   ```
2. Run `python -I -X utf8 <skill base>/scripts/deploy.py <target> <target>/.work/vibecode-answers.json`. It prints the created and skipped files, then the prompts.
3. If the folder is not a git repository: `git init`, then commit everything with the message `Set up the vibecode workflow.` Never add a remote and never push: that's their call.

## 7. Answer them

- **TL;DR** (2 lines): what was set up, the next step.
- A table of the sessions: role, model#effort, when it starts.
- **The planner prompt** in a code block: the one thing to paste now, into a new opencode session on the planner's model. Then the coordinator prompt (or, for solo, the builder prompt), to paste after the planner says the first batches are ready. Both are printed by `deploy.py` and kept in `docs/PROMPTS.md`.
- Missing tools from the probe, and anything not verified, as ⚠️ lines.
- Nothing else: no tour of the files.
