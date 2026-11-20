# Architecture: the code map and its rules

The planner writes the first cut; every batch keeps the map true in the same commit as its change. The architecture test checks that every source file has its line here.

## Stack

| Part | Choice | Why (decision #) |
|---|---|---|

## Layout

| Path | What lives there |
|---|---|

## Layers

What may import what, as a short list. The architecture test enforces it.

## Where a new thing goes

| A new ... | Goes in | Example |
|---|---|---|
| tunable number | (one constants file) | |
| pure rule (logic) | | |
| UI piece | | |
| API route | | |
| test | next to its code / one e2e file per feature | |

## Components to reuse

| Component | What it does | Where |
|---|---|---|

## Size rules

Files at most 400 lines, functions at most 80. The architecture test holds them, with an allow-list that may only shrink.

## Tests

| Kind | Where | Runs | Rule |
|---|---|---|---|
| Unit | | always | logic first, seen red; parameterized tables |
<!-- if:e2e -->| e2e / browser | | under the lock | one file per feature; named files only |
<!-- endif --><!-- if:perf -->| Performance | | reference machine | budgets in `MEASUREMENTS.md` |
<!-- endif -->| Architecture | | always | sizes, layers, the map |
