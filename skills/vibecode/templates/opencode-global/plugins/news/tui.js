// news-panel TUI plugin: /todo pop-up (open / history / toasts views), a toast per new item, toast log, and
// (panel.jsx) sidebar + clickable footer count + session panel. Reads items.json + dones.json (written only by
// index.js); writes checks.json, toasts.json and walk.json. Checking just writes a file. The ONE send: "walk me
// through" (ctrl+s or its row, open view only) puts one prompt into the session you're in, only on that press.
// "Open" = waits on you (counts, footer, walk); bugs + news are FYI rows under them.
// Keys in the pop-up: click / Enter = check (history: un-check), stays open, cursor to the next row;
// ctrl+o = open the item's session; ←/→ slide the selected row's text, home = back to the start.
// `📌 [tag] done` lines (dones.json): walked waits -> checked by:"walk"; FYI from its own session -> by:"ai".
// Fails open: setup never throws, every handler is wrapped, errors go to ~/.local/share/news/tui.log.
import { Plugin } from "@opencode/plugin/tui"
import { readFileSync, writeFileSync, renameSync, mkdirSync, appendFileSync, statSync } from "node:fs"
import { spawnSync } from "node:child_process"
import { join } from "node:path"
import { homedir } from "node:os"
import {
  parseStore, checkItem, uncheckItem, pruneChecks, pushToast, freshItems, isOpen,
  rowsOpen, rowsHistory, rowsToasts, footerText, sidebarLines, promptRoute, openList, fyiList,
  SLASH, WALK, walkPrompt, walkRow, walkTags, isLeftClick,
  scrollTitle, maxScroll, SCROLL_STEP, applyWalkDone, doneKey,
} from "./news-core.js"

const DEFAULT_DIR = process.env.NEWS_DATA || join(homedir(), ".local", "share", "news")
const VIEWS = ["open", "history", "toasts"]
const msg = (e) => (e instanceof Error ? e.message : String(e)).slice(0, 200)

export function sysClip(text) {
  const cmds = process.platform === "win32" ? [["clip.exe", []]]
    : process.platform === "darwin" ? [["pbcopy", []]]
    : [["wl-copy", []], ["xclip", ["-selection", "clipboard"]], ["xsel", ["--clipboard", "--input"]]]
  for (const [c, a] of cmds) {
    try { if (spawnSync(c, a, { input: text, timeout: 2000, windowsHide: true }).status === 0) return true } catch {}
  }
  return false
}

