// news-panel server plugin: captures `📌 [tag] kind: text` markers from top-level sessions' assistant text and
// stores them in ~/.local/share/news/items.json, plus `📌 [tag] done` lines in dones.json (this file is the ONLY
// writer of both; the TUI writes checks.json, toasts.json and walk.json). Read-only toward OpenCode: it subscribes to session.text.ended, looks up the session (parentID,
// title) and never sends anything to a model or a session. Fails open: every error is logged, never thrown.
// Loaded automatically from plugins/news/index.js; tui.js next to it is the /news pop-up + sidebar/footer.
import { readFileSync, writeFileSync, renameSync, mkdirSync, appendFileSync, statSync, existsSync } from "node:fs"
import { join } from "node:path"
import { homedir } from "node:os"
import { createHash } from "node:crypto"
import { fileURLToPath } from "node:url"
import { parseMarkers, parseDone, pruneDones, applyMarkers, pruneItems, parseStore } from "./news-core.js"

export const DATA = process.env.NEWS_DATA || join(homedir(), ".local", "share", "news")
const TYPES = ["session.text.ended"]

export function makeLog(dir = DATA) {
  const file = join(dir, "server.log")
  return (what, obj) => {
    try {
      mkdirSync(dir, { recursive: true })
      try { if (statSync(file).size > 512e3) renameSync(file, file + ".1") } catch {}
      appendFileSync(file, `${new Date().toISOString()} ${what}${obj ? " " + JSON.stringify(obj) : ""}\n`)
    } catch {}
  }
}

const readText = (p) => { try { return readFileSync(p, "utf8") } catch { return null } }

// atomic JSON write: tmp file + rename (a reader never sees half a file). Windows: retry a busy rename.
export function writeJson(path, obj) {
  const tmp = `${path}.${process.pid}.${Math.random().toString(36).slice(2, 8)}.tmp`
  writeFileSync(tmp, JSON.stringify(obj, null, 1))
  for (let n = 0; ; n++) {
    try { renameSync(tmp, path); return } catch (e) {
      if (n >= 5 || !["EPERM", "EBUSY", "EACCES"].includes(e?.code)) { try { renameSync(tmp, tmp + ".dead") } catch {} throw e }
      const until = Date.now() + 20 * (n + 1); while (Date.now() < until) {}
    }
  }
}

// file-backed store for the server side
export function fileStore(dir = DATA) {
  const ITEMS = join(dir, "items.json"), CHECKS = join(dir, "checks.json"), DONES = join(dir, DONES_FILE)
  return {
    readItems() {
      const raw = readText(ITEMS)
      const r = parseStore(raw, "items")
      // a corrupt file (we are its only writer, writes are atomic) is moved aside, never silently overwritten
      if (!r.ok && raw !== null) { try { renameSync(ITEMS, `${ITEMS}.bad-${Date.now()}`) } catch {} }
      return r
    },
    readChecks: () => parseStore(readText(CHECKS), "checks"),
    writeItems(items) { mkdirSync(dir, { recursive: true }); writeJson(ITEMS, { v: 1, items }) },
    readDones: () => parseStore(readText(DONES), "dones"),
    writeDones(dones) { mkdirSync(dir, { recursive: true }); writeJson(DONES, { v: 1, dones }) },
  }
}
const DONES_FILE = "dones.json"

