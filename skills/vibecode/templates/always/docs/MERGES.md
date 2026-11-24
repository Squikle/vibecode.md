# {{PROJECT}}: merges

What merged, newest first. **Agents write this file and never read it:** each merge adds one entry through `python -I tools/work/merge-log.py --batch <id> --sha <sha8> --what "..." --up "..." --try "..."` (`tools/ci/gate.py ship` calls it; it inserts right below the marker line and never commits). To find something, search it (`git grep -n -i <batch> -- docs/MERGES.md`). What is left to build is `docs/ROADMAP.md`.

Each entry: `### date time · batch · sha`, then **What** (one clause), **Up** (`yes, redeployed and healthy`, `no, docs only` or `no: why`) and **Try** (one clause, from the ROADMAP row's "what to try").

<!-- merge-log.py adds each new entry below this line, newest first -->

