// pacer TUI, pure parts: status files -> dialog rows, row -> /pacer command text. No OpenCode imports
// (unit-tested by tests/tui.test.mjs). The TUI never writes pacer state: every change is a /pacer command
// that the server plugin executes (one writer).

export const PERIODS = [30, 60, 120, 240, 300, 600]
export const TTL_WARN = "may lose the cache if its TTL is 5 min"
export const ttlWarning = (period) => (Number(period) > 240 ? TTL_WARN : "")

// same rules as the server's validName (plugin/index.js)
const KEYWORDS = new Set(["on", "off", "all", "status", "move", "week", "global", "batch", "me", "pace", "flush", "from", "now"])
export const validName = (n) => /^[a-z0-9][a-z0-9_-]{0,31}$/.test(n) && !KEYWORDS.has(n)

// available = the pacer data dir exists on this machine (not true for a TUI on a remote server)
export function parseStatus(available, togglesText, stateText) {
  if (!available) return { available: false, tg: null, st: null }
  /** @type {{week: boolean, global: boolean, slots: Record<string, any>} | null} */
  let tg = { week: false, global: false, slots: {} } // no toggles.json yet = everything off
  if (togglesText != null) { try { tg = { ...tg, ...JSON.parse(togglesText) } } catch { tg = null } }
  if (tg) tg.slots ??= {}
  let st = null
  if (stateText != null) { try { st = JSON.parse(stateText) } catch {} }
  return { available: true, tg, st }
}

export const slotOf = (tg, sid) => Object.keys(tg?.slots ?? {}).find((n) => tg.slots[n]?.sid === sid) ?? null

export function ago(ms, now) {
  if (!ms) return "never"
  const s = Math.max(0, Math.round((now - ms) / 1000))
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}
const pct = (x) => (typeof x === "number" ? `${Math.round(x)}%` : "?")

// dialog title = the usage line
export function headline(s, now) {
  if (!s?.available) return "pacer · status unavailable here"
  const p = s.st?.pace
  if (!p) return "pacer · usage not read yet (pace a slot)"
  if (!p.ok) return `pacer · usage error: ${String(p.err ?? "?").slice(0, 60)}`
  return `pacer · 5h ${pct(p.usage?.h5?.pct)} · week ${pct(p.usage?.d7?.pct)} · ${p.mode ?? "?"} · lanes ≤${p.lanes ?? "?"} · ${ago(p.atMs, now)}`
}

// "from arrival" is the default and stays out of the short flags; "from reply" shows
export const flagsOf = (s) => [s?.pace && "pace", s?.batch && `batch ${s.period ?? 60}s${s?.from === "reply" ? " from reply" : ""}`, s?.batch && s?.me === false && "me off"].filter(Boolean).join(" + ") || "-"

const mark = (v) => (v === undefined ? "[?]" : v ? "[x]" : "[ ]")

// the dialog options. Values are stable strings so the list can reopen on the row just used.
export function rows({ available, tg, st, sid, now }) {
  const known = !!tg
  const own = known ? slotOf(tg, sid) : null
  const cfg = own ? tg.slots[own] : undefined
  const flag = (k) => (known ? !!cfg?.[k] : undefined)
  const here = !available ? "This session" : !known ? "This session (status unreadable)" : own ? `This session · slot ${own}` : "This session · not paced"
  const period = cfg?.period ?? 60
  const reply = cfg?.from === "reply"
  const held = heldHere(st, sid, now)
  const out = [
    { value: "pace", title: `${mark(flag("pace"))} pace`, description: "usage line, mode pings for this slot", category: here },
    { value: "batch", title: `${mark(flag("batch"))} batch`, description: reply ? `hold wakes until ${period}s after my last run` : `hold wakes, flush ${period}s after the first one`, category: here },
    { value: "period", title: `    period ${known ? `${period}s` : "?"}`, description: ttlWarning(period) || "pick 30 / 60 / 120 / 240 s (turns batch on)", category: here },
    known && !cfg
      ? { value: "from", title: "    from arrival", description: "needs a slot here first (pace or batch on)", category: here, disabled: true }
      : { value: "from", title: `    from ${!known ? "?" : reply ? "reply" : "arrival"}`, description: !known ? "arrival: first wake opens the window · reply: counts from my last run" : reply ? "the period counts from my last run · Enter: from arrival" : "the first wake opens the window, later ones join · Enter: from reply", category: here },
    known && !cfg
      ? { value: "me", title: "[ ] batch me", description: "needs a slot here first (pace or batch on)", category: here, disabled: true }
      : { value: "me", title: `${mark(known ? cfg.me !== false : undefined)} batch me`, description: "your own prompts wait for the period too", category: here },
    { value: "week", title: `${mark(known ? !!tg.week : undefined)} week`, description: "count the weekly limit in every decision", category: "Account" },
    { value: "global", title: `${mark(known ? !!tg.global : undefined)} global`, description: "in HOLD/LIMITED stop sessions outside every paced slot", category: "Account" },
  ]
  if (held && own) out.splice(out.findIndex((r) => r.value === "week"), 0, { value: "flush", title: "    flush now", description: `${held.n} held · ${held.when} · /now sends it too`, category: here })
  if (known) {
    const names = Object.keys(tg.slots)
    for (const n of names) {
      const s = tg.slots[n], mine = s?.sid === sid
      out.push({
        value: `slot:${n}`,
        title: `${n}${mine ? " (here)" : ""} · ${s?.title || String(s?.sid ?? "?").slice(0, 16)}`,
        description: `${flagsOf(s)} · last run ${ago(st?.rt?.[s?.sid]?.lastRun, now)}${mine ? "" : " · Enter: move here"}`,
        category: "Slots",
      })
    }
    if (!names.length) out.push({ value: "noslots", title: "no slots yet", description: "turn pace or batch on above", category: "Slots", disabled: true })
  }
  out.push(
    { value: "new", title: "new slot here…", description: own ? `this session holds ${own}: off it first` : "name a slot for this session (pace on)", category: "Actions" },
    { value: "status", title: "status in the transcript", description: "same as /pacer status", category: "Actions" },
    { value: "offall", title: "off all…", description: "every slot off, week off, global off", category: "Actions" },
  )
  return out
}

