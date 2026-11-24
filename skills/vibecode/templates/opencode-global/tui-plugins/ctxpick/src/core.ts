// ctxpick core: list the items that make up a compacted session's context and build a trimmed copy.
// Pure functions over the session export format (GET /api/experimental/session/{id}/export).
// No runtime-only TypeScript syntax, so Node can run this file with type stripping.

export type Group = "summary" | "tail" | "output" | "subagent"

export interface Item {
  /** Stable key: group plus original message ID and part index. */
  key: string
  group: Group
  title: string
  chars: number
  tokens: number
}

export interface Options {
  /** Characters per token used for estimates. Claude measures ~2.4 on mixed code/prose. */
  charsPerToken?: number
  /** Tool outputs and synthetic messages after the compaction with at least this many chars are listed. */
  minChars?: number
}

export interface Analysis {
  items: Item[]
  compactionIndex: number
  keepIndex: number
  /** Estimated chars of the context the next request would send (summary + kept tail + later messages). */
  contextChars: number
  /** Prompt tokens measured on the latest assistant reply (input + cache read + cache write), if any. */
  measuredTokens?: number
  charsPerToken: number
}

export interface ExportData {
  info: any
  messages: any[]
}

export const GROUP_LABEL: Record<Group, string> = {
  summary: "Summary sections",
  tail: "Kept tail (verbatim recent messages)",
  output: "Tool outputs after the checkpoint",
  subagent: "Subagent reports after the checkpoint",
}

const TOOL_OUTPUT_MAX_CHARS = 1250
const DEFAULTS = { charsPerToken: 2.4, minChars: 2000 }

const opts = (o?: Options) => ({ ...DEFAULTS, ...(o ?? {}) })
export const estimate = (chars: number, charsPerToken: number) => Math.ceil(chars / charsPerToken)

// ---------- text rendering (mirrors core/src/session/recent-text.ts, v2.3.2) ----------

export function truncateToolOutput(value: string): string {
  if (value.length <= TOOL_OUTPUT_MAX_CHARS) return value
  let end = 0
  let kept = 0
  for (const char of value) {
    if (kept === TOOL_OUTPUT_MAX_CHARS) break
    end += char.length
    kept++
  }
  if (end === value.length) return value
  return `${value.slice(0, end)}\n[truncated]`
}

export function serializeToolContent(content: any[] | undefined): string {
  return (content ?? [])
    .map((item: any) => {
      if (item?.type === "text") return String(item.text ?? "")
      return `[Attached ${item?.mime}${item?.name === undefined ? "" : `: ${item.name}`}]`
    })
    .join("\n")
}

function partToText(part: any): string | undefined {
  if (part?.type === "text") return `[Assistant]: ${part.text}`
  if (part?.type !== "tool") return undefined
  const input = typeof part.state?.input === "string" ? part.state.input : JSON.stringify(part.state?.input)
  const call = `[Assistant tool call]: ${part.name}(${input})`
  if (part.state?.status === "completed")
    return `${call}\n[Tool result]: ${truncateToolOutput(serializeToolContent(part.state.content))}`
  if (part.state?.status === "error") return `${call}\n[Tool error]: ${part.state.error?.message}`
  return call
}

/** The pieces one message contributes to the kept tail, in order. Joined with "\n" they equal messageToText. */
export function messagePieces(message: any): { index: number; text: string }[] {
  switch (message?.type) {
    case "user": {
      const skills = (message.skills ?? []).flatMap((s: any) =>
        s.text === undefined ? [] : [`[Skill activated: ${s.name}]\n${s.text}`],
      )
      const files = (message.files ?? []).map((f: any) => {
        const name = f.name ?? (f.source?.type === "uri" ? f.source.uri : "inline attachment")
        return `[Attached ${f.mime}: ${name}]`
      })
      return [{ index: 0, text: [...skills, `[User]: ${message.text}`, ...files].join("\n") }]
    }
    case "location-switched":
      return [{ index: 0, text: `[User]: The working directory has been changed to ${message.location?.directory}.` }]
    case "assistant":
      return (message.content ?? []).flatMap((part: any, index: number) => {
        const text = partToText(part)
        return text === undefined ? [] : [{ index, text }]
      })
    case "synthetic":
      return [{ index: 0, text: `[Synthetic context]: ${message.text}` }]
    case "skill":
      return [{ index: 0, text: `[Skill activated: ${message.name}]\n${message.text}` }]
    case "shell":
      if (message.metadata?.background === true) return []
      return [{ index: 0, text: `[Shell]: ${message.command}\n${truncateToolOutput(message.output?.output ?? "")}` }]
    default:
      return []
  }
}

