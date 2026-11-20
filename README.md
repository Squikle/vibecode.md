# vibecode

An opencode skill and command that set up a fresh project folder for a **multi-agent build**: a planner, a coordinator, executors, reviewers and an integrator, each running in its own session.

## What `/vibecode` does

1. **Checks the folder.** Empty or almost empty: goes on. Existing files: asks before deploying around them (nothing is ever overwritten).
2. **Takes the idea.** The command's arguments, or it asks for 2 to 5 sentences.
3. **Interviews you once** (one question-tool call): scale, reviewers, hardware, tests, how model use is paid.
4. **Probes the machines** (OS, cores, RAM, GPU, tool versions; optionally a second machine over SSH) and picks an executor count.
5. **Picks models and effort** per role from the models actually available to you.
6. **Deploys the templates** with `scripts/deploy.py`: `AGENTS.md`, `docs/` (vision, planning, backlog, roadmap, decisions, handover, machines, measurements, prompts, spec template), role briefs under `docs/workflow/`, and scripts under `tools/work/` (integration, lanes/worktrees, test locks, remote runs). Then `git init` and a first commit; it never adds a remote or pushes.
7. **Prints the session prompts** with model and effort: the planner prompt to paste first, then the coordinator (or solo builder) prompt. They are also kept in `docs/PROMPTS.md`.

It writes no product code; the planner session plans the product.

## Install

Copy the two pieces into your opencode config:

```sh
cp -r skills/vibecode ~/.config/opencode/skills/
cp commands/vibecode.md ~/.config/opencode/commands/
```

Claude Code users may put the skill in `~/.claude/skills/` instead.

## Usage

Make a new folder, open opencode in it, and run:

```
/vibecode [idea]
```

## Template flags

The interview answers decide which template sets are deployed:

| Flag | Options | Effect |
|---|---|---|
| **Scale** | `team` · `fleet` · `solo` | team: coordinator + integrator + 2-3 executors; fleet: up to 6 executors, lanes on a 2nd machine; solo: one builder session runs the loop itself |
| **Reviewers** | `all` · `risky` · `none` | a fresh reviewer session per batch, only for risky batches, or none |
| **Tests** | `unit` · `e2e` · `perf` | e2e adds a shared stack and lock scripts; perf adds a performance gate on one reference machine |
| **Remote** | on · off | a second machine over SSH for test runs and builder lanes |

## Layout

```
skills/vibecode/SKILL.md          the skill
skills/vibecode/scripts/deploy.py copies and fills the templates
skills/vibecode/templates/        always/ team/ reviewers/ e2e/ remote/
commands/vibecode.md              the /vibecode command
```
