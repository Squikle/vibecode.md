// prompt-polish shared core (pure, no OpenCode imports; unit-tested with node --test).
// Used by index.js (server: is polishing on?) and tui.js (the /polish pop-up and the "show" overlay).
// The overlay is display only: it reads the message the TUI already has (text + metadata.promptPolish.original)
// and never sends anything to a model, so it costs 0 tokens.

import { join } from "node:path"

export const NOTE_RE = /\n\n\[prompt-check, auto\][\s\S]*$/ // appended by index.js when it flags a conflict
const REPLY_RE = /\n\n\(Reply in the language with ISO code "[^"]*"\.\)\s*$/ // optional index.js suffix
export const SETTINGS_FILE = "settings.json"
export const MAX_CHARS = 700 // overlay text budget; longer messages keep the changes and shorten unchanged runs
const KEEP_RUN = 16 // unchanged runs longer than this many words get shortened to head … tail
const KEEP_EDGE = 6
const MAX_CELLS = 2_000_000 // LCS table cap; above it only the common prefix/suffix is matched

// ---------- settings: { polish, show }, both ON by default ----------

/** <data dir>: $PROMPT_POLISH_DATA, else ~/.local/share/prompt-polish */
export const dataDirFrom = (env, home) => env?.PROMPT_POLISH_DATA || join(home, ".local", "share", "prompt-polish")

export const DEFAULTS = Object.freeze({ polish: true, show: true })

/** settings.json text -> {polish, show}; no file / bad JSON / missing or odd key = ON */
export function parseSettings(raw) {
  const out = { ...DEFAULTS }
  if (typeof raw !== "string" || raw.trim() === "") return out
  try {
    const v = JSON.parse(raw)
    for (const k of Object.keys(DEFAULTS)) if (typeof v?.[k] === "boolean") out[k] = v[k]
  } catch {}
  return out
}
export const serializeSettings = (s, now = Date.now()) =>
  JSON.stringify({ polish: s.polish !== false, show: s.show !== false, updated: new Date(now).toISOString() }) + "\n"

const ON = ["on", "1", "true", "yes"]
const OFF = ["off", "0", "false", "no"]

/**
 * `/polish [args]` -> { open } | { status } | { set: {key: bool} } | { error }
 * bare = the pop-up; `on|off` = the polisher; `show on|off`; `polish on|off`; `status`
 */
export function commandRoute(input, s = DEFAULTS) {
  const a = String(input ?? "").trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (a.length === 0) return { open: true }
  if (a.length === 1 && ["status", "state", "?"].includes(a[0])) return { status: true }
  let key = "polish", val = a[0]
  if (a[0] === "polish" || a[0] === "show") { key = a[0]; val = a[1] ?? "toggle" }
  if (a.length > (a[0] === key ? 2 : 1)) return { error: usage }
  if (val === "toggle") return { set: { [key]: !s[key] } }
  if (ON.includes(val)) return { set: { [key]: true } }
  if (OFF.includes(val)) return { set: { [key]: false } }
  return { error: usage }
}
const usage = "use /polish (pop-up), /polish on|off, /polish show on|off, /polish status"

export const statusText = (s) =>
  `polish ${s.polish ? "ON" : "OFF"} · show ${s.show ? "ON" : "OFF"}` +
  (s.polish ? "" : " (prompts go to the model as typed)")

// ---------- the pop-up rows (dialog.select options) ----------

const mark = (v) => (v ? "[x]" : "[ ]")
export function rows(s, last) {
  return [
    { value: "polish", title: `${mark(s.polish)} polish`, description: "a cheap model fixes typos, translates, flags conflicts before the main model reads it", category: "prompt-polish" },
    { value: "show", title: `${mark(s.show)} show`, description: "after a polished prompt, an overlay shows your message with the changes marked (0 tokens)", category: "prompt-polish" },
    last
      ? { value: "last", title: "    show my last prompt", description: "the overlay again for this session's last polished prompt", category: "prompt-polish" }
      : { value: "last", title: "    show my last prompt", description: "nothing polished in this session yet", category: "prompt-polish", disabled: true },
  ]
}

// ---------- which message ----------

/** the latest user message in the list, if prompt-polish changed it: {id, original, final, language} | null */
export function latestPolish(messages) {
  if (!Array.isArray(messages)) return null
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m?.type !== "user") continue
    const pp = m.metadata?.promptPolish
    if (!pp || typeof pp.original !== "string" || typeof m.text !== "string") return null
    if (m.text === pp.original) return null
    return { id: m.id, original: pp.original, final: m.text, language: pp.language }
  }
  return null
}

/**
 * which polished message to pop the overlay for, given what was already seen. Pure state machine:
 * seen = {sid: Set(ids)}; the first look at a session only records (no overlay for old messages).
 * -> { pop: polish | null, seen }
 */
export function nextPop(seen, sid, messages) {
  const s = seen ?? {}
  if (!sid) return { pop: null, seen: s }
  const p = latestPolish(messages)
  if (!s[sid]) {
    s[sid] = new Set(p ? [p.id] : [])
    return { pop: null, seen: s }
  }
  if (!p || s[sid].has(p.id)) return { pop: null, seen: s }
  s[sid].add(p.id)
  return { pop: p, seen: s }
}

// ---------- diff (whitespace kept, so the overlay reads like the message) ----------

