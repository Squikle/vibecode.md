# {{PROJECT}}: merges

What merged, newest first. **Agents write this file and never read it:** each merge adds one entry through `python -I tools/work/merge-log.py --batch <id> --sha <sha8> --what "..." --up "..." --try "..." --from '<cause>'` (`tools/ci/gate.py ship` calls it; it inserts right below the marker line and never commits). To find something, search it (`git grep -n -i <batch> -- docs/MERGES.md`). What is left to build is `docs/ROADMAP.md`.

Each entry: `### date time · batch · sha`, then **What** (one clause), **Up** (`yes, redeployed and healthy`, `no, docs only` or `no: why`), **Try** (one clause, from the ROADMAP row's "what to try") and **From** (why it was made, `merge-cause`: a backticked batch id, a backticked `docs/...md` path or the owner's dated word, `Owner YYYY-MM-DD: "<quote>"`; the default is the batch's spec, then the `From:` line of its report; with none the entry is refused, see `tools/work/cause.py`).

<!-- merge-log.py adds each new entry below this line, newest first -->

