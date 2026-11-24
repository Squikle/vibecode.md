# opencode-global: an opt-in kit for your own OpenCode

Not part of a project: `deploy.py` never copies this folder into one. `python -I -X utf8 deploy.py --global [--force]`
installs it into your OpenCode config folder (`$XDG_CONFIG_HOME/opencode`, else `~/.config/opencode`; the same path
on Windows and macOS, from your home folder). It copies only what is missing (`--force`: overwrites the kit's own
files), prints what to add to your configs, and **never edits `opencode.jsonc` or `cli.json`**.

| Part | Goes to | Kind | What it saves |
|---|---|---|---|
| `plugins/prompt-polish/` | `plugins/prompt-polish/` | server plugin, loaded by itself | misread prompts: a cheap model fixes typos and translates before the main model reads it |
| `tui-plugins/ctxpick/` | `tui-plugins/ctxpick/` (outside `plugins/`) | TUI plugin, listed in `cli.json` | context re-read every turn after a compaction |
| `opencode.snippet.jsonc` | printed, you merge it | config | compaction at ~70% of a per-model `limit.input`, a small kept tail, lane aliases |
| rtk | its own install | server plugin | ~58% of `git log`, ~46% of `git status` output |
| `plugins/pacer/` | `plugins/pacer/` | server plugin + TUI part (`/pacer`), one folder, no `cli.json` line | model calls: wake-ups in a busy session wait and go out as one call; usage paced against the plan's limits |

## prompt-polish

- Runs once per prompt (`session.hook("prompt")`): fixes typos and grammar, translates to English, flags a real
  self-contradiction as a question for the main model. Short English prompts, slash commands, command templates,
  structured text and subagent prompts are left alone; any error or timeout lets your text through untouched.
- **Set it up first:** `POLISH_MODEL` (your cheapest cloud model) and `ONLY_PROVIDERS` (the providers whose sessions
  it may polish) at the top of `index.js`. A session on any other provider, local models included, is never sent.
- One message as typed: start it with `!!`. Off: `ENABLED = false`, or `"-prompt-polish"` in `"plugins"`.

## ctxpick

upstream: https://github.com/Squikle/ctxpick (994c991); update by recopying

- Vendored as is (MIT, its `LICENSE` and author line unchanged): `tui.ts`, `src/`, `bin/`, `package.json`,
  `README.md`. Its own README has the keys and the CLI.
- After a compaction, `/ctxpick` lists the summary's sections, the kept tail and big outputs with token estimates;
  you tick what stays and it writes a trimmed **new** session (the original is never changed).
- A TUI plugin: add its folder to `"plugins"` in `cli.json` (deploy prints the line), then **restart the TUI**.
  The CLI in `bin/` needs **node >= 23.6** (it runs `.ts` directly).

## pacer

- Ported from the source project's build `d842e7b92b`. How it works: [pacer.md](pacer.md).
- Every toggle is off until `/pacer` (a pop-up). Batching holds wake-ups for one `period` (from the first arrival by
  default, or from the last reply) and flushes them as one turn; `/now <prompt>` skips the wait. Pacing reads the
  plan's usage every 5 min and advises a lane cap.
- One folder: the server part (`index.js`, `pacer.py`) and the TUI part (`tui.js`, `tui-core.js`, `footer.jsx`,
  `rpc.js`) load from beside each other, so no `cli.json` line. Restart OpenCode, then the TUI. Needs python.
- Tests stay in the kit (`tests/pacer/`, with a test-only `node_modules` stub); `deploy.py --global` never copies them.

## rtk

A third-party command rewriter (its own license; not vendored here). Install the `rtk` binary by its own docs, then
`rtk init -g --opencode` writes its OpenCode plugin into `plugins/`. Briefs say when to prefix `rtk` (`git log`,
`git status`); never for `git diff`/`show` or pipes.

## The snippet

`opencode.snippet.jsonc`: `compaction {auto, buffer "30%", keep.tokens 4000}` and `limit.input` per model alias, the
`M_*` names as placeholders for your models. The numbers and why are in its comments.
