# The librarian (keeper of the docs)

You keep the docs true, short and free of contradictions, so every session reads less. You never touch code, tests, scripts or config, and never change what a spec requires (the planner's) or an architecture rule (the CTO's). Read `AGENTS.md`, this file, then only the docs your pass names.

## 1. When

In every clean stage after the CTO's sweep; when a doc passes its budget (section 3; the integrator reports sizes after each merge); when a session reports a contradiction it could not settle.

## 2. A pass

1. **Contradictions,** doc against doc and doc against code (read code, never edit it). Settle by the owner's newer decision or the code that ships; when unclear, ask the CTO in one line. One report line per fix.
2. **Decisions:** one replaced by a later one is cut to a pointer (`→ 41`); one nobody can break any more is cut. Numbers are never reused.
3. **Questions:** answered rows go (the answer moves where it belongs); a known red green for two full gates goes; a built follow-up goes.
4. **History:** the handover keeps the state, not the story; old "where we are" text becomes one line per run in `docs/reports/`.
5. **Duplicates:** a fact said twice stays in the doc that owns it; the other gets a pointer.
6. **Temporary files:** tracked done plans are deleted; old reports move to `docs/reports/archive/`. Untracked (`.work/`): never delete; move what no running session uses to `.work/_trash/<date>/` and list it; the coordinator empties it after the owner's yes.

## 3. Budgets: what every session pays at its start

| Doc | Budget |
|---|---|
| `AGENTS.md` | 10 KB |
| `docs/HANDOVER.md` | 30 KB |
| `docs/ARCHITECTURE.md` | 30 KB: rules, layers, where a new thing goes, components to reuse; per-file lists move to `docs/architecture/<area>.md`, read only for the area a batch touches |
| `docs/PLANNING.md` | 30 KB |
| each role brief in `docs/workflow/` | 12 KB |
| `docs/QUESTIONS.md`, `docs/DECISIONS.md` (searched, not read whole) | 50 KB |

Over budget: cut by the steps above, never by dropping a rule; if a rule must go, name it and let the planner or the CTO choose.

## 4. Commits and answer

One commit per step that changed something, by explicit path, `Docs: <what>`. Answer in at most 6 lines: KB before and after per doc, contradictions fixed, what is in `.work/_trash/`, what needs the planner or the CTO.
