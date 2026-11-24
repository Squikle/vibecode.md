// pacer: OpenCode v2 server plugin (id "pacer"). User doc: pacer.md in the opencode-global kit.
// Batching (hold run starts in a session, flush once per period), pacing (pacer.py usage -> messages to each
// paced slot's session), global hold (sessions outside every paced slot's tree stop in HOLD/LIMITED), all driven
// by one /pacer command. Off by default. Install = this file + pacer.py in <config>/opencode/plugins/pacer/.
// State/logs: $PACER_DATA or ~/.local/share/pacer (never the config dir).
// FAIL OPEN: every hook/timer/command catches its errors, logs them, and lets the run through.
// Pure logic is exported (named exports) for the kit's tests/pacer/; the plugin itself is the default export.
import { appendFileSync, readFileSync, writeFileSync, renameSync, statSync, mkdirSync } from "node:fs"
import { execFile } from "node:child_process"
import { join, dirname } from "node:path"
import { homedir } from "node:os"
import { fileURLToPath } from "node:url"
import { createHash } from "node:crypto"

export const DEFER_MAX = 30 * 60e3        // a child waiting on its own shell is held at most this long
export const TICK_MS = 1e3                // flush timer (1 s: a flush lands within a second of its due time)
export const PACE_MS = 5 * 60e3           // pacer.py every 5 min
export const LOG_MAX = 1 << 20            // plugin.log rotates at 1 MB
export const IDLE_WAKE = 50 * 60e3        // HOLD warm-only ping: idle this long (pacer.py rule)
export const MODE_WAKE_IDLE = 10 * 60e3   // a mode with more lanes wakes a session idle this long (pacer.py rule)
export const LANES = { GO: 5, EASE: 3, SLOW: 2, HOLD: 0, BURST: 7, LIMITED: 0 }
export const USAGE = "usage: /pacer [status] | on [slot] | off [slot] | off all | pace on|off [slot] | move [slot] | batch on|off [slot] | batch <15..600> [slot] | batch me on|off [slot] | batch from arrival|reply [slot] | flush [slot] | now | week on|off | global on|off   (slot defaults to this session's, else main)"
export const SKIP_MS = 15e3               // `/pacer now` (the TUI's /now): the next run start within this passes
const STRIP = new Set(["status", "notice"])  // pacer's own replies: shown in the TUI, never sent to the model
const PASS = new Set(["flush", "wake", "now"]) // pacer's own wakes and /now prompts: always pass the batcher

// ---------------------------------------------------------------- pure helpers
export const hhmm = (ms) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` }
export const hhmmss = (ms) => `${hhmm(ms)}:${String(new Date(ms).getSeconds()).padStart(2, "0")}`
export const ago = (ms, now) => { if (!ms) return "never"; const m = Math.round((now - ms) / 60e3); return m < 1 ? "just now" : m < 120 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago` }

export function msgText(m) {
  const c = m?.content
  if (typeof c === "string") return c
  if (Array.isArray(c)) return c.map((p) => (typeof p === "string" ? p : p?.text ?? "")).join("")
  return ""
}
export const pacerKind = (m) => { const p = m?.metadata?.pacer; return p ? (typeof p === "object" ? p.kind ?? "other" : "other") : null }

// What one input message is. "you" = a prompt the user typed (role user, no `source` / pacer / compaction metadata).
export function itemKind(m) {
  if (pacerKind(m)) return "pacer"
  const meta = m?.metadata, text = msgText(m)
  if (meta?.source === "subagent" || text.startsWith('<background-result kind="subagent"')) return isDeferrable(m) ? "subagent-wait" : "subagent"
  if (meta?.source === "shell" || text.startsWith('<background-result kind="shell"')) return "shell"
  // OpenCode's auto-compaction continuation (metadata keepID + recent): the running turn going on, never a wake
  if (meta && meta.keepID != null) return "compaction"
  // a user message with no source: typed (other plugins may decorate it, e.g. prompt-polish)
  if (m?.role === "user" && meta?.source == null) return "you"
  return "other"
}
// A child that went idle but still waits on its own background shell: nothing to act on yet.
export function isDeferrable(m) {
  const t = msgText(m)
  const sub = m?.metadata?.source === "subagent" || t.startsWith('<background-result kind="subagent"')
  return sub && /state="running"/.test(t) && t.includes("Background work still running")
}
// Run start = every message after the last assistant one is a user/synthetic input (a tool result there = continuation).
// Returns those inputs, or null for a continuation.
export function runStartTail(messages) {
  const ms = messages ?? []
  let i = ms.length - 1
  const tail = []
  while (i >= 0 && ms[i]?.role !== "assistant") tail.unshift(ms[i--])
  if (!tail.length || tail.some((m) => m?.role !== "user")) return null
  return tail
}
// Remove pacer status/notice messages from the outgoing call, in place. Returns how many went.
export function stripOwn(messages) {
  let n = 0
  for (let i = (messages?.length ?? 0) - 1; i >= 0; i--) if (STRIP.has(pacerKind(messages[i]))) { messages.splice(i, 1); n++ }
  return n
}
// The UI shows a synthetic only through its `description`, as one non-wrapping row (the model never
// sees the description). Status/notice (never sent to the model) go out one synthetic per line; a
// note/flush/wake stays one message (the model reads its text) with the lines joined by " | ".
export function uiParts(text, kind) {
  const lines = String(text).split("\n").map((l) => l.trimEnd()).filter((l) => l.trim())
  if (STRIP.has(kind)) return lines.map((l) => ({ text: l, description: l }))
  return [{ text, description: lines.join(" | ").slice(0, 2000) }]
}

