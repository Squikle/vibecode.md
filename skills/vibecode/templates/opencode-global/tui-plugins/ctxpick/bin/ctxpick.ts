#!/usr/bin/env node
// ctxpick CLI. Needs Node 23.6+ (runs TypeScript directly).
import { readFileSync, writeFileSync } from "node:fs"
import { parseArgs } from "node:util"
import { analyze, charsAfter, estimate, fmtTokens, GROUP_LABEL, trim, type ExportData } from "../src/core.ts"
import { httpApi } from "../src/api.ts"
import { parseSelection } from "../src/select.ts"

const HELP = `ctxpick: pick what stays in a compacted OpenCode session's context.

Usage:
  ctxpick list <sessionID>                     numbered items with estimated tokens
  ctxpick drop <sessionID> <selection...>      copy the session without the selected items
  ctxpick drop <sessionID> <selection...> --dry-run

Selection: item numbers and ranges (3 5-9), groups (summary tail output subagent),
or summary sections (summary:Context). Example: ctxpick drop ses_... 12-30 subagent

Options:
  --file <export.json>   read a saved export instead of the server (list / --dry-run / --out)
  --out <file>           write the trimmed export to a file instead of importing it
  --title <text>         title of the new session (default: "<title> · ctxpick")
  --chars-per-token <n>  estimate ratio (default 2.4)
  --min-chars <n>        smallest tool output / message listed after the checkpoint (default 2000)
  --url <url>            server URL (default: OPENCODE_SERVER_URL or the running local server)
  --json                 machine-readable output
`

const { values: flags, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    file: { type: "string" },
    out: { type: "string" },
    title: { type: "string" },
    "chars-per-token": { type: "string" },
    "min-chars": { type: "string" },
    url: { type: "string" },
    json: { type: "boolean" },
    "dry-run": { type: "boolean" },
    help: { type: "boolean", short: "h" },
  },
})

const [command, sessionID, ...selection] = positionals
if (flags.help || !command || !["list", "drop"].includes(command) || (!sessionID && !flags.file)) {
  process.stdout.write(HELP)
  process.exit(flags.help ? 0 : 1)
}

const options = {
  charsPerToken: flags["chars-per-token"] ? Number(flags["chars-per-token"]) : undefined,
  minChars: flags["min-chars"] ? Number(flags["min-chars"]) : undefined,
}
for (const [k, v] of Object.entries(options)) if (v !== undefined && !(v > 0)) fail(`--${k} must be a positive number`)
Object.keys(options).forEach((k) => (options as any)[k] === undefined && delete (options as any)[k])

function fail(message: string): never {
  process.stderr.write(`ctxpick: ${message}\n`)
  process.exit(1)
}

async function main() {
  let data: ExportData
  if (flags.file) {
    const raw = JSON.parse(readFileSync(flags.file, "utf8"))
    data = raw?.data ?? raw
  } else data = await httpApi({ url: flags.url }).exportSession(sessionID!)

  const a = analyze(data, options)
  const tk = (chars: number) => fmtTokens(estimate(chars, a.charsPerToken))

  if (command === "list") {
    if (flags.json) return console.log(JSON.stringify({ ...a, items: a.items.map((i, n) => ({ n: n + 1, ...i })) }, null, 2))
    console.log(`${data.info.title ?? data.info.id}`)
    console.log(
      `context ≈ ${tk(a.contextChars)} tokens (estimate, messages only)` +
        (a.measuredTokens ? ` · last request measured ${fmtTokens(a.measuredTokens)} incl. system + tools` : ""),
    )
    let group = ""
    a.items.forEach((item, n) => {
      if (item.group !== group) {
        group = item.group
        const g = a.items.filter((i) => i.group === group)
        console.log(`\n${GROUP_LABEL[item.group]} (${g.length}, ≈${tk(g.reduce((s, i) => s + i.chars, 0))})`)
      }
      console.log(`${String(n + 1).padStart(4)}  ${fmtTokens(item.tokens).padStart(6)}  ${item.title}`)
    })
    return
  }

  if (!selection.length) fail("drop needs a selection. Run `ctxpick list` first.")
  const drop = parseSelection(a.items, selection)
  if (flags["dry-run"]) {
    const after = charsAfter(a, drop)
    const out = { dropped: drop.size, beforeTokens: estimate(a.contextChars, a.charsPerToken), afterTokens: estimate(after, a.charsPerToken) }
    return console.log(flags.json ? JSON.stringify(out) : `would drop ${out.dropped} items: ≈${fmtTokens(out.beforeTokens)} → ≈${fmtTokens(out.afterTokens)} tokens`)
  }

  const result = trim(data, drop, { ...options, title: flags.title })
  const summary = {
    sessionID: result.sessionID,
    title: result.data.info.title,
    dropped: result.dropped.length,
    beforeTokens: estimate(result.beforeChars, a.charsPerToken),
    afterTokens: estimate(result.afterChars, a.charsPerToken),
  }
  if (flags.out) {
    writeFileSync(flags.out, JSON.stringify(result.data))
    return console.log(flags.json ? JSON.stringify({ ...summary, file: flags.out }) : `wrote ${flags.out}`)
  }
  if (flags.file && !flags.url) fail("--file without --out: pass --url too if you really want to import into a server.")
  const created = await httpApi({ url: flags.url }).importSession(result.data)
  if (flags.json) return console.log(JSON.stringify({ ...summary, sessionID: created?.id ?? summary.sessionID }))
  console.log(`new session ${created?.id ?? summary.sessionID} "${summary.title}"`)
  console.log(`dropped ${summary.dropped} items: ≈${fmtTokens(summary.beforeTokens)} → ≈${fmtTokens(summary.afterTokens)} tokens (estimate)`)
  console.log(`the original session is unchanged. Open the new one with /sessions.`)
}

main().catch((e) => fail(e instanceof Error ? e.message : String(e)))