// opts: dir, now, interval (poll ms; 0 = none), ui (mount panel.jsx), clip(text) -> bool,
// createSignal (solid's; else panel.jsx's once loaded; none = ←/→ re-show the pop-up instead)
export function setupNews(context, opts = {}) {
  const dir = opts.dir ?? DEFAULT_DIR
  const now = opts.now ?? Date.now
  const clip = opts.clip ?? sysClip
  let panelSignal
  const signalFn = () => opts.createSignal ?? panelSignal
  const F = {
    items: join(dir, "items.json"), checks: join(dir, "checks.json"), toasts: join(dir, "toasts.json"), log: join(dir, "tui.log"),
    dones: join(dir, "dones.json"), walk: join(dir, "walk.json"),
  }
  const log = (what, obj) => {
    try {
      mkdirSync(dir, { recursive: true })
      try { if (statSync(F.log).size > 512e3) renameSync(F.log, F.log + ".1") } catch {}
      appendFileSync(F.log, `${new Date().toISOString()} ${what}${obj ? " " + JSON.stringify(obj) : ""}\n`)
    } catch {}
  }
  const text = (p) => { try { return readFileSync(p, "utf8") } catch { return null } }
  const mtime = (p) => { try { return statSync(p).mtimeMs } catch { return 0 } }
  const write = (p, obj) => {
    mkdirSync(dir, { recursive: true })
    const tmp = `${p}.${process.pid}.${Math.random().toString(36).slice(2, 8)}.tmp`
    writeFileSync(tmp, JSON.stringify(obj, null, 1))
    renameSync(tmp, p)
  }
  const readItems = () => parseStore(text(F.items), "items")
  const readChecks = () => parseStore(text(F.checks), "checks").value
  const readToasts = () => parseStore(text(F.toasts), "toasts").value
  // walk.json = { v:1, walk: { sid, tags, at } | null, seen: [doneKey...] } (done lines already handled)
  const readWalk = () => {
    let j
    try { j = JSON.parse(text(F.walk) ?? "null") } catch {}
    const ok = j && typeof j === "object"
    return { walk: ok && j.walk && typeof j.walk === "object" ? j.walk : null, seen: ok && Array.isArray(j.seen) ? j.seen.filter((k) => typeof k === "string") : [] }
  }

  // cached snapshot for the footer / sidebar (re-read only when a file changed)
  let snapKey = "", snapVal = { items: {}, checks: {} }
  const snapshot = () => {
    try {
      const k = `${mtime(F.items)}|${mtime(F.checks)}`
      if (k !== snapKey) { snapVal = { items: readItems().value, checks: readChecks() }; snapKey = k }
    } catch (e) { log("ERR snapshot", { e: msg(e) }) }
    return snapVal
  }

  const logToast = (t, source) => {
    try { write(F.toasts, { v: 1, toasts: pushToast(readToasts(), { ...t, source }, now()) }) } catch (e) { log("ERR toast-log", { e: msg(e) }) }
  }
  // our own toasts: shown and logged (the plugin cannot see the TUI's internal toasts, only bus + own)
  const toast = (o) => {
    try { context.ui.toast.show(o) } catch {}
    logToast(o, "self")
  }
  const titleOf = (sid) => { try { return context.data.session.get(sid)?.title } catch { return undefined } }
  // terminal columns as opentui sees them; unknown -> 80 (dialogWidth's default)
  const termCols = () => { try { const w = context.renderer?.width; return Number.isFinite(w) && w > 0 ? w : 80 } catch { return 80 } }
  const routeNow = () => { try { return context.ui.router.current() } catch { return undefined } }

  const setCheck = (tag, on, by) => {
    const it = readItems()
    const item = it.value[tag]
    let checks = readChecks()
    if (on) { if (!item) return false; checks = checkItem(checks, item, now(), by) } else checks = uncheckItem(checks, tag)
    write(F.checks, { v: 1, checks: pruneChecks(checks, it.ok ? it.value : undefined, now()) })
    log(on ? "check" : "uncheck", { tag, ...(by ? { by } : {}) })
    return true
  }

  const prefill = (item) => {
    const line = `[${item.tag}] `
    let copied = false
    try { copied = !!clip(line) } catch {}
    const r = routeNow()
    if (r?.type === "session" && r.sessionID === item.sessionID) {
      // the open session's prompt cannot be written by a plugin (no publish for tui.prompt.append)
      toast({ title: "todo", message: copied ? `"${line}" copied: paste with Ctrl+V` : `copy failed: type "${line}"`, variant: copied ? "info" : "warning" })
      return
    }
    // other session: jump there with the prompt prefilled (undocumented route field; replaces that draft)
    try { context.ui.router.navigate(promptRoute(item)) } catch (e) { log("ERR prefill-nav", { e: msg(e) }) }
    if (copied) toast({ title: "todo", message: `"${line}" in the prompt (also copied: Ctrl+V)`, variant: "info", duration: 3000 })
  }

  // walk me through: the only code path that touches context.client, reached only from a press in the pop-up
  async function walk() {
    const r = routeNow()
    const sid = r?.type === "session" ? r.sessionID : undefined
    const items = readItems().value, checks = readChecks()
    const body = walkPrompt(items, checks, sid, titleOf)
    if (!body) { toast({ title: "todo", message: "nothing open that waits on you: no walk-through to send", variant: "info" }); return }
    if (!sid) { toast({ title: "todo", message: "open a session first: walk me through sends into the session you're in", variant: "warning" }); return }
    const n = openList(items, checks).length
    // record the walk BEFORE sending: the reply's done lines may land before the prompt call returns
    const tags = walkTags(items, checks)
    try { write(F.walk, { v: 1, walk: { sid, tags, at: now() }, seen: readWalk().seen }) } catch (e) { log("ERR walk-record", { e: msg(e) }) }
    try {
      const res = await context.client.session.prompt({ sessionID: sid, text: body })
      if (res?.error) throw new Error(typeof res.error === "string" ? res.error : JSON.stringify(res.error).slice(0, 160))
      log("walk sent", { sid, n, tags, chars: body.length })
      toast({ title: "todo", message: `walk-through sent (${n} waits on you) to this session`, variant: "success", duration: 3000 })
    } catch (e) {
      let copied = false
      try { copied = !!clip(body) } catch {}
      log("ERR walk", { sid, e: msg(e), copied })
      toast({ title: "todo", message: `walk-through send failed: ${msg(e)}${copied ? "; whole text copied: paste with Ctrl+V" : ""}`, variant: "error" })
    }
  }

  let busySince = 0 // a pop-up of ours is up: a second footer click (double click) must not stack another
  async function open() {
    if (busySince && now() - busySince < 60e3) return
    busySince = now()
    let view = "open", current
    let scroll = { v: undefined, off: 0 } // fallback only (no live signal): the row slid on the last re-show
    try {
      while (true) {
        const t = now()
        const it = readItems(), checks = readChecks()
        const items = it.value
        let next
        const n = openList(items, checks).length, nFyi = fyiList(items, checks).length
        const cols = termCols()
        let rows = view === "open" ? rowsOpen(items, checks, t, titleOf, cols)
          : view === "history" ? rowsHistory(items, checks, t, titleOf, cols)
          : rowsToasts(readToasts(), t, cols)
        const itemVals = rows.map((r) => r.value).filter((v) => v && v !== WALK)
        // the row the cursor goes to after `v` leaves the list: the one below, else the one above
        const after = (v) => { const i = itemVals.indexOf(v); return i < 0 ? undefined : itemVals[i + 1] ?? itemVals[i - 1] }
        // the walk row sits on top (only when something waits on you), but the cursor starts on the first
        // item: a plain Enter never sends
        if (view === "open" && n) { if (current === undefined) current = rows[0]?.value; rows = [walkRow(n, cols), ...rows] }

        // inline scroll: with solid's createSignal each scrollable row gets a live titleView (no re-open);
        // without it, ←/→ close + re-show with that row slid and the cursor kept
        const sc = scroll
        scroll = { v: undefined, off: 0 }
        const mk = signalFn(), live = new Map()
        let liveAt = { v: undefined, off: 0 }
        for (const r of rows) {
          if (typeof r.scrollBody !== "string") continue
          if (mk) { try { const [get, set] = mk(r.title); r.titleView = get; live.set(r.value, { row: r, set }) } catch (e) { log("ERR signal", { e: msg(e) }) } }
          else if (sc.v === r.value && sc.off) r.title = scrollTitle(r, sc.off)
        }
        const clamp = (row, o) => Math.max(0, Math.min(maxScroll(row), o))
        const slide = (v, f) => {
          try {
            const ent = live.get(v)
            if (ent) {
              if (liveAt.v !== undefined && liveAt.v !== v) { const o = live.get(liveAt.v); o?.set(o.row.title); liveAt = { v: undefined, off: 0 } }
              const off = clamp(ent.row, f(liveAt.v === v ? liveAt.off : 0))
              liveAt = { v, off }
              ent.set(off ? scrollTitle(ent.row, off) : ent.row.title)
              return
            }
            if (live.size || mk) return // live mode, but this row doesn't scroll (walk row, hint)
            const row = rows.find((r) => r.value === v)
            if (!row || typeof row.scrollBody !== "string") return
            const off = clamp(row, f(sc.v === v ? sc.off : 0))
            if (off === (sc.v === v ? sc.off : 0)) return // already at that end: don't flicker
            next = { op: "scroll", v, off }; close()
          } catch (e) { log("ERR scroll", { e: msg(e) }) }
        }

        const title = view === "open" ? `Todo · open: ${n} waits on you${nFyi ? ` · ${nFyi} FYI` : ""}${it.ok || !text(F.items) ? "" : " · items.json unreadable"}`
          : view === "history" ? "Todo · history (checked, 14 days)" : "Todo · toast log (bus + news-panel)"
        const close = () => { try { context.ui.dialog.clear() } catch {} }
        const actions = [
          { bind: "ctrl+n", title: `view: ${VIEWS[(VIEWS.indexOf(view) + 1) % 3]}`, side: "right", selection: "none", onTrigger: () => { next = "view"; close() } },
        ]
        if (view !== "toasts") {
          actions.unshift(
            { bind: "ctrl+f", title: view === "open" ? "check (click/Enter)" : "uncheck (click/Enter)", onTrigger: (v) => { next = { op: "check", v }; close() } },
            { bind: "ctrl+o", title: "open session", onTrigger: (v) => { next = { op: "nav", v }; close() } },
            { bind: "ctrl+y", title: "[tag] → prompt", onTrigger: (v) => { next = { op: "prefill", v }; close() } },
          )
        }
        if (view === "open") actions.push({ bind: "ctrl+s", title: "walk me through", selection: "none", onTrigger: () => { next = "walk"; close() } })
        actions.push(
          { bind: "right", title: "→ scroll", onTrigger: (v) => slide(v, (o) => o + SCROLL_STEP) },
          { bind: "left", title: "← scroll", onTrigger: (v) => slide(v, (o) => o - SCROLL_STEP) },
          { bind: "home", title: "home: start", onTrigger: (v) => slide(v, () => 0) },
        )
        const shown = context.ui.dialog.select({ title, current, options: rows, actions })
        // select -> dialog.replace() resets the size to medium; widen it now (rows were cut for large)
        try { context.ui.dialog.set?.({ size: "large" }) } catch {}
        const picked = await shown
        if (next === "view") { view = VIEWS[(VIEWS.indexOf(view) + 1) % 3]; current = undefined; continue }
        if (next?.op === "scroll") { scroll = { v: next.v, off: next.off }; current = next.v; continue }
        if (next === "walk" || (view === "open" && picked === WALK)) { await walk(); return }
        // ctrl+f, or a click / Enter on an item row (both reach the select's onSelect): check it
        // (history: un-check), stay open, cursor to the next row
        const toggle = next?.op === "check" ? next.v : view !== "toasts" && !next && picked != null ? picked : undefined
        if (toggle !== undefined) {
          if (toggle && toggle !== WALK) {
            try { setCheck(toggle, view === "open") } catch (e) { toast({ title: "todo", message: `check failed: ${msg(e)}`, variant: "error" }) }
            current = after(toggle)
          } else current = undefined
          continue
        }
        if (next?.op === "nav") {
          const item = items[next.v]
          if (!item?.sessionID) { toast({ title: "todo", message: "no item selected", variant: "warning" }); continue }
          try { context.ui.router.navigate({ type: "session", sessionID: item.sessionID }) } catch (e) { log("ERR nav", { e: msg(e) }) }
          return
        }
        if (next?.op === "prefill") {
          const item = items[next.v]
          if (!item) { toast({ title: "todo", message: "no item selected", variant: "warning" }); continue }
          prefill(item)
          return
        }
        if (picked === undefined || picked === null) return // Escape
        if (view === "toasts") {
          const e = readToasts()[Number(String(picked).split(":")[1])]
          if (e) { try { await context.ui.dialog.alert({ title: e.title || "toast", message: e.message }) } catch {} }
          current = picked
          continue
        }
        return
      }
    } catch (e) {
      log("ERR open", { e: String(e?.stack ?? e).slice(0, 400) })
      try { context.ui.toast.show({ title: "todo", message: `/${SLASH}: ${msg(e)}`, variant: "error" }) } catch {}
    } finally { busySince = 0 }
  }

  // a left click on a footer count (panel.jsx) opens the pop-up; a drag-select of text is not a click
  const click = (ev, where = "footer") => {
    try {
      if (!isLeftClick(ev)) return
      try { if (context.renderer?.getSelection?.()?.getSelectedText?.()) return } catch {}
      log("click", { where, button: ev?.button, busy: !!busySince })
      void open()
    } catch (e) { log("ERR click", { e: msg(e) }) }
  }

  // dones.json (index.js) -> checks: walked waits by:"walk", FYI from its own session by:"ai". Every done line
  // handled (checked or ignored) goes into walk.json `seen`, so it's logged once and never re-applied, also
  // after a hot reload or after the user un-checks the item in history.
  let doneK = ""
  const doneKeyNow = () => `${mtime(F.dones)}|${mtime(F.walk)}`
  const tickDones = () => {
    try {
      const k = doneKeyNow()
      if (k === doneK) return
      doneK = k
      const all = parseStore(text(F.dones), "dones").value.filter((d) => d && typeof d === "object" && typeof d.tag === "string")
      if (!all.length) return
      const w = readWalk(), had = new Set(w.seen)
      const fresh = all.filter((d) => !had.has(doneKey(d)))
      if (!fresh.length) return
      const it = readItems()
      if (!it.ok) { doneK = ""; return } // items.json mid-write / unreadable: retry next tick
      const r = applyWalkDone(readChecks(), it.value, w.walk, fresh, now())
      if (r.checked.length || r.closed.length) write(F.checks, { v: 1, checks: pruneChecks(r.checks, it.value, now()) })
      // seen = keys still in dones.json (it is capped + pruned there, so this stays small)
      const seenNext = [...new Set([...w.seen.filter((x) => all.some((d) => doneKey(d) === x)), ...fresh.map(doneKey)])]
      write(F.walk, { v: 1, walk: w.walk, seen: seenNext })
      doneK = doneKeyNow()
      if (r.checked.length || r.closed.length) {
        log("walk-done", { tags: r.checked, closed: r.closed })
        const parts = [r.checked.length ? `walk: ${r.checked.length} checked` : "", r.closed.length ? `ai: ${r.closed.length} closed` : ""].filter(Boolean)
        toast({ title: "todo", message: parts.join(" · "), variant: "success", duration: 4000 })
      }
      for (const x of r.ignored) log("walk-done-ignored", x)
    } catch (e) { log("ERR tick-dones", { e: msg(e) }) }
  }

  // poll items.json: one toast per new / changed open item (first read = silent baseline)
  let seen, lastM = -1
  const tick = () => { tickItems(); tickDones() }
  const tickItems = () => {
    try {
      const m = mtime(F.items)
      if (m === lastM && seen) return
      lastM = m
      const it = readItems()
      if (!it.ok && seen) return // unreadable right now: keep the old baseline, try again
      const r = freshItems(seen, it.value, readChecks())
      seen = r.snap
      const fresh = r.fresh.slice(0, 3)
      for (const i of fresh) {
        toast({
          title: `📌 ${i.kind}`, message: `[${i.tag}] ${i.text}`, sessionID: i.sessionID, duration: 6000,
          variant: i.kind === "waits on you" ? "warning" : i.kind === "bug" ? "error" : "info",
        })
      }
      if (r.fresh.length > fresh.length) toast({ title: "📌 todo", message: `+${r.fresh.length - fresh.length} more: /${SLASH}`, variant: "info" })
    } catch (e) { log("ERR tick", { e: msg(e) }) }
  }

  const offs = []
  try {
    offs.push(context.data.on("tui.toast.show", (e) => {
      try { const d = e?.data ?? e?.properties ?? e; logToast({ title: d?.title, message: d?.message, variant: d?.variant }, "bus") } catch {}
    }))
  } catch (e) { log("ERR toast-listen", { e: msg(e) }) }

  try {
    context.keymap.layer(() => ({
      mode: "global",
      commands: [
        {
          id: "todo.open",
          title: "todo: open items (📌 markers)",
          description: "checklist of 📌 markers from coordinator replies; click/Enter/ctrl+f check, ctrl+o open session, ←/→ scroll, ctrl+n views, ctrl+y [tag] → prompt, ctrl+s walk me through",
          group: "Session",
          palette: true,
          slash: { name: SLASH },
          run: () => { void open() },
        },
        {
          id: "todo.panel",
          title: "todo: toggle side panel",
          description: "open items in the session side panel",
          group: "Session",
          palette: true,
          run: () => {
            try {
              if (context.ui.panel.current()?.name === "todo") context.ui.panel.close()
              else if (!context.ui.panel.open("todo")) toast({ title: "todo", message: "open a session first", variant: "warning" })
            } catch (e) { log("ERR panel", { e: msg(e) }) }
          },
        },
      ],
    }))
  } catch (e) { log("ERR keymap", { e: msg(e) }); try { context.ui.toast.show({ title: "todo", message: `/${SLASH} off: ${msg(e)}`, variant: "error" }) } catch {} }

  tick() // baseline
  log("setup", { pid: process.pid, dir })
  let timer
  if (opts.interval !== 0) { timer = setInterval(tick, opts.interval ?? 2000); timer.unref?.() }

  const views = {
    footer: () => { const s = snapshot(); return footerText(s.items, s.checks) },
    sidebar: (max = 5) => { const s = snapshot(); return sidebarLines(s.items, s.checks, max) },
    open: () => { const s = snapshot(); return openList(s.items, s.checks) },
    isOpen,
    click,
    // panel.jsx, once per slot after mount: proof the opentui node holds our mouse-up listener
    mounted: (slot, up) => log("footer-mounted", { slot, up }),
  }
  if (opts.ui !== false) {
    import("./panel.jsx")
      .then((m) => { if (typeof m.createSignal === "function") panelSignal = m.createSignal; offs.push(...m.mountPanel(context, views)) })
      .catch((e) => log("ERR panel-load", { e: msg(e) }))
  }

  return {
    open, tick, snapshot, views,
    dispose() {
      try { clearInterval(timer) } catch {}
      for (const f of offs) { try { if (typeof f === "function") f() } catch {} }
    },
  }
}

export default Plugin.define({
  id: "news",
  /** @param {import("@opencode/plugin/tui").Plugin.Context} context */
  setup(context) {
    let api
    try { api = setupNews(context) } catch (e) { try { context.ui.toast.show({ title: "todo", message: `/todo off: ${msg(e)}`, variant: "error" }) } catch {} }
    return () => { try { api?.dispose() } catch {} }
  },
})
