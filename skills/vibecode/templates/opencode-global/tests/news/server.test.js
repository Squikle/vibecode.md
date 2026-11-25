import { test } from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createCapture, fileStore } from "../../plugins/news/index.js"

const ev = (over = {}) => ({
  id: over.id ?? `evt_${Math.random()}`,
  type: "session.text.ended",
  data: { sessionID: "ses_top", assistantMessageID: "msg_1", ordinal: 0, text: "📌 [deploy] waits on you: approve", ...over.data },
})

function memStore() {
  const s = { items: {}, writes: 0 }
  return {
    s,
    readItems: () => ({ ok: true, value: s.items }),
    readChecks: () => ({ ok: true, value: {} }),
    writeItems: (v) => { s.items = v; s.writes++ },
  }
}

const sessions = {
  ses_top: { id: "ses_top", title: "Coordinator", location: { directory: "C:/p" } },
  ses_child: { id: "ses_child", parentID: "ses_top", title: "builder" },
}
const getSession = async (id) => { if (!sessions[id]) throw new Error("404"); return sessions[id] }

test("top-level only: a child session's marker is ignored", async () => {
  const st = memStore()
  const cap = createCapture({ getSession, ...st, now: () => 1000 })
  await cap.onEvent(ev({ data: { sessionID: "ses_child" } }))
  assert.equal(st.s.writes, 0)
  await cap.onEvent(ev())
  assert.equal(st.s.writes, 1)
  const it = st.s.items.deploy
  assert.equal(it.sessionID, "ses_top")
  assert.equal(it.messageID, "msg_1")
  assert.equal(it.sessionTitle, "Coordinator")
  assert.equal(it.directory, "C:/p")
})

test("top-level lookup is cached; an unknown session (get fails) is skipped, not captured", async () => {
  const st = memStore()
  let calls = 0
  const cap = createCapture({ getSession: async (id) => { calls++; return getSession(id) }, ...st, now: () => 1 })
  await cap.onEvent(ev({ data: { text: "📌 [a] news: 1" } }))
  await cap.onEvent(ev({ data: { text: "📌 [b] news: 2" } }))
  assert.equal(calls, 1)
  await cap.onEvent(ev({ data: { sessionID: "ses_gone", text: "📌 [c] news: 3" } }))
  assert.equal(st.s.items.c, undefined)
})

test("no marker / other event type / duplicate event id -> no write", async () => {
  const st = memStore()
  const cap = createCapture({ getSession, ...st, now: () => 1 })
  await cap.onEvent(ev({ data: { text: "just prose" } }))
  await cap.onEvent({ type: "session.idle", data: { sessionID: "ses_top" } })
  const e = ev({ id: "evt_same" })
  await cap.onEvent(e)
  await cap.onEvent(e)
  assert.equal(st.s.writes, 1)
})

test("fail-open: throwing deps never throw out of onEvent", async () => {
  const boom = () => { throw new Error("boom") }
  const logs = []
  const cap = createCapture({ getSession: boom, readItems: boom, readChecks: boom, writeItems: boom, now: () => 1, log: (w) => logs.push(w) })
  await cap.onEvent(ev())
  await cap.onEvent(null)
  await cap.onEvent({ type: "session.text.ended" })
  const cap2 = createCapture({ getSession, readItems: () => ({ ok: true, value: {} }), readChecks: () => ({ ok: true, value: {} }), writeItems: boom, now: () => 1, log: (w) => logs.push(w) })
  await cap2.onEvent(ev())
  assert.ok(logs.some((l) => /ERR/.test(l)))
})

test("unreadable (corrupt) items file: not overwritten silently; it is moved aside first", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-"))
  writeFileSync(join(dir, "items.json"), "{corrupt")
  const fs = fileStore(dir)
  const cap = createCapture({ getSession, ...fs, now: () => 5 })
  await cap.onEvent(ev())
  const j = JSON.parse(readFileSync(join(dir, "items.json"), "utf8"))
  assert.ok(j.items.deploy)
  assert.ok(readdirSync(dir).some((f) => f.startsWith("items.json.bad-")))
})

