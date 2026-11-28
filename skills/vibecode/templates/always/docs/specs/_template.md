# <What the user gets, in their words> (`<id>`)

Status: ready to build | starts with a reproduction | starts with a measurement | starts with a spike | needs grooming
Depends on: <open specs it waits for, or "nothing that is open">
Same files as: <open specs touching the same files, or "none">
Tech: light | heavy (heavy gets the architect's "How to build" before it is built; light a `Fit:` line under this one)
Links: <type> `<id>` · <type> `<id>` (why), or "none". Types: depends-on, applied-by, applies, shares-ui, supersedes, conflicts, part-of (`mod:<module>`), not (`mod:<module>`: unrelated, though it looks close)
<!-- if:reviewers_risky -->Review: yes | no
<!-- endif -->

## What it is for

The owner's words, quoted and dated. Why it matters to them.

## What is there now

- **<Part>:** what the code does today, with `path/file.ts:42`. Measured numbers where they matter. UNVERIFIED where it is a reading.

## What must be true

1. **<Rule in one bold line>.** Then the detail a builder needs: values, words on screen, edge cases.
2. ...

**Edges:** each answered in a rule above or marked `not this batch`, before the spec is `ready`: a narrow window, no room, mid-flight (an action while one runs), a split or second view, a reload, a panel open by default, and for a docs rule, who reads it. Each rule here needs a test that fails before the change; one no test can hold says why.

## In the running app

1. Steps they can follow to see it work.

## Not in this batch

- What is left out, and which spec has it.

## How to build

(The architect's, for a heavy spec: where it goes, what to reuse, refactor first or not, contracts, weak spots, tests. Delete for a light one.)

## Checks

- **Unit:** which rules, as one parameterized table.
- **Browser/e2e:** one file, which cases.
- **Performance:** which budget, measured where.

## Open calls

1. **<Question>.** Default: <the choice a builder takes without asking>.