/** "a  b\nc" -> [{w:"a",sp:"  "},{w:"b",sp:"\n"},{w:"c",sp:""}]; runs of 3+ newlines become 2 */
export function tokens(s) {
  const out = []
  const re = /(\S+)(\s*)/g
  const str = String(s ?? "").replace(/^\s+/, "")
  let m
  while ((m = re.exec(str))) out.push({ w: m[1], sp: m[2].replace(/\n{3,}/g, "\n\n") })
  return out
}

/** word diff: [{op:"eq"|"del"|"ins", w, sp}] (sp from the final text for eq/ins, " " for del) */
export function wordDiff(a, b) {
  const A = tokens(a), B = tokens(b)
  let pre = 0
  while (pre < A.length && pre < B.length && A[pre].w === B[pre].w) pre++
  let suf = 0
  while (suf < A.length - pre && suf < B.length - pre && A[A.length - 1 - suf].w === B[B.length - 1 - suf].w) suf++
  const a2 = A.slice(pre, A.length - suf), b2 = B.slice(pre, B.length - suf)
  const out = B.slice(0, pre).map((t) => ({ op: "eq", ...t }))
  const del = (t) => ({ op: "del", w: t.w, sp: " " })
  if (a2.length * b2.length > MAX_CELLS) {
    for (const t of a2) out.push(del(t))
    for (const t of b2) out.push({ op: "ins", ...t })
  } else {
    const n = a2.length, m = b2.length
    const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--) L[i][j] = a2[i].w === b2[j].w ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1])
    let i = 0, j = 0
    while (i < n && j < m) {
      if (a2[i].w === b2[j].w) { out.push({ op: "eq", ...b2[j] }); i++; j++ }
      else if (L[i + 1][j] >= L[i][j + 1]) out.push(del(a2[i++]))
      else out.push({ op: "ins", ...b2[j++] })
    }
    while (i < n) out.push(del(a2[i++]))
    while (j < m) out.push({ op: "ins", ...b2[j++] })
  }
  for (const t of B.slice(B.length - suf)) out.push({ op: "eq", ...t })
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

/** merge neighbours of the same kind: [{t,k}] */
function pushSeg(segs, t, k) {
  const last = segs[segs.length - 1]
  if (last && last.k === k) last.t += t
  else segs.push({ t, k })
}

/** shorten unchanged runs (keep head/tail words) until the text fits MAX_CHARS; changes are never dropped */
function fit(diff) {
  const len = (d) => d.reduce((n, x) => n + x.w.length + x.sp.length, 0)
  if (len(diff) <= MAX_CHARS) return diff
  const out = []
  let k = 0
  while (k < diff.length) {
    if (diff[k].op !== "eq") { out.push(diff[k++]); continue }
    let e = k
    while (e < diff.length && diff[e].op === "eq") e++
    const run = diff.slice(k, e)
    const head = k === 0 ? 0 : KEEP_EDGE
    const tail = e === diff.length ? 0 : KEEP_EDGE
    if (run.length > KEEP_RUN || (k === 0 && run.length > KEEP_EDGE) || (e === diff.length && run.length > KEEP_EDGE)) {
      const h = run.slice(0, head), t = run.slice(run.length - tail)
      out.push(...h, { op: "gap", w: "…", sp: " " }, ...t)
    } else out.push(...run)
    k = e
  }
  return out
}

/**
 * the overlay content, or null = show nothing.
 * { title, segs:[{t,k}], duration }   k: "eq" (kept), "del" (removed), "ins" (added), "gap" (shortened), "note"
 */
export function overlay(p) {
  if (!p) return null
  const { body, note } = splitNote(p.final)
  const diff = wordDiff(p.original, body)
  const changed = diff.filter((d) => d.op !== "eq").length
  if (changed === 0 && !note) return null // only whitespace changed
  const words = Math.max(1, tokens(p.original).length + tokens(body).length)
  const lang = typeof p.language === "string" && p.language && p.language !== "en" ? p.language : null
  const rewrite = lang !== null || changed / words > 0.5
  const segs = []
  if (rewrite) pushSeg(segs, clip(body.trim(), MAX_CHARS), "eq")
  else {
    for (const d of fit(diff)) pushSeg(segs, d.w + d.sp, d.op)
    const last = segs[segs.length - 1]
    if (last) last.t = last.t.trimEnd()
  }
  if (note) segs.push({ t: `\n\n${clip(note, 300)}`, k: "note" })
  const what = lang ? `translated from ${lang}` : rewrite ? "rewritten" : null
  const title = ["✎ polished", what, note ? "conflict note" : null].filter(Boolean).join(" · ")
  const chars = segs.reduce((n, s) => n + s.t.length, 0)
  return { title, segs, duration: Math.min(20_000, 6_000 + chars * 25) }
}

/** "teh" -> "t̶e̶h̶" (U+0336 after each non-space char): strike-through that survives a plain-string toast */
export const strike = (s) => Array.from(String(s ?? "")).map((c) => (/\s/.test(c) ? c : `${c}̶`)).join("")

/** the toast body: the corrected message, removed words struck, no markers, no edit count */
export const toastText = (v) =>
  v ? v.segs.map((s) => (s.k === "del" ? strike(s.t.trimEnd()) + s.t.slice(s.t.trimEnd().length) : s.t)).join("") : ""

/** plain text of the overlay ([-removed-] {+added+}); for tests, logs and the no-JSX fallback */
export const overlayText = (v) =>
  v ? v.segs.map((s) => (s.k === "del" ? `[-${s.t.trimEnd()}-]${s.t.slice(s.t.trimEnd().length)}` : s.k === "ins" ? `{+${s.t.trimEnd()}+}${s.t.slice(s.t.trimEnd().length)}` : s.t)).join("") : ""
