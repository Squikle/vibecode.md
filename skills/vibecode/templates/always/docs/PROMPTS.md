# The prompts that start each session

Start a new opencode session in this folder, pick the model named, and paste the prompt.<!-- if:team --> The coordinator writes the prompts of the sessions it spawns itself (`docs/workflow/coordinator.md`).<!-- endif -->

## Start here

**1. Planner** on `{{M_PLANNER}}` (the first plan deserves the top effort the model offers):

```text
You are the planner of {{PROJECT}}. Read AGENTS.md, then docs/PLANNING.md, and do "The first session": interview me, then write the vision, the architecture's first cut, the bootstrap spec and the next specs in order. Touch no code.
```

<!-- if:team -->**2. Coordinator** on `{{M_COORD}}`, once the planner says the first batches are ready:

```text
You are the coordinator of {{PROJECT}}. Read AGENTS.md, then docs/workflow/coordinator.md, and do its "Start". At most {{EXECUTORS}} executors and 1 integrator at a time; reviewers on top. Build docs/BACKLOG.md in order. Give me a short status now and after each merge.
```
<!-- endif --><!-- if:solo -->**2. Builder** on `{{M_BUILDER}}`, once the planner says the first batches are ready:

```text
You are the builder of {{PROJECT}}. Read AGENTS.md and docs/HANDOVER.md. Build docs/BACKLOG.md top-down, one batch at a time, by the loop in HANDOVER section 3, merging each into main yourself. Give me a short status after each merge.
```
<!-- endif -->

## Later

**The planner again** (on `{{M_PLANNER}}`), after they tried the app:

```text
You are the planner of {{PROJECT}}. Read AGENTS.md and docs/PLANNING.md ("Later sessions"), then docs/BACKLOG.md. My points from the app: <paste>. Check what is planned first, write reproduce-first specs, place them by my priority, push.
```

<!-- if:team -->**Resume after a usage cutoff or a reboot** (coordinator, on `{{M_COORD}}`):

```text
You are the coordinator of {{PROJECT}}. Read AGENTS.md, docs/workflow/coordinator.md, then .work/RESUME.md if it exists, else the bottom of .work/coordinator-log.md. Do "Start" (checks), then start FRESH sessions that resume from git status and their ledgers .work/<id>/progress.md.
```

**Wrap up** (to the running coordinator): `wrap up` — every session commits its WIP, writes its next steps in its ledger and starts nothing new; the coordinator writes `.work/RESUME.md`.
<!-- endif -->

## Models and effort

| Role | Model | When |
|---|---|---|
| Planner | `{{M_PLANNER}}` | first plan, later passes |
<!-- if:team -->| Coordinator | `{{M_COORD}}` | the whole run |
| Executor, well-defined batch | `{{M_EXEC}}` | most batches |
| Executor, design / performance / unclear batch | `{{M_EXEC_HARD}}` | the coordinator picks per batch |
| Integrator | `{{M_INTEG}}` | one at a time |
<!-- endif --><!-- if:solo -->| Builder | `{{M_BUILDER}}` | the whole run |
<!-- endif --><!-- if:reviewers -->| Reviewer (fresh per review) | `{{M_REVIEW}}` | {{REVIEW_STEP}} |
<!-- endif -->| Read helper | `{{M_HELPER}}` | a large read with a small answer |
<!-- if:cap -->
**Usage cap:** every session on every machine draws from one cap. Prefer fewer sessions at the right effort over many; at a cutoff, the ledgers make the restart cheap.
<!-- endif -->
