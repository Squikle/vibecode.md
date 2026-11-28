# The 4-hour routine: friction, doubt, role telemetry

The coordinator's: three no-model scans of the sessions, each with a state file so a finding alerts once. Read this when the routine runs or a flag needs a decision. Every finding ends as a fix at its source, written down as a lesson with its guard (`coordinator-more.md#5`).

## 1. The runs

One shell automation every 4 hours (no model), its exit code the alert:

```text
python -I -X utf8 tools/work/friction.py --since 48h --state .work/friction-state.json
python -I -X utf8 tools/work/doubt.py --state .work/doubt-state.json
python -I -X utf8 tools/work/role-telemetry.py --since 24h --state .work/role-telemetry.json --log .work/log/tooluse.md
```

A Monday digest runs role telemetry with `--since 7d` and no `--state`: the week's numbers, for one fix.

## 2. Friction (the environment)

- Exit 1 = a NEW PROMOTE: read the report, unknown clusters first.
- **A PROMOTE (5+ sessions) is fixed at source first** (install, env var, wrapper), checked against one session's lines; a brief line only for what can't be fixed; its regex joins `TRAPS`.
- Then `--fixed <trap>=<date>`: the report counts its hits since (an old shell).

## 3. Doubt (the code)

A PROMOTE (3+ lanes doubted a file) goes to the architect's queue as a patch-vs-rework look, never straight to a lane.

## 4. Role telemetry (the roles' own tools and the knowledge map)

Per role: raw commands a project tool covers (each with its tool), inline helpers typed again and again, and, in sessions that asked `kmap.py`, reads of files it never returned. The first run is the baseline; a flag is a share 5 points over it, a rule new in a role's top three, a role that stopped calling its tools, a helper typed 10+ times in 3+ sessions, or half of 20+ reads off the map.

**A flag climbs one rung at a time, each with its guard:**

| Rung | When | Do | Guard |
|---|---|---|---|
| 1. See | a NEW flag | read 3 examples: `role-telemetry.py --examples <role> <rule>` | a false alarm: tune the rule, with a test |
| 2. Tool gap | the raw use needs what no tool does | build it (a subcommand, a flag) | its unit test |
| 3. Lesson | the tool covers it | a lesson with its guard | - |
| 4. Brief line | | one line in the role's brief: `<raw> -> <tool>` | next week's count down by half, else back to rung 1 |
| 5. Nudge | still high after a week | a per-role permission that refuses the pattern with a message naming the tool | only on the owner's yes; a friction cluster of refusals rolls it back |
| 6. Hard rule | the tool covered every case for 2 weeks | a `deny` in the role's agent config | only what `AGENTS.md` already forbids in words |

**Measured, not blocked:** a spawned session never gets an `ask` rule (nobody answers it, the lane stalls). Off-map reads are good news when the merge used the file (a missing link: fix the map, `kmap.py miss`) and a brief problem when it didn't.

**Per session and per role's worth:** `--out` keeps each session's project and $ (list prices when the database has no cost; a per-project table), first request, kmap calls against raw lookups, big whole reads and error loops; such a pattern is a session flag, a role flag in 3+ sessions. `--value` rates each role of `tools/work/roles.json` against its budget and value metric (`unknown` on thin data); `tools/jobs/jobs.py` runs it every 4 h.

**One fix a week:** from the Monday digest, pick the (role, rule) pair with the most calls; log it with its numbers before and after.