// the capture logic, deps injected: getSession(id) -> SessionInfo; read/write as fileStore. Events are handled
// one at a time (a queue), so two text parts ending together cannot lose each other's write.
export function createCapture({ getSession, readItems, readChecks, writeItems, readDones, writeDones, now = Date.now, log = () => {} }) {
  const seen = []
  const info = new Map() // sessionID -> { top, title, directory } (failures are not cached)
  let queue = Promise.resolve()

  const lookup = async (sid) => {
    if (info.has(sid)) return info.get(sid)
    let s
    try { s = await getSession(sid); s = s?.data ?? s } catch (e) { log("session-get-failed", { sid: String(sid).slice(0, 30), e: String(e?.message ?? e).slice(0, 120) }); return undefined }
    if (!s || typeof s !== "object") return undefined
    const v = { top: !s.parentID, title: s.title, directory: s.location?.directory ?? s.directory }
    info.set(sid, v)
    if (info.size > 500) info.delete(info.keys().next().value)
    return v
  }

  const handle = async (e) => {
    if (e?.type !== "session.text.ended") return
    const d = e.data ?? e.properties
    if (!d || typeof d.sessionID !== "string" || typeof d.text !== "string") return
    if (e.id) { if (seen.includes(e.id)) return; seen.push(e.id); if (seen.length > 300) seen.shift() }
    const markers = parseMarkers(d.text)
    const dones = readDones && writeDones ? parseDone(d.text) : []
    if (!markers.length && !dones.length) return
    const s = await lookup(d.sessionID)
    if (!s?.top) return // child (subagent) sessions never feed the panel
    const t = now()
    const messageID = d.assistantMessageID ?? d.messageID
    // done lines: recorded raw (the TUI decides against its walk record whether they check anything)
    if (dones.length) {
      const cur = readDones().value
      const has = new Set(cur.map((x) => `${x?.messageID}|${x?.tag}`))
      const add = dones.filter((x) => !has.has(`${messageID}|${x.tag}`))
        .map((x) => ({ tag: x.tag, sessionID: d.sessionID, messageID, role: "assistant", at: t }))
      if (add.length) {
        writeDones(pruneDones([...add, ...cur], t))
        log("done-lines", { sid: d.sessionID.slice(0, 30), tags: add.map((x) => x.tag) })
      }
    }
    if (!markers.length) return
    const cur = readItems()
    const r = applyMarkers(cur.value, markers, { sessionID: d.sessionID, messageID, sessionTitle: s.title, directory: s.directory }, t)
    if (!r.changed.length) return
    let checks = {}
    try { const c = readChecks(); if (c.ok) checks = c.value } catch {}
    writeItems(pruneItems(r.items, checks, t))
    log("captured", { sid: d.sessionID.slice(0, 30), tags: r.changed })
  }

  return {
    onEvent(e) {
      const p = queue.then(() => handle(e)).catch((err) => log("ERR capture", { e: String(err?.stack ?? err).slice(0, 400) }))
      queue = p
      return p
    },
  }
}

// ---------------------------------------------------------------- one capture per process
// OpenCode loads one instance per location and every instance's subscription sees every session (pacer
// measured this on 2.4.1). So ONE subscription per process lives on globalThis, ref-counted; when the owning
// instance unloads, the loop moves to another instance's client. The key carries a hash of this file.
const CORE_KEY = Symbol.for("opencode.news.core")
let BUILD = "dev"
try { BUILD = createHash("sha1").update(readFileSync(fileURLToPath(import.meta.url))).digest("hex").slice(0, 10) } catch {}

function createCore() {
  const log = makeLog()
  const ctxs = []
  const client = () => ctxs[ctxs.length - 1]
  const cap = createCapture({
    getSession: (sessionID) => { const c = client(); if (!c) throw new Error("no client"); return c.session.get({ sessionID }) },
    ...fileStore(),
    log,
  })
  let stopped = false, sub = null
  const subscribe = async () => {
    while (!stopped && !sub) {
      const c = client()
      if (!c) return
      const ac = new AbortController()
      sub = { c, ac }
      try {
        for await (const e of c.event.subscribe({ signal: ac.signal, types: TYPES })) void cap.onEvent(e)
      } catch (err) { if (!ac.signal.aborted) log("ERR subscribe", { e: String(err?.message ?? err).slice(0, 200) }) }
      sub = null
      if (!stopped) await new Promise((r) => { const t = setTimeout(r, ac.signal.aborted ? 50 : 1000); t.unref?.() })
    }
  }
  return {
    attach(ctx) {
      ctxs.push(ctx)
      if (!sub) subscribe().catch((e) => log("ERR subscribe", { e: String(e) }))
      log("setup", { loc: ctx?.location?.directory, refs: this.refs, build: BUILD, pid: process.pid, data: DATA })
    },
    detach(ctx) {
      const i = ctxs.indexOf(ctx)
      if (i >= 0) ctxs.splice(i, 1)
      if (sub?.c === ctx) sub.ac.abort()
    },
    stop() { stopped = true; try { sub?.ac.abort() } catch {} log("stop", { build: BUILD }) },
  }
}

function acquire() {
  const reg = (globalThis[CORE_KEY] ??= {})
  let core = reg[BUILD]
  if (!core) { core = reg[BUILD] = createCore(); core.refs = 0 }
  core.refs++
  let done = false
  return {
    core,
    release() {
      if (done) return
      done = true
      if (--core.refs > 0) return
      if (reg[BUILD] === core) delete reg[BUILD]
      try { core.stop() } catch {}
    },
  }
}

export default {
  id: "news",
  async setup(ctx) {
    let h
    try { h = acquire(); h.core.attach(ctx) } catch (e) { makeLog()("ERR setup", { e: String(e?.stack ?? e).slice(0, 400) }) }
    return () => { try { h?.core.detach(ctx); h?.release() } catch {} }
  },
}
