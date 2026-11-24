# ctxpick

Keep/drop picker for a **compacted** OpenCode (v2) session's context.

After auto-compaction, a session carries a summary, a kept tail of recent
messages and whatever big tool outputs and subagent reports came after the
checkpoint. ctxpick lists all of those with token estimates, lets you tick
what stays, and writes the result as a **new session**. The original is never
modified.

Items it lists:

| Group | What |
|---|---|
| `summary` | each `## ` section of the compaction summary |
| `tail` | each message in the kept tail (the "recent" text carried past the checkpoint) |
| `output` | tool outputs after the checkpoint of at least `minChars` characters |
| `subagent` | subagent (task) reports after the checkpoint of at least `minChars` characters |

## Install

Requires OpenCode v2 (tested on 2.3.2) and Node >= 23.6 for the CLI (it runs
`.ts` files directly).

1. Clone it:

   ```sh
   git clone https://github.com/Squikle/ctxpick
   ```

2. Add the folder to the `plugins` list in your global TUI config
   (`~/.config/opencode/cli.json`). Use the absolute path, forward slashes are fine on Windows:

   ```json
   {
     "plugins": ["/path/to/ctxpick"]
   }
   ```

   Optional settings use the tuple form:

   ```json
   { "plugins": [["/path/to/ctxpick", { "charsPerToken": 2.4, "minChars": 2000 }]] }
   ```

3. Restart the TUI. `ctxpick` should show up in the plugin list.

## Use (TUI)

1. Open a session that has been compacted at least once.
2. Type `/ctxpick`, or open the command palette and pick
   **ctxpick: choose what stays in context** (group "Session").
3. The header shows the current estimate and the estimate after your choices.
   Each row is `[x]` = keep or `[ ]` = drop, with its token estimate.
4. Keys:
   - **Enter**: toggle the highlighted item
   - **ctrl+g**: toggle the whole group of the highlighted item
   - **ctrl+r**: keep everything again
   - **ctrl+s**: apply
   - **Esc**: cancel, nothing changes
5. Apply asks for confirmation, creates the trimmed copy (title gets
   ` · ctxpick`) and switches to it. Your original session stays in the list
   as it was.

## Use (CLI)

The CLI talks to a running OpenCode server (`--url`, or `OPENCODE_SERVER_URL`,
or the local server's own state file).

```sh
node bin/ctxpick.ts list <sessionID>                 # numbered items + estimates
node bin/ctxpick.ts drop <sessionID> 3 5-9 --dry-run # show before/after, change nothing
node bin/ctxpick.ts drop <sessionID> subagent output # drop whole groups -> new session
node bin/ctxpick.ts drop <sessionID> 4 --out trimmed.json  # write the export, don't import
```

Selections: item numbers, ranges (`5-9`) or group names. Other flags:
`--file <export.json>`, `--title`, `--chars-per-token`, `--min-chars`, `--json`.

## How dropping works

OpenCode has no API to delete parts of a session's context, so ctxpick never
edits a session in place. It uses the server's export/import endpoints
(marked *experimental* in OpenCode's API):

1. `GET /api/experimental/session/<id>/export` of the source session.
2. Trim the copy:
   - dropped summary sections are cut out of the summary text;
   - the kept tail is rewritten: the checkpoint's `keepID` is removed and a
     curated `recent` text with only the kept tail messages is written instead;
   - dropped outputs / subagent reports are replaced by a short
     `[ctxpick: ... removed ...]` placeholder, so the conversation still reads
     coherently and tool calls stay paired.
3. The session ID and all message IDs are regenerated in OpenCode's format
   (same ordering rules) so the copy can't collide with the original.
4. `POST /api/experimental/session/import` the result as a new session and open it.

## Limits

- **Token numbers are estimates**: characters / `charsPerToken` (default 2.4,
  measured on Claude traffic; OpenCode itself uses 4). System prompt and tool
  definitions are not included, so the real request is bigger.
- **Real savings depend on the provider.** Providers that receive OpenCode's
  context as-is save roughly what the estimate says. The `claude-agent`
  provider runs its own Claude Code session and passes the OpenCode history
  into a fresh session as one checkpoint block, so its real numbers can be far
  smaller than the estimate for both the original and the copy, and the
  difference between them smaller too.
- Only content *after* the latest compaction checkpoint is listed; anything
  before it is already only in the summary.
- The copy is a new session: no fork lineage, and the provider's prompt cache
  starts cold on the first turn.
- Tested on OpenCode 2.3.2. The export/import endpoints are experimental and
  the export format is not a stable contract; a future version may break it.

## Uninstall

1. Remove the ctxpick entry from `plugins` in `~/.config/opencode/cli.json`.
2. Restart the TUI and delete the cloned folder.
3. Sessions created by ctxpick are normal sessions; delete them like any other
   if you don't want them.

## License

MIT, see [LICENSE](LICENSE).
