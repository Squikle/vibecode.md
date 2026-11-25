# The vault: the docs as an Obsidian vault

`vaultify.py` turns the docs of a git revision into a vault: `docs/**` mirrored, one note per batch, decision, open
question, known red and merge, and the hub `index.md`. Zero LLM, stdlib only, Windows and macOS. Open
`<main tree>/.work/vault` (git-ignored) in Obsidian. Optional: a project that never opens it can delete `tools/vault/`
(the ship step then prints `vault=skip`).

## By hand

```
python -I -X utf8 tools/vault/vaultify.py --rev origin/main
```

One JSON line: `written`, `merged`, `conflicts`, `deleted`, `misses` (a doc row of a shape it does not know: details on
stderr), `batches`, `seconds`; exit 1 on a miss or a conflict. `--if-changed` exits at once when the last build was of
the same rev by the same scripts. `--vault <dir>` puts it elsewhere.

## Your edits: merged, or a conflict

You may edit any note. The next build merges the new docs into it (3-way `git merge-file` against the copy it last wrote,
kept in `.vaultify/base/`). When both changed the same lines it is a **conflict**: your file stays as it is, the new
version is parked in `.vaultify/conflicts/<path>` (Obsidian hides `.vaultify/`), `vault-alert.json` next to the vault
lists it, and the hub warns at its top. Merge the parked copy in by hand, then:

```
python -I -X utf8 tools/vault/vaultify.py --resolve <path>
```

A note you edited whose source vanished is a conflict too (resolve it: the file is yours from then on, never deleted).

## Now: the coordinator's jobs

`vault_now.py` writes `Now.md` from `.work/coordinator-now.md`'s job tables (a header with a Batch column and a Next or
State column): one row per job, in order (building, reviewing, gate, waiting, done), linked to its batch note, in roles
and batch ids only: session ids dropped, code names read as "the <role> (<batch>)". The hub embeds it (`![[Now]]`);
vaultify writes a stub once and never touches it again. It never fails (exit 0; one line per run in
`.work/vault-now.log`). Refresh it with a no-model shell automation every 2 minutes:

```
python -I -X utf8 tools/vault/vault_now.py --if-changed
```

`--if-changed` skips at once when the checkpoint, the script and the last vault build are as they were.

## The hook: it rebuilds when main moves

Once per clone (every worktree shares the hooks):

```
python -I tools/vault/hook.py print      # the plan: hooks dir, vault, python, log; writes nothing
python -I tools/vault/hook.py install    # post-merge + post-commit; a hook that is not ours is chained, not replaced
python -I tools/vault/hook.py remove     # ours out, a chained one back
```

They act only on `main` (a lane's commit starts no process), build `HEAD --if-changed`, log one line per run in
`.work/vault-hook.log`, print one line only on a miss, a conflict or an error, and never fail git. The integrator's
`gate.py ship` also runs it and reports `vault=<written>/<misses>`.

## A miss

A row the parser does not know (a Batch cell with no `` `id` ``, a cell count off the header, a MERGES heading not
`### date time · batch · sha`). The librarian fixes the row; a right row of a new shape is a builder's job: `vaultify.py`'s
`TABLES` or regexes.

## Tests

```
python -I -X utf8 -m unittest discover -s tools/vault/tests      # stdlib only; the merge tests need git
```
