# Backlog: open batches, in order

Rows are written by the product lead and placed by <!-- if:team -->the coordinator, who owns the order<!-- endif --><!-- if:solo -->the product lead<!-- endif --> (`docs/PLANNING.md` "The order"; the owner's words on order: `docs/log/order.md`); a merged row is removed by the integrator<!-- if:solo --> (the builder)<!-- endif -->. Order by tiers (one line of why per tier).

**Tiers:** (the product lead writes them in the first session)

| # | Batch | What | Status | Waits on | Same files as | Spec |
|---|---|---|---|---|---|---|
| 1 | `bootstrap` The skeleton runs, with its tests and gate | (the product lead fills) | needs grooming | nothing | everything | `docs/specs/bootstrap.md` |