// /pacer args. Slot names: a-z 0-9 _ - (max 32), not a keyword. Default slot = this session's, else "main".
const KEYWORDS = new Set(["on", "off", "all", "status", "move", "week", "global", "batch", "me", "pace", "flush", "from", "now"])
export const validName = (n) => /^[a-z0-9][a-z0-9_-]{0,31}$/.test(n) && !KEYWORDS.has(n)
export function parseArgs(raw) {
  const w = String(raw ?? "").trim().toLowerCase().split(/\s+/).filter(Boolean)
  const oo = (x) => (x === "on" ? true : x === "off" ? false : undefined)
  const bad = { op: "usage" }
  // optional trailing slot name: rest = words after the fixed part
  const named = (op, rest) => (rest.length === 0 ? op : rest.length === 1 && validName(rest[0]) ? { ...op, name: rest[0] } : bad)
  if (w.length === 0 || (w.length === 1 && w[0] === "status")) return { op: "status" }
  if (w[0] === "on") return named({ op: "pace", on: true }, w.slice(1))
  if (w[0] === "pace" && w.length >= 2 && oo(w[1]) !== undefined) return named({ op: "pace", on: oo(w[1]) }, w.slice(2))
  if (w[0] === "off") return w.length === 2 && w[1] === "all" ? { op: "offall" } : named({ op: "off" }, w.slice(1))
  if (w[0] === "move") return named({ op: "move" }, w.slice(1))
  if (w[0] === "flush") return named({ op: "flush" }, w.slice(1))
  if (w[0] === "now") return w.length === 1 ? { op: "now" } : bad
  if ((w[0] === "week" || w[0] === "global") && w.length === 2 && oo(w[1]) !== undefined) return { op: w[0], on: oo(w[1]) }
  if (w[0] === "batch" && w.length >= 2) {
    if (w[1] === "me") return w.length >= 3 && oo(w[2]) !== undefined ? named({ op: "me", on: oo(w[2]) }, w.slice(3)) : bad
    if (w[1] === "from") return w[2] === "arrival" || w[2] === "reply" ? named({ op: "from", from: w[2] }, w.slice(3)) : bad
    if (oo(w[1]) !== undefined) return named({ op: "batch", on: oo(w[1]) }, w.slice(2))
    if (/^\d+$/.test(w[1])) return +w[1] >= 15 && +w[1] <= 600 ? named({ op: "period", period: +w[1] }, w.slice(2)) : bad
  }
  return bad
}

// toggles.json = { week, global, slots: { <name>: { sid, title, pace, batch, period, me } } }. One slot per session.
export const emptyToggles = () => ({ week: false, global: false, slots: {} })
export const hasFlags = (s) => !!(s && (s.pace || s.batch))
export const slotOf = (tg, sid) => Object.keys(tg?.slots ?? {}).find((n) => tg.slots[n]?.sid === sid) ?? null
export const cfgOf = (tg, sid) => { const n = slotOf(tg, sid); return n ? tg.slots[n] : undefined }
export const pacedSids = (tg) => Object.values(tg?.slots ?? {}).filter((s) => s?.pace).map((s) => s.sid)
const titleOf = (s) => s?.title || String(s?.sid ?? "?").slice(0, 16)
const cleanup = (tg) => { for (const n of Object.keys(tg.slots)) if (!hasFlags(tg.slots[n])) delete tg.slots[n] }
const moveHint = (n) => `\`/pacer move${n === "main" ? "" : " " + n}\``

// Apply one parsed /pacer op (works on a copy). Returns { tg, reply, changed }.
export function applyOp(tg0, sid, title, op) {
  const tg = JSON.parse(JSON.stringify(tg0 ?? emptyToggles()))
  tg.slots ??= {}
  const own = slotOf(tg, sid)
  let reply, changed = true
  const no = (r) => { changed = false; reply = r }
  // set ops (on, batch on, batch N, batch me): resolve the slot, refuse if it lives elsewhere
  const setSlot = (create, fn, say) => {
    const name = op.name ?? own ?? "main"
    if (own && name !== own) return no(`this session already holds slot ${own}: \`/pacer off\` it first, or run \`/pacer on ${name}\` in the other coordinator.`)
    const s = tg.slots[name]
    if (s && s.sid !== sid) return no(`slot ${name} is in ${titleOf(s)}: ${moveHint(name)} takes it over, \`/pacer on <name>\` starts a second coordinator.`)
    if (!s && !create) return no(`no slot ${name} here: \`/pacer batch on${op.name ? " " + op.name : ""}\` first.`)
    const t = (tg.slots[name] ??= { sid, pace: false, batch: false, period: 60, me: true })
    if (title) t.title = title
    fn(t)
    reply = `slot ${name} (${titleOf(t)}): ${say(t)}`
  }
  switch (op.op) {
    case "pace":
      if (op.on !== false) setSlot(true, (s) => (s.pace = true), () => "pace on.")
      else {
        const name = op.name ?? own
        if (!name || !tg.slots[name]) no(name ? `no slot ${name}.` : "this session holds no slot.")
        else { tg.slots[name].pace = false; reply = `slot ${name} (${titleOf(tg.slots[name])}): pace off${tg.slots[name].batch ? "" : "; slot released"}.` }
      }
      break
    case "batch":
      if (op.on) setSlot(true, (s) => (s.batch = true), (s) => `batch on, every ${s.period}s.`)
      else {
        const name = op.name ?? own
        if (!name || !tg.slots[name]) no(name ? `no slot ${name}.` : "this session holds no slot.")
        else { tg.slots[name].batch = false; reply = `slot ${name} (${titleOf(tg.slots[name])}): batch off${tg.slots[name].pace ? "" : "; slot released"}.` }
      }
      break
    case "period": setSlot(true, (s) => { s.period = op.period; s.batch = true }, (s) => `batch on, every ${s.period}s.`); break
    case "me": setSlot(false, (s) => (s.me = op.on), () => (op.on ? "batch me on: your prompts wait for the period too." : "batch me off: your prompts pass at once.")); break
    case "from": setSlot(false, (s) => (s.from = op.from), (s) => (op.from === "arrival"
      ? `batch from arrival: the first held item opens a ${s.period ?? 60}s window; what comes in it joins one flush.`
      : `batch from reply: the ${s.period ?? 60}s count from my last run; after a quiet period a wake passes at once.`)); break
    case "off": {
      const name = op.name ?? own
      if (!name) { no("this session holds no slot."); break }
      if (!tg.slots[name]) { no(`no slot ${name}.`); break }
      reply = `slot ${name} off (was in ${titleOf(tg.slots[name])}).`
      delete tg.slots[name]
      break
    }
    case "offall": { const n = Object.keys(tg.slots).length; tg.week = false; tg.global = false; tg.slots = {}; reply = `everything off (${n} slot${n === 1 ? "" : "s"} cleared, week off, global off).`; break }
    case "week": tg.week = op.on; reply = `week ${op.on ? "on: the week counts in every decision" : "off: the week is ignored"}.`; break
    case "global": tg.global = op.on; reply = `global ${op.on ? "on: in HOLD/LIMITED, sessions outside every paced slot's tree are stopped before their model call" : "off"}.`; break
    case "move": {
      const names = Object.keys(tg.slots)
      let name = op.name
      if (!name) {
        if (!names.length) { no("nothing to move: no slot is on."); break }
        if (names.length > 1) { no(`several slots: ${names.map((n) => `${n} (${titleOf(tg.slots[n])})`).join(", ")}. Say which: \`/pacer move <name>\`.`); break }
        name = names[0]
      }
      const s = tg.slots[name]
      if (!s) { no(`no slot ${name}.`); break }
      if (s.sid === sid) { no(`slot ${name} is already here.`); break }
      if (own) { no(`this session already holds slot ${own}: \`/pacer off\` it first.`); break }
      const from = titleOf(s)
      s.sid = sid; s.title = title || undefined
      reply = `slot ${name} moved from ${from} to ${titleOf(s)}.`
      break
    }
    default: no(USAGE)
  }
  cleanup(tg)
  return { tg, reply: `[pacer] ${reply}`, changed }
}