test("fileStore: storage file appears, atomic write leaves no tmp file; prune applied on write", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-"))
  const fs = fileStore(join(dir, "sub"))
  const DAY = 864e5
  // an item checked 20 days ago (still same text) is pruned on the next write
  fs.writeItems({ old: { tag: "old", kind: "news", text: "o", created: 0, updated: 0 } })
  writeFileSync(join(dir, "sub", "checks.json"), JSON.stringify({ v: 1, checks: { old: { at: 0, kind: "news", text: "o" } } }))
  const cap = createCapture({ getSession, ...fs, now: () => 20 * DAY })
  await cap.onEvent(ev())
  const j = JSON.parse(readFileSync(join(dir, "sub", "items.json"), "utf8"))
  assert.deepEqual(Object.keys(j.items), ["deploy"])
  assert.equal(j.v, 1)
  assert.ok(!readdirSync(join(dir, "sub")).some((f) => f.endsWith(".tmp")))
  assert.ok(existsSync(join(dir, "sub", "items.json")))
})

// ---------------------------------------------------------------- done lines (todo-click-check)
function memDones() {
  const st = memStore()
  st.s.dones = []
  st.readDones = () => ({ ok: true, value: st.s.dones })
  st.writeDones = (v) => { st.s.dones = v; st.s.doneWrites = (st.s.doneWrites ?? 0) + 1 }
  return st
}

test("done lines: `📌 [tag] done` in a top-level reply land in dones (role assistant, session, message, time)", async () => {
  const st = memDones()
  const cap = createCapture({ getSession, ...st, now: () => 777 })
  await cap.onEvent(ev({ data: { assistantMessageID: "msg_7", text: "ok\n📌 [deploy] done\n- 📌 [x] done: you said so" } }))
  assert.deepEqual(st.s.dones, [
    { tag: "deploy", sessionID: "ses_top", messageID: "msg_7", role: "assistant", at: 777 },
    { tag: "x", sessionID: "ses_top", messageID: "msg_7", role: "assistant", at: 777 },
  ])
  assert.equal(st.s.writes, 0) // no item marker: items.json untouched
  // same message again (a re-sent text part): no duplicate
  await cap.onEvent(ev({ data: { assistantMessageID: "msg_7", text: "📌 [deploy] done" } }))
  assert.equal(st.s.dones.length, 2)
  // a child session's done line is ignored
  await cap.onEvent(ev({ data: { sessionID: "ses_child", text: "📌 [y] done" } }))
  assert.ok(!st.s.dones.some((d) => d.tag === "y"))
  // markers + done lines in one reply: both handled
  await cap.onEvent(ev({ data: { assistantMessageID: "msg_8", text: "📌 [n] news: new\n📌 [z] done" } }))
  assert.ok(st.s.items.n)
  assert.equal(st.s.dones[0].tag, "z")
})

test("done lines: store without dones support (older deps) still captures markers; fileStore writes dones.json", async () => {
  const st = memStore()
  const cap = createCapture({ getSession, ...st, now: () => 1 })
  await cap.onEvent(ev({ data: { text: "📌 [a] news: 1\n📌 [b] done" } }))
  assert.ok(st.s.items.a)
  const dir = mkdtempSync(join(tmpdir(), "news-"))
  const fs = fileStore(dir)
  const cap2 = createCapture({ getSession, ...fs, now: () => 5 })
  await cap2.onEvent(ev({ data: { text: "📌 [q] done" } }))
  const j = JSON.parse(readFileSync(join(dir, "dones.json"), "utf8"))
  assert.equal(j.v, 1)
  assert.equal(j.dones[0].tag, "q")
  assert.ok(!existsSync(join(dir, "items.json")))
})