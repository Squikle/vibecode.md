# Golden queries: what the knowledge map must keep finding

One line per question a role really asks, with what its top 20 must hold and must not hold. `kmap.py check` turns a line that fails red, so a change to the map's rules or a lost link shows at once. Add a line when a `kmap.py miss` was a real gap you fixed, or when two parts that look alike must stay apart.

Shape: ``- `<topic>` has `<id>` `<id>`; not `<id>` `` (ids as `kmap.py` takes them: a spec id, a path, `mod:<id>`, `dec:<n>`).

