# Modules: the parts of the product, for the knowledge map

`tools/work/kmap.py` groups specs, files and areas into these modules, so a question about one part reaches what belongs to it and a `not` link can wall off what only looks close. Declared, not computed: a module keeps its members when the code moves. The product lead writes the first rows; the architect adds one when a module is born or splits; `kmap.py check` turns a seed that matches nothing red and lists open specs in no module.

Seeds: spec ids, paths and globs (`src/minimap*`) in backticks. Keep a row to one line.

| Module | What it is | Seeds |
|---|---|---|