/** Rough size of a message as sent after the checkpoint (tool outputs in full, reasoning excluded). */
function liveChars(message: any): number {
  switch (message?.type) {
    case "user":
      return String(message.text ?? "").length
    case "synthetic":
    case "skill":
    case "system":
      return String(message.text ?? "").length
    case "shell":
      return String(message.command ?? "").length + String(message.output?.output ?? "").length
    case "assistant":
      return (message.content ?? []).reduce((n: number, p: any) => {
        if (p?.type === "text") return n + String(p.text ?? "").length
        if (p?.type === "tool") {
          const input = typeof p.state?.input === "string" ? p.state.input : JSON.stringify(p.state?.input ?? "")
          return n + input.length + serializeToolContent(p.state?.content).length
        }
        return n
      }, 0)
    default:
      return 0
  }
}

// ---------- summary sections ----------

export function splitSummary(summary: string): { preamble: string; sections: { title: string; text: string }[] } {
  const lines = summary.split("\n")
  const sections: { title: string; text: string }[] = []
  const pre: string[] = []
  for (const line of lines) {
    if (line.startsWith("## ")) sections.push({ title: line.slice(3).trim(), text: line })
    else if (sections.length) sections[sections.length - 1]!.text += "\n" + line
    else pre.push(line)
  }
  return { preamble: pre.join("\n"), sections }
}

// ---------- analysis ----------

const latestCompaction = (messages: any[]) =>
  messages.findLastIndex((m) => m?.type === "compaction" && m?.status === "completed" && typeof m?.summary === "string")

const short = (s: string, n: number) => {
  const one = s.replace(/\s+/g, " ").trim()
  return one.length > n ? one.slice(0, n - 1) + "…" : one
}

function toolLabel(part: any): string {
  const input = part.state?.input
  if (input && typeof input === "object") {
    for (const k of ["command", "path", "filePath", "file", "pattern", "url", "query", "description", "code"]) {
      if (typeof input[k] === "string") return `${part.name} ${short(input[k], 60)}`
    }
  }
  return `${part.name} ${short(typeof input === "string" ? input : JSON.stringify(input ?? ""), 60)}`
}

function pieceLabel(message: any, piece: { index: number; text: string }): string {
  if (message.type === "assistant") {
    const part = message.content[piece.index]
    if (part.type === "tool") return `assistant · ${toolLabel(part)}`
    return `assistant · ${short(part.text ?? "", 70)}`
  }
  if (message.type === "user") return `user · ${short(message.text ?? "", 70)}`
  if (message.type === "synthetic")
    return `${message.metadata?.source === "subagent" ? "subagent" : "synthetic"} · ${short(message.description ?? message.text ?? "", 60)}`
  return `${message.type} · ${short(piece.text, 70)}`
}

export function analyze(data: ExportData, options?: Options): Analysis {
  const o = opts(options)
  const messages = data.messages
  const ci = latestCompaction(messages)
  if (ci < 0) throw new Error("This session has no completed compaction yet. Run /compact first.")
  const compaction = messages[ci]
  const items: Item[] = []
  const add = (key: string, group: Group, title: string, chars: number) =>
    items.push({ key, group, title, chars, tokens: estimate(chars, o.charsPerToken) })

  // Summary sections
  splitSummary(compaction.summary).sections.forEach((s, i) => add(`summary:${i}`, "summary", s.title, s.text.length))

  // Kept tail
  const keepID = compaction.metadata?.keepID
  const ki = typeof keepID === "string" ? messages.findIndex((m) => m.id === keepID) : -1
  let tailChars = 0
  if (ki >= 0 && ki < ci) {
    for (const m of messages.slice(ki, ci)) {
      for (const piece of messagePieces(m)) {
        add(`tail:${m.id}:${piece.index}`, "tail", pieceLabel(m, piece), piece.text.length)
        tailChars += piece.text.length
      }
    }
  } else if (typeof compaction.metadata?.recent === "string" && compaction.metadata.recent.length) {
    // A stored tail (older compactions, or a ctxpick copy): one block, all or nothing.
    add(`tail:stored`, "tail", "stored recent text (one block)", compaction.metadata.recent.length)
    tailChars = compaction.metadata.recent.length
  }

  // Big items after the checkpoint
  let liveTotal = 0
  messages.slice(ci + 1).forEach((m) => {
    liveTotal += liveChars(m)
    if (m.type === "assistant") {
      ;(m.content ?? []).forEach((p: any, i: number) => {
        if (p?.type !== "tool" || p.state?.status !== "completed") return
        const chars = serializeToolContent(p.state.content).length
        if (chars >= o.minChars) add(`output:${m.id}:${i}`, "output", toolLabel(p), chars)
      })
    } else if (m.type === "synthetic") {
      const chars = String(m.text ?? "").length
      const sub = m.metadata?.source === "subagent"
      if (sub || chars >= o.minChars)
        add(`${sub ? "subagent" : "output"}:${m.id}:0`, sub ? "subagent" : "output", pieceLabel(m, { index: 0, text: m.text }), chars)
    }
  })

  const last = messages.slice(ci + 1).findLast((m) => m.type === "assistant" && m.tokens)
  const t = last?.tokens
  const measuredTokens = t ? (t.input ?? 0) + (t.cache?.read ?? 0) + (t.cache?.write ?? 0) : undefined

  return {
    items,
    compactionIndex: ci,
    keepIndex: ki,
    contextChars: compaction.summary.length + tailChars + liveTotal,
    measuredTokens,
    charsPerToken: o.charsPerToken,
  }
}

