// prompt-polish TUI core (pure, no OpenCode imports; unit-tested with node --test).
// polish-show: when the server plugin rewrote your prompt, show what changed in a small block above the input.
// Display only: it reads the message the TUI already has (text + metadata.promptPolish.original) and never sends
// anything to a model, so it costs 0 tokens.

export const NOTE_RE = /\n\n\[prompt-check, auto\][\s\S]*$/ // appended by index.js when it flags a conflict
const REPLY_RE = /\n\n\(Reply in the language with ISO code "[^"]*"\.\)\s*$/ // optional index.js suffix
export const MAX_HUNKS = 4
export const CONTEXT_WORDS = 3
export const MAX_EXCERPT = 280
const MAX_CELLS = 2_000_000 // LCS table cap; above it only the common prefix/suffix is matched

const words = (s) => String(s ?? "").split(/\s+/).filter(Boolean)
const normWord = (w) => w // exact match: case and punctuation fixes count as edits

// ---------- state file ----------

/** show.json -> boolean; no file / unreadable / bad JSON = ON (the default) */
export function parseShow(raw) {
  if (typeof raw !== "string" || raw.trim() === "") return true
  try {
    const v = JSON.parse(raw)
    return typeof v?.show === "boolean" ? v.show : true
  } catch {
    return true
  }
}
export const serializeShow = (show, now = Date.now()) => JSON.stringify({ show: Boolean(show), updated: new Date(now).toISOString() }) + "\n"

/** `/polish-show [on|off|toggle|status]` -> { next } (next undefined = status only) or { error } */
export function commandRoute(input, current) {
  const a = String(input ?? "").trim().toLowerCase()
  if (a === "" || a === "toggle") return { next: !current }
  if (["on", "1", "true", "yes", "show"].includes(a)) return { next: true }
  if (["off", "0", "false", "no", "hide"].includes(a)) return { next: false }
  if (["status", "state", "?"].includes(a)) return { next: undefined }
  return { error: `unknown argument "${a}"; use /polish-show [on|off|status]` }
}
export const stateText = (show) =>
  show
    ? "polish-show ON: when prompt-polish rewrites a prompt, a ✎ block above the input shows what changed (0 tokens)"
    : "polish-show OFF: polished prompts are not marked (the polisher itself still runs)"

// ---------- which message ----------

/** the latest user message in the list, if prompt-polish changed it */
export function latestPolish(messages) {
  if (!Array.isArray(messages)) return null
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m?.type !== "user") continue
    const pp = m.metadata?.promptPolish
    if (!pp || typeof pp.original !== "string" || typeof m.text !== "string") return null
    if (m.text === pp.original) return null
    return { id: m.id, original: pp.original, final: m.text, language: pp.language, issues: Number(pp.issues) || 0 }
  }
  return null
}

// ---------- diff ----------

/** word diff: [{op:"eq"|"del"|"ins", w:string}] */
export function wordDiff(a, b) {
  const A = words(a), B = words(b)
  let pre = 0
  while (pre < A.length && pre < B.length && normWord(A[pre]) === normWord(B[pre])) pre++
  let suf = 0
  while (suf < A.length - pre && suf < B.length - pre && normWord(A[A.length - 1 - suf]) === normWord(B[B.length - 1 - suf])) suf++
  const a2 = A.slice(pre, A.length - suf), b2 = B.slice(pre, B.length - suf)
  const out = A.slice(0, pre).map((w) => ({ op: "eq", w }))
  if (a2.length * b2.length > MAX_CELLS) {
    for (const w of a2) out.push({ op: "del", w })
    for (const w of b2) out.push({ op: "ins", w })
  } else {
    const n = a2.length, m = b2.length
    const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--)
        L[i][j] = normWord(a2[i]) === normWord(b2[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1])
    let i = 0, j = 0
    while (i < n && j < m) {
      if (normWord(a2[i]) === normWord(b2[j])) { out.push({ op: "eq", w: b2[j] }); i++; j++ }
      else if (L[i + 1][j] >= L[i][j + 1]) out.push({ op: "del", w: a2[i++] })
      else out.push({ op: "ins", w: b2[j++] })
    }
    while (i < n) out.push({ op: "del", w: a2[i++] })
    while (j < m) out.push({ op: "ins", w: b2[j++] })
  }
  for (const w of A.slice(A.length - suf)) out.push({ op: "eq", w })
  return out
}