// The batcher. st = per-session runtime { lastRunStart, lastRunEnd, held: { ids[], since, kinds{}, opened? } | null,
// due, skipUntil }. cfg.from picks where the period counts from:
// - "arrival" (default): the first held item opens a window of one period; whatever arrives in it joins, and the
//   window never restarts, so the wait stays bounded. A prompt after a long quiet waits one period too.
// - "reply": from the later of the last passed run's start and its end: after a long turn, whatever arrives in
//   the next period waits for one flush; after a quiet period it passes at once.
// A held batch's due never moves later. skipUntil (`/pacer now`, the TUI's /now): the next run start passes.
// Returns { action: "pass"|"hold", why, st } (st is a new object).
export const fromOf = (cfg) => (cfg?.from === "reply" ? "reply" : "arrival")
export function decideBatch(st0, cfg, tail, now) {
  const st = { lastRunStart: 0, lastRunEnd: 0, held: null, due: null, ...st0 }
  const kinds = tail.map(itemKind)
  const pass = (why) => ({ action: "pass", why, st: { ...st, lastRunStart: now, held: null, due: null, skipUntil: null } })
  const hold = (why, due, opened) => {
    const held = st.held ? { ids: [...st.held.ids], since: st.held.since, kinds: { ...st.held.kinds } } : { ids: [], since: now, kinds: {} }
    if (st.held?.opened != null || opened != null) held.opened = st.held?.opened ?? opened
    tail.forEach((m, i) => {
      const id = m?.id ?? `${now}-${i}`
      if (held.ids.includes(id)) return
      held.ids.push(id)
      const k = kinds[i] === "subagent-wait" ? "subagent" : kinds[i]
      held.kinds[k] = (held.kinds[k] ?? 0) + 1
    })
    return { action: "hold", why, st: { ...st, held, due, skipUntil: st.skipUntil && now <= st.skipUntil ? st.skipUntil : null } }
  }
  if (tail.some((m) => pacerKind(m) === "now")) return pass("/now")
  if (tail.some((m) => PASS.has(pacerKind(m)))) return pass("pacer flush")
  if (kinds.includes("compaction")) return pass("compaction continues the turn")
  if (st.skipUntil && now <= st.skipUntil) return pass("/now")
  if (!cfg.me && kinds.includes("you")) return pass("typed prompt, me off")
  const real = kinds.filter((k) => k !== "pacer")
  if (real.length && real.every((k) => k === "subagent-wait")) {
    const since = st.held?.since ?? now
    return hold("only children waiting on their shells", Math.min(st.due ?? Infinity, since + DEFER_MAX))
  }
  const periodMs = (cfg.period ?? 60) * 1000
  if (fromOf(cfg) === "arrival") {
    // the window opens at the first held item that is not a child waiting on its shell
    const opened = st.held?.opened ?? now
    const due = Math.min(st.held && st.due != null ? st.due : Infinity, opened + periodMs)
    if (now >= due) return pass("window over")
    return hold(`window ${cfg.period ?? 60}s from the first arrival`, due, opened)
  }
  const base = Math.max(st.lastRunStart ?? 0, st.lastRunEnd ?? 0)
  if (now - base >= periodMs) return pass("period elapsed")
  const due = st.held && st.due != null ? Math.min(st.due, base + periodMs) : base + periodMs
  return hold(`period ${cfg.period ?? 60}s not elapsed`, due)
}

