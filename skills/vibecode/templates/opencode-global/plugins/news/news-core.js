// news-panel pure core: no OpenCode imports, no I/O. Shared by index.js (server capture, sole writer of
// items.json) and tui.js (views; sole writer of checks.json + toasts.json). Everything here is a pure function
// over plain JSON so it can be tested with node --test.
//
// Marker (one line per item, written by the coordinator in its reply text; zero model cost, no tool call):
//   📌 [<tag>] <kind>: <short text>
//   kind = "waits on you" (alias "waits") | "bug" | "news"; tag = [a-z0-9][a-z0-9._-]{0,39} (lowercased)
// A marker inside a ``` / ~~~ fence or inline code is not an item. Same tag again = update, never a duplicate.

export const HISTORY_MS = 14 * 864e5
export const TOAST_CAP = 300
export const TEXT_CAP = 200
export const KINDS = ["waits on you", "bug", "news"]
const TAG_RE = /^[a-z0-9][a-z0-9._-]{0,39}$/
const KIND_ALIAS = { "waits on you": "waits on you", waits: "waits on you", bug: "bug", news: "news" }
// optional bullets / numbers / quotes, optional bold, then the pin
const MARKER_RE = /^\s*(?:(?:[-*+]|\d+[.)]|>)\s*)*(?:\*\*|__)?\s*📌\s*\[([^\]]*)\]\s*([^:\]]+?)\s*:\s*(.*)$/u
const FENCE_RE = /^\s{0,3}(`{3,}|~{3,})(.*)$/

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v)
const cap = (s, n = TEXT_CAP) => (s.length > n ? s.slice(0, n - 1) + "…" : s)

// ---------------------------------------------------------------- parser
// the lines of `text` that are outside ``` / ~~~ fences
function* proseLines(text) {
  if (typeof text !== "string" || !text) return
  let fence = null // { ch, len } while inside a fence
  for (const line of text.split(/\r?\n/)) {
    const f = FENCE_RE.exec(line)
    if (fence) {
      if (f && f[1][0] === fence.ch && f[1].length >= fence.len && !f[2].trim()) fence = null
      continue
    }
    if (f && !(f[1][0] === "`" && f[2].includes("`"))) { fence = { ch: f[1][0], len: f[1].length }; continue }
    yield line
  }
}

export function parseMarkers(text) {
  const out = new Map() // tag -> marker; first position kept, last value wins
  for (const line of proseLines(text)) {
    const m = MARKER_RE.exec(line)
    if (!m) continue
    const tag = m[1].trim().toLowerCase()
    const kind = KIND_ALIAS[m[2].trim().toLowerCase().replace(/\s+/g, " ")]
    const body = m[3].replace(/\s*(?:\*\*|__)\s*$/, "").trim()
    if (!TAG_RE.test(tag) || !kind || !body) continue
    out.set(tag, { tag, kind, text: cap(body) })
  }
  return [...out.values()]
}

// Done line (the walk-through reply ends with one per settled item):  📌 [<tag>] done  (optional ": why",
// "— why", bullets, bold, or the whole line in `inline code`, since the walk prompt shows it that way).
// Never an item: "done" isn't a kind. Fenced lines don't count.
const DONE_RE = /^\s*(?:(?:[-*+]|\d+[.)]|>)\s*)*(?:\*\*|__)?`?\s*📌\s*\[([^\]]*)\]\s*done(?:\s*(?:\*\*|__))?`?\s*(?:[.!]|[:—–-].*)?\s*$/iu
export function parseDone(text) {
  const out = new Map()
  for (const line of proseLines(text)) {
    const m = DONE_RE.exec(line)
    if (!m) continue
    const tag = m[1].trim().toLowerCase()
    if (TAG_RE.test(tag) && !out.has(tag)) out.set(tag, { tag })
  }
  return [...out.values()]
}

