# The vault: the docs as an Obsidian vault

`vaultify.py` turns the docs of a git revision and the project's append-only logs into a vault: `docs/**` mirrored, one
note per batch, decision, open question, known red, merge and **log line**, table views over them (`*.base`), and the
hub `index.md`. Zero LLM, stdlib only, Windows and macOS. Open `<main tree>/.work/vault` (git-ignored) in Obsidian as a
vault (the views need Obsidian 1.9+). **On by default:** `deploy.py` installs the hook below; a project that never
wants it deploys with `--no-vault` or deletes `tools/vault/` (the ship step then prints `vault=skip`, integrate skips
its vault check).

## By hand

```
python -I -X utf8 tools/vault/vaultify.py      # [--rev <rev>] [--vault <dir>] [--logs <file|folder>]... [--if-changed]
```

One JSON line: `written`, `merged`, `conflicts`, `deleted`, `misses` (a doc row or committed log line of a shape it does
not know: details on stderr), `batches`, `log_events`, `log_misses`, `seconds`; exit 1 on a miss or a conflict.
Then `mesh/`: the knowledge map of the same rev (`tools/kmap/kmap.py export --rev`; `--mesh off` skips it, `--src`
too): one note per module, spec and area, **all regenerated** each build, a gone node's note deleted, so edit the
source in git, never a `mesh/` note. The JSON line's `mesh` is `{written, same, deleted, seconds}` or one error word,
and never changes the exit.
`--if-changed` exits at once when the last build was of the same rev, by the same scripts, with the same local log
bytes. `--rev` defaults to local `main`, else `origin/main` (the gate's base rule); the hook builds `--rev HEAD`.

## Logs: one line per event, one note per line

```
- 2026-11-26 18:49 GATE verdict=NEW-RED sha=c389b513 seconds=241 parts="unit 4774/0, docs 51/1" #ci
2026-11-26 18:50 SPAWN `alpha` builder model=prov/mid minutes=12.5
  an indented line right under an event is its body
```

- **Where:** committed logs `docs/log/**/*.md`, and local ones in the main tree: `.work/coordinator-log.md` and
  `.work/log/**/*.md` (the gate appends `.work/log/gate.md`: one `GATE` line per run, one `RED` / `FLAKY` per new red or
  flaky test, with its title, part and machine). `--logs` (repeatable) replaces the local ones. A log is named by its
  path under `docs/log/` or `.work/log/`, `/` read as `-`; one name from two places is one log.
- **The line:** `[- ]YYYY-MM-DD [hh:mm[:ss]] [TYPE[:]] text`. TYPE is the first word in capitals (2+ letters) right
  after the time: the note's `type`, lower-cased (none: `note`). **Use a verb or a kind (SPAWN, VERDICT, MERGED,
  SOURCE), never a name** (a lane, a person): a name as TYPE makes one type per name. In the text, `key=value` and
  `key="a value"` are properties (numbers and `true`/`false` typed; `sha`, `id`, `version`, `batch` stay text; the build's
  own keys `type`, `date`, `day`, `log`, `tags`, `source`, `batches` stay in the text only), `#tag` a tag, a backticked
  batch id a link to its batch note. Headings, prose and fenced code are not events.
- **A miss:** a line that starts with a date and is not that shape (`2026-11-26: x`, month 13, a date alone). A
  committed log's: a miss (exit 1; integrate's vault check stops the merge). A local one's: counted (`log_misses`,
  `log-miss:` on stderr) and listed on the log's note, exit 0: a ledger never fails a build.
- **Notes:** `log/<name>/<day>-<hhmm>-<type>-<hash>.md` with `type`, `date`, `day`, `log`, `tags`, `source`, `batches`
  and the line's keys; the newest 2000 per log (`LOG_KEEP`). `log/<name>.md`: counts by type, first and last, the newest
  30. A line changed in place is a new note (the old one goes, or parks as a conflict if you had edited it).
- **Cost:** no model reads a log for the vault. The agent that appends a line pays its output tokens (~100 for a
  ~400-character line); the build is a script (one measured run: 757 events from ~450 KB of logs in ~1 s).

## Views: tables over the notes

`Batches.base`, `Merges.base`, `Decisions.base`, `Questions.base` and `Logs.base` (views: Newest, By type, By day, Reds
by test, Flaky by test) filter, group and sort the notes by their properties; the hub links them. A view is a file
like any note: edit it, add views, and the next build merges its own changes into yours (below). A `.base` file you
make yourself is never touched. Without Obsidian 1.9, the Dataview plugin reads the same properties:

