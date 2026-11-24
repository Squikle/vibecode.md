# The vault: the docs as an Obsidian vault

`vaultify.py` turns the docs of a git revision into a vault: `docs/**` mirrored, one note per batch, decision, open
question, known red and merge, and the hub `index.md`. Zero LLM, stdlib only, Windows and macOS. Open
`<main tree>/.work/vault` (git-ignored) in Obsidian. Optional: a project that never opens it can delete `tools/vault/`
(the ship step then prints `vault=skip`).

## By hand

```
python -I -X utf8 tools/vault/vaultify.py --rev origin/main
```

One JSON line: `written`, `deleted`, `kept_edited` (a note you edited is never overwritten), `misses` (a doc row of a shape
it does not know: exit 1, details on stderr), `batches`, `seconds`. `--if-changed` exits at once when the last build was
of the same rev by the same script. `--vault <dir>` puts it elsewhere.

## The hook: it rebuilds when main moves

Once per clone (every worktree shares the hooks):

```
python -I tools/vault/hook.py print      # the plan: hooks dir, vault, python, log; writes nothing
python -I tools/vault/hook.py install    # post-merge + post-commit; a hook that is not ours is chained, not replaced
python -I tools/vault/hook.py remove     # ours out, a chained one back
```

They act only on `main` (a lane's commit starts no process), build `HEAD --if-changed`, log one line per run in
`.work/vault-hook.log`, print one line only on a miss or an error, and never fail git. The integrator's
`gate.py ship` also runs it and reports `vault=<written>/<misses>`.

## A miss

A row the parser does not know (a Batch cell with no `` `id` ``, a cell count off the header, a MERGES heading not
`### date time · batch · sha`). The librarian fixes the row; a right row of a new shape is a builder's job: `vaultify.py`'s
`TABLES` or regexes.
