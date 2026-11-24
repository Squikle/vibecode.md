---
description: Ask the docs or code a factual question, or up to 5 at once - the small model finds it and answers short (cheap)
model: {{M_HELPER}}
---
Answer these questions about {{PROJECT}} from its files: one question, or a numbered list of up to 5 independent ones. Find with grep or a file search first and read only the lines you need. Answer each under its number in at most 10 lines with a `file:line` for every claim; per question, say "not found" and where you looked when it isn't there; never guess. Change nothing.

Questions: $ARGUMENTS