// One flush line for the held items (+ optional pace text).
export function flushText(held, paceText, now) {
  const parts = []
  if (held?.ids?.length) {
    const k = held.kinds ?? {}
    const bits = [`${k.subagent ?? 0} subagent`, `${k.shell ?? 0} shell`, `${k.you ?? 0} you`]
    if (k.other) bits.push(`${k.other} other`)
    parts.push(`[pacer] batch: ${held.ids.length} held since ${hhmm(held.since ?? now)} (${bits.join(", ")})`)
  }
  if (paceText) parts.push(paceText)
  return parts.join("\n")
}

// Global: stop this run? Only in HOLD/LIMITED, only for sessions outside every paced slot's tree.
export function globalBlocks(tg, mode, sid, ancestors) {
  if (!tg?.global || (mode !== "HOLD" && mode !== "LIMITED")) return false
  const paced = new Set(pacedSids(tg))
  return !paced.has(sid) && !(ancestors ?? []).some((a) => paced.has(a))
}

// Pace messages for one paced slot (pacer.py's wake rules). prev/cur = pacer.py status --json results.
// s = { busy, idleSince, lastSend }. Returns { why, wake, text } or null. The usage line appears once.
export function paceMessage(prev, cur, s, now, slot = "main") {
  if (!cur?.ok || cur.mode === "LIMITED") return null
  const pct = (r, k) => r?.usage?.[k]?.pct
  const line = String(cur.line ?? "").replace(/^PACE \d\d:\d\d \| /, "")
  const idle = s.busy ? 0 : now - (s.idleSince ?? now)
  const sinceSend = now - (s.lastSend ?? 0)
  const msg = (why, tail, wake) => ({ why, wake, text: `[pacer ${slot}] ${hhmm(now)} ${why}: ${line}.${tail} Reply in 2 lines.` })
  const reset = prev?.ok && [["h5", true], ["d7", !!cur.week]].some(([k, on]) => on && pct(prev, k) != null && pct(prev, k) >= 95 && pct(cur, k) < pct(prev, k) - 40)
  if (reset) return msg("RESET", " The limit reset. Resume the stopped children with the keep-alive line, then go on.", true)
  if (cur.mode === "HOLD" && idle >= IDLE_WAKE && sinceSend >= IDLE_WAKE)
    return msg("HOLD", " HOLD: start nothing new; idle children get a warm-only ping (Reply OK, no tools); running ones finish their step.", true)
  if (prev?.ok && prev.mode && prev.mode !== cur.mode) {
    const up = (LANES[cur.mode] ?? 0) > (LANES[prev.mode] ?? 5)
    const wake = up && idle >= MODE_WAKE_IDLE && sinceSend >= MODE_WAKE_IDLE
    return msg(`MODE ${prev.mode}->${cur.mode}`, ` New lane cap lanes<=${cur.lanes}: spawn within it.`, wake)
  }
  return null
}

export function statusText({ tg, sid, pace, counters, rt, now }) {
  const own = slotOf(tg, sid)
  const c = counters?.[sid] ?? {}
  const L = [`[pacer] status ${hhmm(now)}`]
  L.push(`toggles: week ${tg.week ? "on" : "off"} | global ${tg.global ? "on" : "off"}`)
  L.push(`this session: ${own ? `slot ${own}` : "no slot"}`)
  L.push(pace?.ok ? `usage: ${String(pace.line).replace(/^PACE /, "")} (read ${ago(pace.atMs, now)})` : `usage: ${pace ? `unavailable (${pace.err ?? "error"})` : "not read yet"}`)
  if (own) {
    const saved = Math.max(0, (c.held ?? 0) - (c.flushes ?? 0))
    L.push(`batch here: runs passed ${c.passed ?? 0} / held ${c.held ?? 0}, items held ${c.items ?? 0}, flushes ${c.flushes ?? 0}, model calls saved ${saved}`)
    const s = rt?.[sid]
    if (s?.held?.ids?.length) {
      const left = Math.max(0, Math.ceil(((s.due ?? now) - now) / 1000))
      L.push(`held now: ${s.held.ids.length} since ${hhmm(s.held.since ?? now)}, flush ${s.busy ? "after the running turn" : `at ${hhmmss(s.due ?? now)} (in ${left}s)`}; \`/pacer flush\` sends it now`)
    }
  }
  const names = Object.keys(tg.slots ?? {})
  if (!names.length) L.push("slots: none")
  else {
    L.push("slots:")
    for (const n of names) {
      const f = tg.slots[n]
      const flags = [f.pace && "pace", f.batch && `batch ${f.period ?? 60}s from ${fromOf(f)}${f.me === false ? " (me off)" : ""}`].filter(Boolean).join(" + ")
      L.push(`- ${n}: ${titleOf(f)}${f.sid === sid ? " (this)" : ""} | ${flags} | last run ${ago(rt?.[f.sid]?.lastRun, now)}`)
    }
  }
  return L.join("\n")
}


// ---------------------------------------------------------------- one pacer per process
// OpenCode loads one plugin instance per location (directory) and may load this module fresh for each one.
// Measured on 2.4.1: the context hook and the /pacer executor fire only in the session's own location, but
// every instance's event subscription sees every session. So all state and all timers live in ONE core on
// globalThis (shared across module copies), ref-counted by the instances; each instance only registers its
// hook + command and delegates. The core key carries a hash of this file: an edited copy gets its own core.
const CORE_KEY = Symbol.for("opencode.pacer.core")
const SEQ_KEY = Symbol.for("opencode.pacer.core.seq") // birth order of the cores in this process
let BUILD = "dev"
try { BUILD = createHash("sha1").update(readFileSync(fileURLToPath(import.meta.url))).digest("hex").slice(0, 10) } catch {}