/** group a diff into changes with a little context: [{before, del, ins, after}] (strings) */
export function hunks(diff, ctx = CONTEXT_WORDS) {
  const out = []
  let k = 0
  while (k < diff.length) {
    if (diff[k].op === "eq") { k++; continue }
    const start = k
    const del = [], ins = []
    // one change = a run of del/ins words
    while (k < diff.length && diff[k].op !== "eq") (diff[k].op === "del" ? del : ins).push(diff[k++].w)
    const beforeFrom = Math.max(0, start - ctx)
    const before = diff.slice(beforeFrom, start).filter((d) => d.op === "eq").map((d) => d.w)
    const afterEq = []
    for (let x = k; x < diff.length && afterEq.length < ctx && diff[x].op === "eq"; x++) afterEq.push(diff[x].w)
    out.push({ before: before.join(" "), del: del.join(" "), ins: ins.join(" "), after: afterEq.join(" "), cut: beforeFrom > 0 })
  }
  return out
}

/** split index.js's appended notes off the final text */
export function splitNote(final) {
  let body = String(final ?? "")
  let note = null
  const r = body.match(REPLY_RE)
  if (r) body = body.slice(0, r.index)
  const m = body.match(NOTE_RE)
  if (m) { note = m[0].trim(); body = body.slice(0, m.index) }
  return { body, note }
}

const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s)

/**
 * the block's content, or null = render nothing.
 * { title, mode:"edits"|"rewrite", hunks:[[{t,k}]], more, excerpt, note }
 * segment kinds k: "ctx" (unchanged words), "del" (removed), "arrow", "ins" (added)
 */
export function view(p) {
  if (!p) return null
  const { body, note } = splitNote(p.final)
  const diff = wordDiff(p.original, body)
  const changed = diff.filter((d) => d.op !== "eq").length
  const total = Math.max(1, words(p.original).length)
  if (changed === 0 && !note) return null // only whitespace changed
  const hs = hunks(diff)
  const lang = typeof p.language === "string" && p.language && p.language !== "en" ? p.language : null
  const rewrite = lang !== null || changed / (total + words(body).length) > 0.5 || hs.length > 12
  const parts = []
  if (lang) parts.push(`translated from ${lang}`)
  else if (changed > 0) parts.push(rewrite ? "rewritten" : `${hs.length} edit${hs.length === 1 ? "" : "s"}`)
  if (note) parts.push("conflict note added")
  const v = { title: `✎ prompt-polish · ${parts.join(" · ")}`, mode: rewrite ? "rewrite" : "edits", hunks: [], more: 0, excerpt: null, note }
  if (rewrite) v.excerpt = clip(body.replace(/\s+/g, " ").trim(), MAX_EXCERPT)
  else {
    v.hunks = hs.slice(0, MAX_HUNKS).map((h) => {
      const segs = []
      const pre = `${h.cut ? "…" : ""}${h.before}`
      if (pre) segs.push({ t: `${pre} `, k: "ctx" })
      if (h.del) segs.push({ t: clip(h.del, 80), k: "del" })
      if (h.del && h.ins) segs.push({ t: " → ", k: "arrow" })
      if (!h.del) segs.push({ t: "+", k: "arrow" })
      if (h.ins) segs.push({ t: clip(h.ins, 80), k: "ins" })
      else segs.push({ t: " (removed)", k: "arrow" })
      if (h.after) segs.push({ t: ` ${h.after}…`, k: "ctx" })
      return segs
    })
    v.more = Math.max(0, hs.length - MAX_HUNKS)
  }
  return v
}

/** plain-text version of the block (tests, logs) */
export function viewLines(v) {
  if (!v) return []
  const lines = [v.title]
  if (v.excerpt) lines.push(`  sent as: ${v.excerpt}`)
  for (const h of v.hunks) lines.push(`  ${h.map((s) => s.t).join("")}`)
  if (v.more) lines.push(`  +${v.more} more (full text in the message above)`)
  if (v.note) lines.push(`  ${clip(v.note, 300)}`)
  return lines
}

/** the whole decision for one render: toggle + session messages -> view or null */
export function blockFor(show, messages) {
  if (!show) return null
  return view(latestPolish(messages))
}