const both = (title, on, off) => ({ kind: "choose", title, options: [{ title: "on", value: on }, { title: "off", value: off }] })

// what Enter on a row does. kind: run (send text) | choose (sub-list of texts) | confirm | prompt | none
export function rowAction(value, { tg, sid }) {
  const known = !!tg
  const own = known ? slotOf(tg, sid) : null
  const cfg = own ? tg.slots[own] : undefined
  switch (value) {
    case "pace": return known ? { kind: "run", text: cfg?.pace ? "pace off" : "on" } : both("pace", "on", "pace off")
    case "batch": return known ? { kind: "run", text: cfg?.batch ? "batch off" : "batch on" } : both("batch", "batch on", "batch off")
    case "period": return {
      kind: "choose", title: "batch period", current: `batch ${cfg?.period ?? 60}`,
      options: PERIODS.map((p) => ({ title: `${p}s`, value: `batch ${p}`, description: ttlWarning(p) || undefined })),
    }
    case "me":
      if (!known) return both("batch me", "batch me on", "batch me off")
      return cfg ? { kind: "run", text: `batch me ${cfg.me !== false ? "off" : "on"}` } : { kind: "none", reason: "turn pace or batch on here first" }
    case "from":
      if (!known) return { kind: "choose", title: "batch from", options: [{ title: "arrival", value: "batch from arrival" }, { title: "reply", value: "batch from reply" }] }
      return cfg ? { kind: "run", text: `batch from ${cfg.from === "reply" ? "arrival" : "reply"}` } : { kind: "none", reason: "turn pace or batch on here first" }
    case "week": case "global": return known ? { kind: "run", text: `${value} ${tg[value] ? "off" : "on"}` } : both(value, `${value} on`, `${value} off`)
    case "status": return { kind: "run", text: "status", close: true }
    case "flush": return { kind: "run", text: "flush", close: true }
    case "new": return { kind: "prompt", title: "new pacer slot", description: "a-z 0-9 _ - (max 32). Paces this session.", placeholder: "main" }
    case "offall": return { kind: "confirm", text: "off all", title: "pacer: off all", message: "Turn every slot off, and week and global off?", label: { confirm: "Off all", cancel: "Back" } }
  }
  if (value?.startsWith("slot:")) {
    const n = value.slice(5)
    if (known && tg.slots[n]?.sid === sid) return { kind: "none", reason: `slot ${n} is already here` }
    return { kind: "run", text: `move ${n}` }
  }
  return { kind: "none" }
}

export function newSlotCommand(raw) {
  const n = String(raw ?? "").trim().toLowerCase()
  if (!n) return { error: "no name given" }
  return validName(n) ? { text: `on ${n}` } : { error: `bad slot name "${n.slice(0, 40)}": a-z 0-9 _ - (max 32), not a keyword` }
}

// what this session holds right now (plugin-state.json rt), or null. when = "flush in 23s" | "flushing…" |
// "flush after this turn"
export function heldHere(st, sid, now) {
  const r = sid ? st?.rt?.[sid] : null
  const n = r?.held?.ids?.length ?? 0
  if (!n) return null
  if (r.busy) return { n, left: null, when: "flush after this turn" }
  const left = Math.max(0, Math.ceil(((r.due ?? now) - now) / 1000))
  return { n, left, when: left ? `flush in ${left}s` : "flushing…" }
}
// the prompt-footer line: the timer while something is held here, else this session's slot flags (so you can
// see the pacer is live); empty in a session with no slot
export function footerText(stateText, sid, now, togglesText) {
  let st = null
  try { st = stateText ? JSON.parse(stateText) : null } catch { st = null }
  const h = heldHere(st, sid, now)
  if (h) return `pacer: ${h.n} held · ${h.when}`
  let tg = null
  try { tg = togglesText ? JSON.parse(togglesText) : null } catch { return "" }
  const own = sid ? slotOf(tg, sid) : null
  const flags = own ? flagsOf(tg.slots[own]) : "-"
  return flags === "-" ? "" : `pacer: ${flags} · nothing held`
}

// the server's reply -> a toast (status replies go into the transcript instead)
export function replyToast(cmd, reply) {
  if (/^\s*status\s*$/i.test(String(cmd ?? "")) || !reply) return null
  const text = String(reply).replace(/^\[pacer[^\]]*\]\s*/, "").trim().slice(0, 300)
  const variant = /\berror\b/i.test(text) ? "error" : /^usage:/i.test(text) ? "warning" : "success"
  return { message: text, variant }
}

// `/now <prompt>`: the prompt skips the batcher (anything held rides along); bare `/now` flushes what is held
export const nowRoute = (input) => (String(input ?? "").trim() ? { kind: "prompt", text: String(input).trim() } : { kind: "flush" })

// typed `/pacer <args>` goes to the server unchanged; bare `/pacer` (or the palette) opens the dialog
export const slashRoute = (input) => (String(input ?? "").trim() ? { kind: "forward", text: String(input).trim() } : { kind: "open" })