// Get (or make) the process-wide core for this build and take a ref. release() drops it; the last one stops it.
export function acquireCore(make, { build = BUILD, g = globalThis } = {}) {
  const reg = (g[CORE_KEY] ??= {})
  let core = reg[build]
  if (!core) { core = reg[build] = make(); core.refs = 0 }
  core.refs++
  let done = false
  const release = () => {
    if (done) return
    done = true
    if (--core.refs > 0) return
    if (reg[build] === core) delete reg[build]
    try { core.stop?.() } catch {}
  }
  return { core, release }
}

// One model call can reach several hooks: the first decides, the rest wait for it and do nothing.
export const hookKey = (event) => {
  const ms = event?.messages ?? []
  const last = ms[ms.length - 1]
  return `${event?.sessionID}|${last?.id ?? `n${ms.length}:${msgText(last).slice(0, 80)}`}`
}
export function makeOnce(ttl = 120e3) {
  const seen = new Map()
  return (key, fn, now = Date.now()) => {
    for (const [k, v] of seen) if (now - v.at > ttl) seen.delete(k)
    const hit = seen.get(key)
    if (hit) return { first: false, done: hit.done }
    const done = Promise.resolve().then(fn).catch(() => {})
    seen.set(key, { at: now, done })
    return { first: true, done }
  }
}