/** Estimated context chars after dropping `drop` (keys). */
export function charsAfter(a: Analysis, drop: ReadonlySet<string>): number {
  return a.items.reduce((n, item) => (drop.has(item.key) ? n - item.chars : n), a.contextChars)
}

// ---------- IDs (same layout as packages/schema/src/identifier.ts) ----------

const CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
let lastTimestamp = 0
let counter = 0

export function createID(prefix: string, descending: boolean, timestamp = Date.now()): string {
  if (timestamp !== lastTimestamp) {
    lastTimestamp = timestamp
    counter = 0
  }
  counter++
  const current = BigInt(timestamp) * 0x1000n + BigInt(counter)
  const value = descending ? ~current : current
  const time = Array.from({ length: 6 }, (_, i) =>
    Number((value >> BigInt(40 - 8 * i)) & 0xffn)
      .toString(16)
      .padStart(2, "0"),
  ).join("")
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(14))
  return `${prefix}_${time}${Array.from(bytes, (b) => CHARS[b % 62]).join("")}`
}

function remapStrings(value: any, map: Map<string, string>): any {
  if (typeof value === "string") return map.get(value) ?? value
  if (Array.isArray(value)) return value.map((v) => remapStrings(v, map))
  if (value && typeof value === "object") {
    const out: any = {}
    for (const [k, v] of Object.entries(value)) out[k] = remapStrings(v, map)
    return out
  }
  return value
}

// ---------- trimming ----------

export interface TrimResult {
  data: ExportData
  sessionID: string
  beforeChars: number
  afterChars: number
  dropped: Item[]
}

const placeholder = (what: string, chars: number) =>
  `[ctxpick: ${what} removed by the user to save context (${chars} chars). Re-run or re-ask if you need it.]`

/** A new export with fresh session/message IDs and the dropped items removed. The input is not modified. */
export function trim(data: ExportData, drop: ReadonlySet<string>, options?: Options & { title?: string }): TrimResult {
  const a = analyze(data, options)
  const dropped = a.items.filter((i) => drop.has(i.key))
  const messages: any[] = structuredClone(data.messages)
  const compaction = messages[a.compactionIndex]

  // 1. Summary sections
  if (dropped.some((i) => i.group === "summary")) {
    const { preamble, sections } = splitSummary(compaction.summary)
    const kept = sections.filter((_, i) => !drop.has(`summary:${i}`)).map((s) => s.text.replace(/\n+$/, ""))
    compaction.summary = [preamble.trim(), ...kept].filter(Boolean).join("\n\n")
  }

  // 2. Kept tail: write a curated `recent` and drop the keepID reference so history uses it as stored.
  if (dropped.some((i) => i.group === "tail")) {
    let recent = ""
    if (a.keepIndex >= 0) {
      recent = messages
        .slice(a.keepIndex, a.compactionIndex)
        .map((m) =>
          messagePieces(m)
            .filter((p) => !drop.has(`tail:${m.id}:${p.index}`))
            .map((p) => p.text)
            .join("\n"),
        )
        .filter(Boolean)
        .join("\n\n")
    }
    const { keepID: _, ...meta } = compaction.metadata ?? {}
    compaction.metadata = { ...meta, recent }
  }

  // 3. Big outputs and subagent reports after the checkpoint
  for (const item of dropped) {
    if (item.group !== "output" && item.group !== "subagent") continue
    const [, id, idx] = item.key.split(":")
    const m = messages.find((x) => x.id === id)
    if (!m) continue
    if (m.type === "assistant") {
      const part = m.content[Number(idx)]
      part.state = { ...part.state, content: [{ type: "text", text: placeholder("tool output", item.chars) }] }
    } else if (m.type === "synthetic") {
      m.text = placeholder(m.metadata?.source === "subagent" ? "subagent report" : "synthetic message", item.chars)
    }
  }

  // 4. Fresh IDs. Message IDs are global primary keys and import keeps the IDs it is given.
  const sessionID = createID("ses", true)
  const map = new Map<string, string>([[data.info.id, sessionID]])
  for (const m of messages) map.set(m.id, createID("msg", false, Number(m.time?.created) || Date.now()))
  const outMessages = messages.map((m) => remapStrings(m, map))
  const { fork: _fork, ...info } = structuredClone(data.info)
  info.id = sessionID
  info.title = options?.title ?? `${data.info.title ?? "session"} · ctxpick`

  return {
    data: { info, messages: outMessages },
    sessionID,
    beforeChars: a.contextChars,
    afterChars: charsAfter(a, drop),
    dropped,
  }
}

export const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))