````
```dataview
TABLE date, machine, part, sha FROM "log/gate" WHERE type = "red" SORT date DESC
```
````

## Fresh between merges

The hook builds when `main` moves; logs grow in between. Two no-model shell automations (a scheduled task, cron, or
the agent tool's shell automation, never a model turn), each a no-op when nothing changed:

```
python -I -X utf8 tools/vault/vaultify.py --if-changed      # every ~10 minutes: the new log lines
python -I -X utf8 tools/vault/vault_now.py --if-changed     # every 2 minutes: Now.md (below)
```

## A project whose events are not merges

Research, writing, ops, data, personal planning: the work has its own events. Once the owner's needs are clear, the
coordinator (solo: the product lead) proposes ONE log in one message: its file (`docs/log/<name>.md` when the team shares it,
else `.work/log/<name>.md`), its TYPE words, the keys each one carries, who appends, and the views that answer the
owner's questions (added to `Logs.base` or a new `.base`). For example:

```
# research: docs/log/research.md
- 2026-11-26 10:12 SOURCE a survey of retrieval methods url=https://example.org/survey kind=paper #retrieval
- 2026-11-26 10:40 CLAIM dense beats sparse on long queries from=survey confidence=0.7
- 2026-11-26 11:05 FINDING two sources of three agree; the third is older claims=3
# ops: .work/log/ops.md
- 2026-11-26 14:02 DEPLOY api version=2.4.1 env=prod minutes=6
- 2026-11-26 15:30 INCIDENT api 502s after the deploy severity=2 minutes=25 #rollback
```

Views: claims grouped by `confidence`, sources by `kind`; incidents by `severity`, deploys by `version`.

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
State column): one row per job, in order (building, reviewing, gate, idle, waiting, done), linked to its batch note,
with its last sign of life (Seen), in roles and batch ids only: session ids dropped, code names read as "the <role> (<batch>)". The hub embeds it (`![[Now]]`);
vaultify writes a stub once and never touches it again. It never fails (exit 0; one line per run in
`.work/vault-now.log`). Refresh it every 2 minutes (Fresh between merges, above):

```
python -I -X utf8 tools/vault/vault_now.py --if-changed      # [--src <checkpoint>] [--vault <dir>] [--trees <worktrees>]
```

- **Seen:** the newest of the batch's `progress.md`, `report.md`, `review*.md` in the main tree's `.work`, its
  `progress.md` in `<trees>/<wt>/.work/<batch>/` (`--trees`, default `<main tree>-wt`), and its gates
  (`.work/ci/<sha8>[.only]/gate.pid` of its session, Head sha or a sha in its current step: `ended` at the verdict,
  `running`, `lost` with no verdict and a dead pid; the gate's own liveness rule, `gate.runs`). A running gate makes the
  job `gate`; a building, reviewing or gate job with no trace or clock for 30 minutes (`IDLE_MIN`) is `idle`, and so is
  the word `idle`. It only reads `.work`.
- **Cheap:** `--if-changed` skips at once when the checkpoint, the script, the last vault build, Now.md's existence and
  each job's state and Seen are as they were.

## The hook: it rebuilds when main moves

`deploy.py` installs it in a git repository; else once per clone (every worktree shares the hooks):

```
python -I tools/vault/hook.py print      # the plan: hooks dir, vault, python, log; writes nothing
python -I tools/vault/hook.py install    # post-merge + post-commit; a hook that is not ours is chained, not replaced
python -I tools/vault/hook.py remove     # ours out, a chained one back
```

They act only on `main` (a lane's commit starts no process), build `HEAD --if-changed`, log one line per run in
`.work/vault-hook.log` (with `mesh=<written>/<seconds>s`), print one line only on a miss, a conflict or an error,
and never fail git: every git and kmap call has a timeout (`tools/kmap/tests/test_hook_timeouts.py`). The integrator's
`gate.py ship` also runs it and reports `vault=<written>/<misses>`.

## A miss

A row the parser does not know (a Batch cell with no `` `id` ``, a cell count off the header, a MERGES heading not
`### date time · batch · sha`), or a committed log line off the shape (Logs). The librarian fixes the row or the line; a
right row of a new shape is a builder's job: `vaultify.py`'s `TABLES` or regexes.

## Tests

```
python -I -X utf8 -m unittest discover -s tools/vault/tests      # stdlib only; the merge tests need git
```