function createCore() {
  // self-contained: pacer.py sits next to this file; all state goes to a per-user data dir (not the synced config dir)
  const HERE = dirname(fileURLToPath(import.meta.url))
  const DATA = process.env.PACER_DATA || join(homedir(), ".local", "share", "pacer")
  const TOGGLES = join(DATA, "toggles.json"), RUNTIME = join(DATA, "plugin-state.json"), LOG = join(DATA, "plugin.log")
  const PY = process.env.PACER_PY || join(HERE, "pacer.py"), PYTHON = process.env.PACER_PYTHON || (process.platform === "win32" ? "python" : "python3")
  const DEBUG = !!process.env.PACER_DEBUG
  try { mkdirSync(DATA, { recursive: true }) } catch {}

  const log = (what, o = {}) => {
    try {
      try { if (statSync(LOG).size > LOG_MAX) renameSync(LOG, LOG + ".1") } catch {}
      appendFileSync(LOG, `${new Date().toISOString()} ${what} ${JSON.stringify(o)}\n`)
    } catch {}
  }
  const readJson = (p, d) => { try { return JSON.parse(readFileSync(p, "utf8")) } catch { return d } }
  const writeAtomic = (p, o) => { const tmp = `${p}.${process.pid}.tmp`; writeFileSync(tmp, JSON.stringify(o, null, 1)); renameSync(tmp, p) }

  // the instances' clients; core-level calls (synthetic, get, events) use any live one: they work across locations
  const ctxs = []
  const client = () => ctxs[0]

  // toggles.json: re-read when its mtime changes (hand edits work too)
  let tgCache = emptyToggles(), tgMtime = -1
  const toggles = () => {
    try { const m = statSync(TOGGLES).mtimeMs; if (m !== tgMtime) { tgCache = { ...emptyToggles(), ...readJson(TOGGLES, {}) }; tgCache.slots ??= {}; tgMtime = m } }
    catch { if (tgMtime !== 0) { tgCache = emptyToggles(); tgMtime = 0 } }
    return tgCache
  }
  const saveToggles = (tg) => { writeAtomic(TOGGLES, tg); tgCache = tg; try { tgMtime = statSync(TOGGLES).mtimeMs } catch {} }
  const anyPace = (tg) => tg.global || pacedSids(tg).length > 0

  // runtime (survives a restart so a held batch still flushes and the reset wake still fires)
  const R = {}
  const reload = () => { for (const k of Object.keys(R)) delete R[k]; Object.assign(R, { rt: {}, counters: {}, pace: null, prevPace: null, ...readJson(RUNTIME, {}) }) }
  let dirty = false
  // OpenCode restarted (another process saved this state): none of its turns still runs. A `busy` saved mid-turn
  // would keep a held batch waiting for a run end that never comes, so clear it; the tick then flushes what is
  // due. The same process (a hot reload of this file) keeps it. A state with no pid yet (older builds): kept.
  const adopt = () => {
    if (R.pid === process.pid) return
    if (R.pid !== undefined) for (const s of Object.values(R.rt ?? {})) if (s) { s.busy = false; s.ran = false }
    R.pid = process.pid; dirty = true
  }
  reload(); adopt()
  // Standby: an older build's core still live in this process (its file was replaced mid-run, and some
  // locations have not reloaded yet) keeps the decisions; this one only strips, answers status and edits
  // toggles. When the older one goes, this one re-reads its state from disk and takes over.
  const born = (globalThis[SEQ_KEY] = (globalThis[SEQ_KEY] ?? 0) + 1)
  let self = null, waiting = false
  const standby = () => {
    const reg = globalThis[CORE_KEY] ?? {}
    if (Object.values(reg).some((c) => c && c !== self && c.refs > 0 && (c.born ?? 0) < born)) {
      if (!waiting) { waiting = true; log("standby", { build: BUILD }) }
      return true
    }
    if (waiting) { waiting = false; dirty = false; reload(); adopt(); log("active", { build: BUILD }) }
    return false
  }
  const save = () => { if (!dirty || standby()) return; dirty = false; try { writeAtomic(RUNTIME, R) } catch (e) { log("ERR save", { e: String(e) }) } }
  // ran = a run passed the batcher and has not ended yet; its end (not a pacer hold's interrupt) sets lastRunEnd
  const rt = (sid) => (R.rt[sid] ??= { lastRunStart: 0, lastRunEnd: 0, ran: false, held: null, due: null, lastRun: 0, lastSend: 0, idleSince: Date.now(), busy: false, pending: null })
  const count = (sid, k, n = 1) => { const c = (R.counters[sid] ??= {}); c[k] = (c[k] ?? 0) + n; dirty = true }

  const info = new Map() // sid -> { at, title, parentID }
  const getInfo = async (sid) => {
    const hit = info.get(sid)
    if (hit && Date.now() - hit.at < 60e3) return hit
    try { const s = await client().session.get({ sessionID: sid }); const d = s?.data ?? s; const v = { at: Date.now(), title: d?.title, parentID: d?.parentID }; info.set(sid, v); return v }
    catch { return { at: Date.now() } }
  }
  const ancestors = async (sid) => { const out = []; let cur = sid; for (let i = 0; i < 12; i++) { const p = (await getInfo(cur)).parentID; if (!p) break; out.push(p); cur = p } return out }

  const synth = async (sessionID, text, kind, resume) => {
    try {
      for (const p of uiParts(text, kind)) await client().session.synthetic({ sessionID, ...p, resume, metadata: { pacer: { kind } } })
      return true
    } catch (e) { log("ERR synthetic", { sid: sessionID, kind, e: String(e) }); return false }
  }
  // hold with the hook's own instance (the session's location)
  const hold = async (c, sid) => {
    const r = await (c ?? client()).session.interrupt({ sessionID: sid, resume: false })
    return r?.interrupted ?? r?.data?.interrupted ?? r
  }

  // ---- pacer.py every 5 min (only while a slot is paced or global is on)
  let paceBusy = null
  const runPacer = () => (paceBusy ??= new Promise((resolve) => {
    execFile(PYTHON, ["-I", PY, "status", "--json"], { timeout: 30e3, windowsHide: true, env: { ...process.env, PACER_DATA: DATA, PACER_TOGGLES: TOGGLES } }, (err, stdout) => {
      paceBusy = null
      let res
      try { res = JSON.parse(String(stdout).trim().split("\n").pop()) } catch { res = { ok: false, err: err ? `pacer.py ${err.code ?? err.message}`.slice(0, 120) : "bad pacer.py output" } }
      res.atMs = Date.now()
      R.prevPace = R.pace?.ok ? R.pace : R.prevPace
      R.pace = res; dirty = true
      log("pace", { ok: res.ok, mode: res.mode, lanes: res.lanes, err: res.err })
      try { onPace() } catch (e) { log("ERR onPace", { e: String(e) }) }
      resolve(res)
    })
  }))
  const onPace = () => {
    const tg = toggles(), now = Date.now()
    for (const [name, slot] of Object.entries(tg.slots)) {
      if (!slot?.pace) continue
      const s = rt(slot.sid)
      const m = paceMessage(R.prevPace, R.pace, s, now, name)
      if (!m) continue
      s.pending = { text: m.text, wake: m.wake || !!s.pending?.wake, why: m.why, at: s.pending?.at ?? now }
      dirty = true
      log("pace-msg", { slot: name, sid: slot.sid.slice(0, 20), why: m.why, wake: m.wake })
    }
  }

  // ---- flush timer (one per process; a tick never overlaps the previous one)
  let lastPaceRun = 0, ticking = false
  const tick = async () => {
    if (ticking || !client() || standby()) return
    ticking = true
    try {
      const tg = toggles(), now = Date.now()
      if (anyPace(tg) && now - lastPaceRun >= PACE_MS) { lastPaceRun = now; runPacer() }
      for (const sid of Object.keys(R.rt)) {
        const s = R.rt[sid], cfg = cfgOf(tg, sid)
        if (!s) continue
        if (s.pending && !cfg?.pace) { s.pending = null; dirty = true } // slot moved or turned off: its ping goes with it
        if (!s.held && !s.pending) continue
        if (s.pending && !s.pending.wake) {
          // a note that wakes nothing: it steers into a running turn, or waits in the inbox for the next run
          const text = s.pending.text, why = s.pending.why
          s.pending = null; dirty = true // taken before the await: nothing else can send it again
          if (await synth(sid, text, "note", false)) { s.lastSend = now; log("note", { sid: sid.slice(0, 20), why }) }
          else if (!s.pending) s.pending = { text, why, wake: false } // failed: retry next tick
          continue
        }
        // a pace wake with nothing held: arrival mode opens its own window at the wake; reply mode counts from the last run
        const wakeBase = !s.pending ? 0 : fromOf(cfg) === "arrival" ? (s.pending.at ?? 0) : Math.max(s.lastRunStart ?? 0, s.lastRunEnd ?? 0)
        const batchDue = cfg?.batch ? Math.max(s.due ?? 0, s.pending ? wakeBase + (cfg.period ?? 60) * 1000 : 0) : 0
        const due = s.held ? (cfg?.batch ? s.due ?? now : now) : batchDue // no batch any more: flush held at once
        if (s.busy || now < due) continue
        const text = flushText(s.held, s.pending?.text, now)
        if (!text) { s.held = null; s.pending = null; continue }
        // leading newline: a provider may glue all inputs since the last reply with no separator
        if (await synth(sid, "\n" + text, s.held ? "flush" : "wake", true)) {
          log("flush", { sid: sid.slice(0, 20), items: s.held?.ids.length ?? 0, pace: s.pending?.why })
          if (s.held) count(sid, "flushes")
          if (s.pending) s.lastSend = now
          s.held = null; s.due = null; s.pending = null; dirty = true
        }
      }
    } catch (e) { log("ERR tick", { e: String(e?.stack ?? e).slice(0, 400) }) }
    finally { ticking = false }
    save()
  }
  const timer = setInterval(tick, TICK_MS)
  timer.unref?.()

  // ---- the context hook (every instance delegates here): act only on run starts, once per model call
  const noticed = new Map()
  const once = makeOnce()
  const decide = async (c, event, msgs) => {
    const tail = runStartTail(msgs)
    if (!tail || standby()) return
    const sid = event.sessionID, now = Date.now(), tg = toggles(), cfg = cfgOf(tg, sid)
    // Global
    if (tg.global && R.pace?.ok && !tail.some((m) => PASS.has(pacerKind(m)))) {
      const anc = await ancestors(sid)
      if (globalBlocks(tg, R.pace.mode, sid, anc)) {
        const ok = await hold(c, sid)
        log("global-hold", { sid: sid.slice(0, 20), mode: R.pace.mode, interrupted: ok })
        if (ok && now - (noticed.get(sid) ?? 0) > 10 * 60e3) {
          noticed.set(sid, now)
          const who = Object.entries(tg.slots).filter(([, s]) => s.pace).map(([n, s]) => `${n} (${titleOf(s)})`).join(", ") || "the paced slots"
          const d7hit = (R.pace.usage?.d7?.pct ?? 0) >= 100
          const until = now + ((d7hit ? R.pace.left7 : R.pace.left5) ?? 0) * 1000
          await synth(sid, `pacer global: budget held for ${who} until ${hhmm(until)}; \`/pacer global off\` to override`, "notice", false)
        }
        return
      }
    }
    if (!cfg?.batch) return
    const s = rt(sid)
    const r = decideBatch(s, cfg, tail, now)
    if (r.action === "hold") {
      const ok = await hold(c, sid)
      if (!ok) { log("hold-failed", { sid: sid.slice(0, 20), why: r.why }); return } // nothing stopped: let it run
      const before = s.held?.ids.length ?? 0
      Object.assign(s, { lastRunStart: r.st.lastRunStart, held: r.st.held, due: r.st.due, skipUntil: r.st.skipUntil, busy: false, idleSince: now })
      count(sid, "held"); count(sid, "items", (s.held?.ids.length ?? 0) - before)
      // an "other" item: log its shape (role + metadata keys, never the text) so the classifier can learn it
      const other = tail.filter((m) => itemKind(m) === "other").map((m) => `${m?.role}:${Object.keys(m?.metadata ?? {}).join("+") || "-"}`)
      log("hold", { sid: sid.slice(0, 20), why: r.why, items: s.held?.ids.length, due: hhmmss(s.due), ...(other.length ? { other } : {}) })
    } else {
      Object.assign(s, { lastRunStart: r.st.lastRunStart, held: null, due: null, skipUntil: null, ran: true })
      if (s.pending?.wake) s.pending = { ...s.pending, wake: false } // rides along as a note
      count(sid, "passed")
      log("pass", { sid: sid.slice(0, 20), why: r.why, n: tail.length })
    }
    dirty = true
  }
  const context = async (c, loc, event) => {
    try {
      const msgs = event.messages
      const stripped = stripOwn(msgs) // every hook strips its own copy (idempotent)
      if (stripped) log("strip", { sid: String(event.sessionID).slice(0, 20), n: stripped })
      const key = hookKey(event)
      const o = once(key, () => decide(c, event, msgs))
      if (DEBUG) log("hook", { loc, key: key.slice(0, 60), first: o.first })
      await o.done
    } catch (e) { log("ERR context", { e: String(e?.stack ?? e).slice(0, 400) }) }
  }

  // ---- /pacer: the TUI's RPC (or the fallback server command) delegates here. Returns the reply text.
  // echo: "always" (typed server command) | "status" (RPC: only a status goes into the transcript; the TUI
  // shows every other reply as a toast) | "never"
  const command = async (inv, echo = "always") => {
    const sid = inv?.sessionID
    let reply, op = { op: "usage" }
    try {
      op = parseArgs(inv?.prompt?.text)
      const now = Date.now()
      const passive = standby()
      if (passive) reload() // read-only view of the older core's state
      if (passive && op.op === "flush") reply = "[pacer] flush waits: an older pacer build still runs in this server; restart OpenCode to finish loading the new one."
      else if (op.op === "status") {
        // a status you ask for reads fresh usage when the last read is over 5 min old, paced or not
        // (before: only while something was paced, so the line could show a 5 h window that had already reset)
        if (!passive && (!R.pace?.atMs || now - R.pace.atMs >= PACE_MS)) await runPacer()
        reply = statusText({ tg: toggles(), sid, pace: R.pace, counters: R.counters, rt: R.rt, now })
      } else if (op.op === "flush") {
        const tg = toggles(), name = op.name ?? slotOf(tg, sid)
        const slot = name ? tg.slots[name] : null
        const s = slot ? R.rt[slot.sid] : null
        if (!slot) reply = `[pacer] ${name ? `no slot ${name}.` : "this session holds no slot."}`
        else if (!s?.held?.ids?.length) reply = `[pacer] slot ${name}: nothing held.`
        else {
          s.due = 0; dirty = true
          reply = `[pacer] slot ${name}: flushing ${s.held.ids.length} held ${s.busy ? "after the running turn" : "now"}.`
        }
        log("cmd", { sid: String(sid).slice(0, 20), op: "flush", name })
      } else if (op.op === "now") {
        // one-shot: the next run start here passes the batcher (the TUI's /now sends its prompt right after)
        // a running turn takes the prompt as a steer (no run start), so no flag: it would let the next wake pass
        const s = rt(sid), n = s.held?.ids?.length ?? 0
        if (s.busy) reply = "[pacer] now: a turn is running; your prompt goes straight into it."
        else {
          s.skipUntil = now + SKIP_MS; dirty = true
          reply = `[pacer] now: your next prompt passes${n ? `; the ${n} held ride along` : ""}.`
        }
        log("cmd", { sid: String(sid).slice(0, 20), op: "now", held: n, busy: !!s.busy })
      } else {
        const title = (await getInfo(sid)).title
        const r = applyOp(toggles(), sid, title, op)
        if (r.changed) {
          saveToggles(r.tg)
          for (const k of Object.keys(R.rt)) if (R.rt[k].held && !cfgOf(r.tg, k)?.batch) { R.rt[k].due = 0; dirty = true } // batch gone there: flush now, strand nothing
          if (op.op === "move" || op.op === "pace") rt(sid) // track busy/idle here from now on
          if ((op.op === "pace" && op.on) || (op.op === "global" && op.on)) { lastPaceRun = Date.now(); runPacer() }
        }
        reply = r.reply
        log("cmd", { sid: String(sid).slice(0, 20), op: op.op, name: op.name, changed: r.changed })
      }
    } catch (e) { reply = `[pacer] error: ${String(e).slice(0, 200)}`; log("ERR cmd", { e: String(e?.stack ?? e).slice(0, 400) }) }
    if (sid && (echo === "always" || (echo === "status" && op.op === "status"))) await synth(sid, reply, "status", false)
    return reply
  }

  // ---- events: ONE subscription per process (every instance sees every session); fails over to another
  // instance's client when its owner unloads
  const TYPES = ["session.execution.started", "session.execution.succeeded", "session.execution.failed", "session.execution.interrupted", "session.archived", "session.deleted"]
  const seenType = new Set(), seenId = []
  let stopped = false, sub = null // { c, ac }
  const onEvent = (e) => {
    if (standby()) return
    if (e?.id) { if (seenId.includes(e.id)) return; seenId.push(e.id); if (seenId.length > 200) seenId.shift() }
    if (!seenType.has(e?.type)) { seenType.add(e?.type); log("event-shape", { type: e?.type, keys: Object.keys(e?.data ?? {}) }) }
    const sid = e?.data?.sessionID ?? e?.data?.id ?? e?.data?.info?.id
    if (!sid) return
    if (e.type === "session.archived" || e.type === "session.deleted") {
      const tg = toggles()
      const gone = Object.keys(tg.slots).filter((n) => tg.slots[n]?.sid === sid)
      if (gone.length) { const t = { ...tg, slots: { ...tg.slots } }; for (const n of gone) delete t.slots[n]; saveToggles(t); log("slot-dropped", { slots: gone, why: e.type }) }
      if (R.rt[sid] || R.counters[sid]) { delete R.rt[sid]; delete R.counters[sid]; dirty = true }
      return
    }
    const s = R.rt[sid] ?? (cfgOf(toggles(), sid) ? rt(sid) : null)
    if (!s) return
    if (e.type === "session.execution.started") { s.busy = true; s.lastRun = Date.now() }
    else {
      s.busy = false; s.idleSince = Date.now()
      if (s.ran) { s.ran = false; s.lastRunEnd = Date.now() } // a held run's interrupt leaves it alone
    }
    dirty = true
  }
  const subscribe = async () => {
    while (!stopped && !sub) {
      const c = client()
      if (!c) return // restarts on the next attach
      const ac = new AbortController()
      sub = { c, ac }
      try {
        for await (const e of c.event.subscribe({ signal: ac.signal, types: TYPES })) {
          try { onEvent(e) } catch (err) { log("ERR event", { e: String(err) }) }
        }
      } catch (err) { if (!ac.signal.aborted) log("ERR subscribe", { e: String(err).slice(0, 200) }) }
      sub = null
      if (!stopped) await new Promise((r) => setTimeout(r, ac.signal.aborted ? 50 : 1000).unref?.())
    }
  }

  self = {
    refs: 0,
    build: BUILD,
    born,
    get standby() { return standby() },
    get R() { return R },
    attach(ctx, loc) {
      ctxs.push(ctx)
      if (!sub) subscribe().catch((e) => log("ERR subscribe", { e: String(e) }))
      log("setup", { loc, refs: this.refs, build: BUILD, pid: process.pid, data: DATA, py: PY, slots: Object.keys(toggles().slots) })
    },
    detach(ctx) {
      const i = ctxs.indexOf(ctx)
      if (i >= 0) ctxs.splice(i, 1)
      if (sub?.c === ctx) sub.ac.abort() // the loop moves to the next instance's client
    },
    context, command, log,
    stop() {
      stopped = true
      clearInterval(timer)
      try { sub?.ac.abort() } catch {}
      save()
      log("stop", { build: BUILD })
    },
  }
  return self
}