// ---------------------------------------------------------------- items (server side)
// meta = { sessionID, messageID, sessionTitle?, directory? }
export function applyMarkers(items, markers, meta, now) {
  const next = { ...(isObj(items) ? items : {}) }
  const changed = []
  for (const m of markers ?? []) {
    const prev = isObj(next[m.tag]) ? next[m.tag] : undefined
    if (prev && prev.kind === m.kind && prev.text === m.text && prev.messageID === meta?.messageID && prev.sessionID === meta?.sessionID) continue
    next[m.tag] = {
      tag: m.tag, kind: m.kind, text: m.text,
      sessionID: meta?.sessionID, messageID: meta?.messageID,
      sessionTitle: meta?.sessionTitle ?? prev?.sessionTitle, directory: meta?.directory ?? prev?.directory,
      created: prev?.created ?? now, updated: now,
    }
    changed.push(m.tag)
  }
  return { items: next, changed }
}

// ---------------------------------------------------------------- checks (TUI side)
// checks = { [tag]: { at, kind, text } }. An item is checked while its kind+text still equal the checked ones:
// the model can only update an item, and new kind/text under a checked tag reopens it (that is news again).
export const isOpen = (item, check) => !check || check.kind !== item?.kind || check.text !== item?.text
// by: who checked it when it wasn't a press ("walk" = a `📌 [tag] done` line from the walk-through reply)
export const checkItem = (checks, item, now, by) => ({ ...(checks ?? {}), [item.tag]: { at: now, kind: item.kind, text: item.text, ...(by ? { by } : {}) } })
export function uncheckItem(checks, tag) { const c = { ...(checks ?? {}) }; delete c[tag]; return c }

// waits on you -> bugs -> news (anything else last), newest first inside a kind
const rank = (i) => { const n = KINDS.indexOf(i.kind); return n < 0 ? KINDS.length : n }
const valid = (items) => Object.values(isObj(items) ? items : {}).filter((i) => isObj(i) && typeof i.tag === "string")
// Only "waits on you" needs the user: that is what "open" means (counts, footer, walk). Bugs + news are FYI:
// shown under the waits until the AI writes `📌 [tag] done` in their session, or the user checks them.
export const needsYou = (i) => i?.kind === "waits on you"
// every unchecked item, in pop-up order: waits on you -> bugs -> news, newest first inside a kind
export function shownList(items, checks) {
  return valid(items).filter((i) => isOpen(i, checks?.[i.tag]))
    .sort((a, b) => rank(a) - rank(b) || (b.updated ?? 0) - (a.updated ?? 0))
}
export const openList = (items, checks) => shownList(items, checks).filter(needsYou)
export const fyiList = (items, checks) => shownList(items, checks).filter((i) => !needsYou(i))
export function historyList(items, checks, now) {
  return valid(items).filter((i) => { const c = checks?.[i.tag]; return c && !isOpen(i, c) && now - (c.at ?? 0) <= HISTORY_MS })
    .sort((a, b) => (checks[b.tag].at ?? 0) - (checks[a.tag].at ?? 0))
}

// server, on each write: drop items checked (and unchanged) more than 14 days ago; open items stay
export function pruneItems(items, checks, now) {
  const out = {}
  for (const i of valid(items)) {
    const c = checks?.[i.tag]
    if (c && !isOpen(i, c) && now - (c.at ?? 0) > HISTORY_MS) continue
    out[i.tag] = i
  }
  return out
}
// TUI: drop a check once it is old and its item is gone. items undefined (unreadable file) -> keep all.
export function pruneChecks(checks, items, now) {
  if (!isObj(items)) return checks
  const out = {}
  for (const [tag, c] of Object.entries(checks ?? {})) {
    if (!items[tag] && now - (c?.at ?? 0) > HISTORY_MS) continue
    out[tag] = c
  }
  return out
}

// ---------------------------------------------------------------- toast log
export function pushToast(log, t, now) {
  if (!t || typeof t.message !== "string" || !t.message.trim()) return log
  const e = { at: now, title: t.title ? String(t.title) : undefined, message: cap(t.message, 500), variant: t.variant ?? "info", source: t.source ?? "self" }
  return [e, ...(Array.isArray(log) ? log : [])].filter((x) => now - (x?.at ?? 0) <= HISTORY_MS).slice(0, TOAST_CAP)
}

