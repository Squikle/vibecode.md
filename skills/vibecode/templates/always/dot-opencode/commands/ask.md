---
description: Ask the docs or code a factual question - the small model finds it and answers short (cheap)
model: {{M_HELPER}}
---
Answer this question about {{PROJECT}} from its files. Find with grep or a file search first and read only the lines you need. Answer in at most 10 lines with a `file:line` for every claim; say "not found" and where you looked when it isn't there; never guess. Change nothing.

Question: $ARGUMENTS
