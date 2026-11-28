# {{PROJECT}}: roadmap

Everything built and planned, by theme, written as **what you get and what to try**, not as code. The build order is `docs/BACKLOG.md` (its numbers in brackets, `[#4]`). State as of {{DATE}}. The app runs at: (the `bootstrap` batch fills this in).

| Mark | Meaning |
|---|---|
| ✅ | on `main`: try it now |
| 🔀 | built and reviewed, waiting to merge |
| 🔨 | being built now (by whom: `E2`, `lane M1`, ...) |
| 📋 | planned, ready to build |
| 🧹 | needs grooming: the product lead writes its specs first |
| ⏸ | waits for the owner's word |

**Build order across themes:** (one line: 1 <theme's first batches> → 2 ... → 3 ...; the product lead writes it, everyone keeps it current)

---

## What's new

Newest first. **Every merge adds one dated part** (`### Since <date>, <time>`); a part is never edited later, only followed by a newer one.

### Since {{DATE}}

**Set up (🧹):** the workflow, docs and scripts, by `/vibecode`. Next: the product lead's first specs.

<!-- Form of a part:
### Since 2026-01-31, 14:20
**Merged (✅): try now**
| Batch | What you get | Try |
|---|---|---|
| `id` | one sentence in the user's words | the steps, short |
**Planned (📋):** `id` [#n] what it will give, one line each.
**Changed course:** what moved and why, one line each (their decisions, reorders).
-->

---

## 1. <Theme> (one paragraph: what this theme is for, in the owner's words)

| | Batch | What you get | Try |
|---|---|---|---|
| 📋 | `bootstrap` [#1] | the app starts, with its tests and one command for the gate | run it |

<!-- How to write this file (product lead, coordinator, integrator):
- One table per theme, themes in build order; a row per batch: mark, `id` [#n] (merged rows drop the number), what you get (the user's view, one sentence), try (steps they can follow; "—" if nothing to see).
- Product lead: adds rows for new specs in their theme, writes the build-order line and a "Planned" line in the newest part.
- Coordinator: marks 🔨 with who builds it when a session starts it, 🔀 when it is reviewed and gated; ⏸ when it waits for the owner.
- Integrator: on each merge sets ✅, drops the [#n], renumbers the other [#n] from BACKLOG, adds the dated "What's new" part with the merged rows and their "Try".
- Never describe code here; say what changes on screen or in use. Keep a row to one line.
-->