// new / changed open items since the last snapshot (first call = silent baseline)
const sig = (i) => `${i.kind}\u0000${i.text}`
export function freshItems(snap, items, checks) {
  const next = {}
  for (const i of valid(items)) next[i.tag] = sig(i)
  if (!snap) return { fresh: [], snap: next }
  const fresh = shownList(items, checks).filter((i) => snap[i.tag] !== next[i.tag]) // news is still news: toast it
  return { fresh, snap: next }
}

// ---------------------------------------------------------------- storage (fail-open)
const LISTS = new Set(["toasts", "dones", "seen"])
export function parseStore(raw, key) {
  const def = LISTS.has(key) ? [] : {}
  if (typeof raw !== "string") return { ok: false, value: def }
  try {
    const v = JSON.parse(raw)?.[key]
    if (LISTS.has(key) ? Array.isArray(v) : isObj(v)) return { ok: true, value: v }
  } catch {}
  return { ok: false, value: def }
}

// ---------------------------------------------------------------- views
export function relTime(t, now) {
  const s = Math.max(0, Math.floor((now - (t ?? now)) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

// counts only what waits on the user; FYI alone -> a bare "📌 FYI" so the footer stays a click target
export function footerText(items, checks) {
  const w = openList(items, checks).length
  if (w) return `📌 ${w} waits on you`
  return fyiList(items, checks).length ? "📌 FYI" : ""
}
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`
function fyiLine(items, checks) {
  const f = fyiList(items, checks)
  if (!f.length) return null
  const b = f.filter((i) => i.kind === "bug").length, n = f.length - b
  return `FYI: ${[b ? plural(b, "bug", "bugs") : "", n ? `${n} news` : ""].filter(Boolean).join(", ")} (/${SLASH})`
}

// the slash command (2-letter prefix "to" is unique in the v2.4.1 menu: built-ins, plugins, server, project)
export const SLASH = "todo"

// ---------------------------------------------------------------- display width
// Kind marks. All three are Emoji_Presentation + East Asian Wide: 2 columns, no VS16 needed, so opentui
// (which measures with Bun.stringWidth) lays them out like CJK.
export const MARKS = { "waits on you": "⏳", bug: "🐞", news: "📰" }
const markOf = (i) => MARKS[i?.kind] ?? "·"

// Same runtime as the TUI (Bun) -> Bun.stringWidth, exactly what opentui measures. Node (tests) -> a
// small fallback: emoji / wide CJK = 2, combining + format chars = 0, else 1.
const seg = typeof Intl?.Segmenter === "function" ? new Intl.Segmenter(undefined, { granularity: "grapheme" }) : null
const graphemes = (s) => (seg ? Array.from(seg.segment(s), (x) => x.segment) : Array.from(s))
const WIDE = /[ᄀ-ᅟ⺀-〾ぁ-㏿㐀-䶿一-鿿ꀀ-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦\u{20000}-\u{3FFFD}]/u
function fallbackWidth(g) {
  if (/\p{Extended_Pictographic}/u.test(g) && (/\p{Emoji_Presentation}/u.test(g) || g.includes("️"))) return 2
  const c = g.replace(/[\p{Mn}\p{Me}\p{Cf}\p{Cc}]/gu, "")
  if (!c) return 0
  return WIDE.test(c) ? 2 : 1
}
const bunWidth = typeof globalThis.Bun?.stringWidth === "function" ? globalThis.Bun.stringWidth : null
const gWidth = (g) => { if (bunWidth) { try { return bunWidth(g) } catch {} } return fallbackWidth(g) }
export function textWidth(s) {
  s = String(s ?? "")
  if (bunWidth) { try { return bunWidth(s) } catch {} }
  let n = 0
  for (const g of graphemes(s)) n += fallbackWidth(g)
  return n
}
const flat = (s) => String(s ?? "").replace(/\s+/g, " ").trim()
// the longest prefix that fits `w` columns (never splits a grapheme), and the rest
function take(s, w) {
  let out = "", n = 0, i = 0
  const gs = graphemes(s)
  for (; i < gs.length; i++) { const x = gWidth(gs[i]); if (n + x > w) break; out += gs[i]; n += x }
  return [out, gs.slice(i).join("")]
}
// one line, at most `w` columns, "…" only when something was cut
export function cutTo(s, w) {
  s = String(s ?? "")
  if (!(w > 0)) return ""
  if (textWidth(s) <= w) return s
  return take(s, w - 1)[0] + "…"
}
// one line, at most `w` columns, no "…": the pop-up clips at the edge and scrolls the selected row instead
export function clipTo(s, w) {
  s = String(s ?? "")
  if (!(w > 0)) return ""
  return textWidth(s) <= w ? s : take(s, w)[0]
}
// drop the first `n` columns (a wide grapheme that straddles the cut goes too)
function dropCols(s, n) {
  if (!(n > 0)) return s
  const gs = graphemes(s)
  let i = 0, c = 0
  for (; i < gs.length && c < n; i++) c += gWidth(gs[i])
  return gs.slice(i).join("")
}
// Inline scroll of one pop-up row: rows carry scrollHead (mark + tag, pinned), scrollBody (the text that
// slides) and scrollW (the title width). Rows without them (walk row, hints) never move.
export const SCROLL_STEP = 8
export const PIN_MIN = 12 // columns the text needs next to a pinned mark + tag
const room = (row) => Math.max(1, (row.scrollW ?? 0) - textWidth(row.scrollHead ?? ""))
export const maxScroll = (row) => (typeof row?.scrollBody === "string" ? Math.max(0, textWidth(row.scrollBody) - room(row)) : 0)
export function scrollTitle(row, off = 0) {
  if (typeof row?.scrollBody !== "string") return row?.title ?? ""
  const o = Math.max(0, Math.min(maxScroll(row), Number(off) || 0))
  return (row.scrollHead ?? "") + clipTo(dropCols(row.scrollBody, o), room(row))
}

// word-wrap to `w` columns (long words hard-split), at most `max` lines, the last one cut with "…"
export function wrapTo(s, w, max = 2) {
  const words = flat(s).split(" ").filter(Boolean)
  if (!(w > 0) || !words.length) return []
  const lines = []
  let cur = ""
  for (let word of words) {
    const cand = cur ? `${cur} ${word}` : word
    if (textWidth(cand) <= w) { cur = cand; continue }
    if (cur) lines.push(cur)
    cur = ""
    while (textWidth(word) > w) { const [a, b] = take(word, w); if (!a) break; lines.push(a); word = b }
    cur = word
  }
  if (cur) lines.push(cur)
  if (lines.length <= max) return lines
  return [...lines.slice(0, max - 1), cutTo(lines.slice(max - 1).join(" "), w)]
}

// v2.4.1 dialog: width = min(medium 60 | large 88 | xlarge 116, terminal cols - 2). Option row (new
// designs): list = width - 4, row = 1 bar + 3 left pad + title + 1 gap + footer + 4 right pad -> title gets
// width - 12 - (footer + 1). One more column of slack: overflow is clipped, which would eat the "…".
const DIALOG = { medium: 60, large: 88, xlarge: 116 }
export const dialogWidth = (cols, size = "large") => Math.min(DIALOG[size] ?? 60, (cols > 0 ? cols : 80) - 2)
export const titleWidth = (cols, footer = "", size = "large") => Math.max(8, dialogWidth(cols, size) - 13 - (footer ? textWidth(footer) + 1 : 0))

// side panel: the built-in sidebar is 42 wide (2+2 padding, 1 inner pad, 1 scrollbar) -> 36 columns of text
export const SIDEBAR_W = 36
export function sidebarLines(items, checks, max = 5, w = SIDEBAR_W) {
  const open = openList(items, checks), fyi = fyiLine(items, checks)
  if (!open.length && !fyi) return ["📌 Todo: none open"]
  const lines = [open.length ? `📌 Todo: ${open.length} waits on you` : "📌 Todo: nothing waits on you"]
  for (const i of open.slice(0, max)) {
    lines.push(cutTo(`${markOf(i)} [${i.tag}]`, w))
    for (const l of wrapTo(i.text, w - 2, 2)) lines.push(`  ${l}`)
  }
  if (open.length > max) lines.push(cutTo(`… +${open.length - max} more (/${SLASH})`, w))
  if (fyi) lines.push(cutTo(fyi, w))
  return lines
}

// ---------------------------------------------------------------- walk me through
// The one prompt the plugin ever sends, and only on a press (tui.js). Self-contained: the instruction, then
// the open items in pop-up order (waits on you, then bugs, then news; newest first inside each) as a
// numbered `N. [tag] kind: text` list (so "answer by number" works), plus `(from <title>)` for items from
// another session than `sid`. No ids beyond that fallback. A heading + list = structured text, so
// prompt-polish passes it through unchanged. Nothing open -> null (send nothing).
export const WALK = "__walk__" // pop-up row value; tags start with [a-z0-9], so it can't collide
export const WALK_MAX = 30
export const WALK_HEAD = "Go through all my open 📌 items in ONE `question` tool call: one question per item that waits on me (your pick first, marked Recommended). After my answers, end your reply with one `📌 [tag] done` line per item my answers settled; skip any I left open."
// the tags the walk prompt lists (what a `📌 [tag] done` line may check afterwards)
export const walkTags = (items, checks) => openList(items, checks).slice(0, WALK_MAX).map((i) => i.tag)
export function walkPrompt(items, checks, sid, titleOf) {
  const open = openList(items, checks)
  if (!open.length) return null
  const lines = [WALK_HEAD, "", `## Open 📌 items (${open.length})`]
  open.slice(0, WALK_MAX).forEach((i, n) => {
    lines.push(`${n + 1}. [${i.tag}] ${i.kind}: ${i.text}${i.sessionID && i.sessionID !== sid ? ` (from ${who(i, titleOf, 40)})` : ""}`)
  })
  if (open.length > WALK_MAX) lines.push(`- … and ${open.length - WALK_MAX} more`)
  return lines.join("\n")
}
export const walkRow = (n, cols = 80) => ({
  value: WALK,
  title: clipTo(`▶ walk me through all ${n} (ctrl+s)`, titleWidth(cols)),
  truncateTitle: false,
  detail: `Sends ONE prompt into the session you're in: the ${n} items that wait on you, as one \`question\` pop-up. The reply's \`📌 [tag] done\` lines check them off here.`,
})

// ---------------------------------------------------------------- walk -> done lines -> checks
// walk = { sid, tags, at } (tui.js writes it before sending); dones = [{ tag, sessionID, messageID, role, at }]
// (index.js captures them from assistant replies). A done line checks its item only when it is an assistant
// line, in the walk's session, after the walk was sent, for a tag the walk listed, and the item is still open.
export const DONES_CAP = 200
export const doneKey = (d) => `${d?.messageID}|${d?.tag}|${d?.at}`
export function pruneDones(list, now) {
  return (Array.isArray(list) ? list : []).filter((d) => isObj(d) && now - (d.at ?? 0) <= HISTORY_MS)
    .sort((a, b) => (b.at ?? 0) - (a.at ?? 0)).slice(0, DONES_CAP)
}
// FYI items (bug / news) need no walk: a done line closes one when it is an assistant line from the item's own
// session, not older than the item's last update, and the item is still open (by: "ai").
export function applyWalkDone(checks, items, walk, dones, now) {
  let next = { ...(checks ?? {}) }
  const checked = [], closed = [], ignored = []
  for (const d of Array.isArray(dones) ? dones : []) {
    if (!isObj(d) || typeof d.tag !== "string") continue
    const it = items?.[d.tag]
    if (isObj(it) && !needsYou(it)) {
      const why = d.role !== "assistant" ? "not an assistant message"
        : d.sessionID !== it.sessionID ? "other session"
        : (d.at ?? 0) < (it.updated ?? 0) ? "before the item"
        : !isOpen(it, next[d.tag]) ? "already checked"
        : null
      if (why) { ignored.push({ tag: d.tag, why }); continue }
      next = checkItem(next, it, now, "ai")
      closed.push(d.tag)
      continue
    }
    const why = !isObj(walk) ? "no walk sent"
      : d.role !== "assistant" ? "not an assistant message"
      : d.sessionID !== walk.sid ? "other session"
      : !(Array.isArray(walk.tags) && walk.tags.includes(d.tag)) ? "tag not in the walk"
      : !((d.at ?? 0) > (walk.at ?? Infinity)) ? "before the walk"
      : !isObj(items?.[d.tag]) ? "no item"
      : !isOpen(items[d.tag], next[d.tag]) ? "already checked"
      : null
    if (why) { ignored.push({ tag: d.tag, why }); continue }
    next = checkItem(next, items[d.tag], now, "walk")
    checked.push(d.tag)
  }
  return { checks: next, checked, closed, ignored }
}
// footer label clicks: left button only (opentui MouseButton LEFT=0; wheel = 4/5); no event info = a click
export const isLeftClick = (ev) => ev?.button === undefined || ev?.button === 0

const who = (i, titleOf, n = 16) => { let t; try { t = titleOf?.(i.sessionID) } catch {} return t || i.sessionTitle || String(i.sessionID ?? "?").slice(0, n) }
const hint = (title) => [{ title, value: null, disabled: true }]
// One line per option: mark, tag, text cut by display width with "…" (truncateTitle:false: the built-in cut
// counts UTF-16 units and has no idea about emoji). The full text + session + time go to `detail`, which
// the v2.4.1 select shows wrapped under the list for the selected option.
// No "…": the title is clipped at the edge, and the selected row scrolls (scrollHead stays, scrollBody slides).
const itemRow = (i, footer, title, cols, detailTail) => {
  const w = titleWidth(cols, footer), head = `${markOf(i)} [${i.tag}] `, body = flat(i.text)
  return {
    value: i.tag,
    title: clipTo(head + body, w),
    truncateTitle: false,
    category: needsYou(i) ? i.kind : `${i.kind} · FYI`,
    footer,
    detail: `${i.text}\n${who(i, title, 40)} · ${detailTail}`,
    // narrow pop-up + long tag: pinning mark + tag would leave no room, so then the whole line slides
    ...(w - textWidth(head) >= PIN_MIN ? { scrollHead: head, scrollBody: body } : { scrollHead: "", scrollBody: head + body }),
    scrollW: w,
  }
}
export function rowsOpen(items, checks, now, titleOf, cols = 80) {
  const l = shownList(items, checks) // waits first, then the FYI groups (bug · FYI, news · FYI)
  if (!l.length) return hint("nothing open: all caught up")
  return l.map((i) => { const t = relTime(i.updated, now); return itemRow(i, t, titleOf, cols, `${t} ago`) })
}
export function rowsHistory(items, checks, now, titleOf, cols = 80) {
  const l = historyList(items, checks, now)
  if (!l.length) return hint("no checked items in the last 14 days")
  return l.map((i) => {
    const c = checks[i.tag], t = relTime(c.at, now), by = c.by ? `${c.by} ` : ""
    return itemRow(i, `✓ ${by}${t}`, titleOf, cols, `checked ${c.by ? `by ${c.by} ` : ""}${t} ago`)
  })
}
export function rowsToasts(log, now, cols = 80) {
  if (!Array.isArray(log) || !log.length) return hint("no toasts logged yet")
  return log.map((t, n) => {
    const text = `${t.title ? `${t.title}: ` : ""}${t.message}`
    const footer = `${t.variant ?? "info"} · ${relTime(t.at, now)}`
    const category = t.source === "bus" ? "bus (server-published)" : "news-panel"
    const w = titleWidth(cols, footer)
    return { value: `toast:${n}`, title: clipTo(flat(text), w), truncateTitle: false, category, footer, detail: `${text}\n${category} · ${relTime(t.at, now)} ago`, scrollHead: "", scrollBody: flat(text), scrollW: w }
  })
}

export const promptRoute = (item) => ({ type: "session", sessionID: item.sessionID, prompt: { text: `[${item.tag}] `, files: [], agents: [], pasted: [] } })