// ---------------------------------------------------------------- the plugin (one per location; thin)
export default {
  id: "pacer",
  async setup(ctx) {
    const loc = ctx?.location?.directory
    const { core, release } = acquireCore(createCore)
    core.attach(ctx, loc)
    let reg, cmd, rpc
    try {
      reg = await ctx.session.hook("context", (event) => core.context(ctx, loc, event))
      // the TUI's /pacer calls this RPC, so the server adds no /pacer of its own (one entry in the slash list).
      // No RPC here (older OpenCode): fall back to the server command, which the TUI also knows how to call.
      try {
        const { PacerRpc } = await import("./rpc.js")
        // register walks `events` (Object.entries): an rpc.js cached from before it had one would throw again
        const def = PacerRpc.events ? PacerRpc : { ...PacerRpc, events: {} }
        rpc = await ctx.rpc.register(def, {
          command: async (input) => ({ reply: await core.command({ sessionID: input?.sessionID, prompt: { text: input?.text ?? "" } }, "status") }),
        })
      } catch (e) {
        rpc = null
        core.log?.("rpc-fallback", { loc, e: String(e?.message ?? e).slice(0, 200) })
        cmd = await ctx.command.transform((editor) => {
          editor.add({
            name: "pacer",
            description: "pacer: pace usage, batch wakes, global hold (/pacer status | on [slot] | off [slot] | batch ... | flush | move [slot])",
            execute: (inv) => core.command(inv, "always"),
          })
        })
      }
    } catch (e) { core.detach(ctx); release(); throw e }
    return () => {
      try { reg?.dispose?.() } catch {}
      try { rpc?.dispose?.() } catch {}
      try { cmd?.dispose?.() } catch {}
      core.detach(ctx)
      release()
    }
  },
}
