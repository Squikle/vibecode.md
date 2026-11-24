// node --test tests/pacer/   (one pacer per process: ref-counted core, hook dedupe, event failover)
import test from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync } from "node:fs"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { pathToFileURL } from "node:url"
import { acquireCore, makeOnce, hookKey } from "../../plugins/pacer/index.js"

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

test("acquireCore: one core per build, ref-counted, stop on the last release only", () => {
  const g = {}
  let made = 0, stopped = 0
  const make = () => { made++; return { stop: () => stopped++ } }
  const a = acquireCore(make, { build: "b1", g }), b = acquireCore(make, { build: "b1", g })
  assert.equal(made, 1); assert.equal(a.core, b.core); assert.equal(a.core.refs, 2)
  a.release(); a.release() // idempotent
  assert.equal(stopped, 0); assert.equal(b.core.refs, 1)
  b.release()
  assert.equal(stopped, 1)
  assert.deepEqual(Object.keys(g[Symbol.for("opencode.pacer.core")]), [])
  const c = acquireCore(make, { build: "b1", g })
  assert.equal(made, 2); assert.notEqual(c.core, a.core) // a fresh core after the last one stopped
  const d = acquireCore(make, { build: "b2", g })
  assert.equal(made, 3); assert.notEqual(d.core, c.core) // an edited file = its own core
  c.release(); d.release(); assert.equal(stopped, 3)
})

test("makeOnce: same key runs once (all callers wait for it), new key or expired key runs again", async () => {
  const once = makeOnce(1000)
  let n = 0
  const fn = async () => { await sleep(20); n++ }
  const a = once("s|m1", fn, 0), b = once("s|m1", fn, 10)
  assert.equal(a.first, true); assert.equal(b.first, false)
  await b.done
  assert.equal(n, 1) // b's wait covered a's work
  await once("s|m2", fn, 20).done; assert.equal(n, 2)
  await once("s|m1", fn, 5000).done; assert.equal(n, 3) // expired
  const bad = once("s|boom", () => { throw new Error("x") }, 6000)
  await bad.done // a throwing decision doesn't reject the waiters
})

test("hookKey: session + last message id, text fallback without ids", () => {
  assert.equal(hookKey({ sessionID: "A", messages: [{ id: "m1" }, { id: "m2" }] }), "A|m2")
  assert.equal(hookKey({ sessionID: "A", messages: [{ role: "user", content: "hi" }] }), "A|n1:hi")
  assert.notEqual(hookKey({ sessionID: "A", messages: [{ id: "m2" }] }), hookKey({ sessionID: "B", messages: [{ id: "m2" }] }))
})

function fakeCtx(loc) {
  const c = { calls: { interrupt: 0, synthetic: [], subscribe: 0, aborted: 0 }, hooks: [], cmds: [], disposed: 0, location: { directory: loc } }
  c.session = {
    hook: async (_n, fn) => { c.hooks.push(fn); return { dispose() { c.disposed++ } } },
    interrupt: async () => { c.calls.interrupt++; return { interrupted: true } },
    synthetic: async (p) => { c.calls.synthetic.push(p) },
    get: async ({ sessionID }) => ({ data: { id: sessionID, title: "T " + sessionID } }),
  }
  c.command = { transform: async (fn) => { fn({ add: (x) => c.cmds.push(x) }); return { dispose() { c.disposed++ } } } }
  c.event = {
    subscribe: ({ signal }) => {
      c.calls.subscribe++
      return (async function* () { await new Promise((r) => signal.addEventListener("abort", () => { c.calls.aborted++; r() })) })()
    },
  }
  return c
}

// a ctx with the RPC API and an event stream the test feeds (push(e))
function rpcCtx(loc) {
  const c = fakeCtx(loc)
  c.rpcs = []
  c.rpc = { register: async (def, impl) => { c.rpcs.push({ def, impl }); return { dispose() { c.disposed++ } } } }
  const queue = [], waiters = []
  c.push = (e) => { const w = waiters.shift(); if (w) w(e); else queue.push(e) }
  c.event.subscribe = ({ signal }) => {
    c.calls.subscribe++
    return (async function* () {
      while (!signal.aborted) {
        const e = queue.length ? queue.shift() : await new Promise((r) => { waiters.push(r); signal.addEventListener("abort", () => r(null)) })
        if (e) yield e
      }
      c.calls.aborted++
    })()
  }
  return c
}

test("RPC: the server registers `pacer` and no /pacer command; the period counts from the run's end; flush", async () => {
  const dir = mkdtempSync(join(tmpdir(), "pacer-rpc-"))
  process.env.PACER_DATA = dir
  writeFileSync(join(dir, "toggles.json"), JSON.stringify({ week: false, global: false, slots: { main: { sid: "ses_A", title: "A", pace: false, batch: true, period: 60, me: true } } }))
  try {
    const url = pathToFileURL(join(import.meta.dirname, "..", "..", "plugins", "pacer", "index.js")).href
    const m = (await import(url + "?copy=rpc")).default
    const c = rpcCtx("w1")
    const dispose = await m.setup(c)
    assert.equal(c.cmds.length, 0, "no server /pacer: the TUI's is the only one")
    assert.equal(c.rpcs.length, 1); assert.equal(c.rpcs[0].def.id, "pacer")
    assert.deepEqual(c.rpcs[0].def.events, {}, "register walks `events`: it must exist (2.4.1 threw without it)")
    assert.ok(Object.values(c.rpcs[0].def.methods).every((m) => m.errors), "and each method's `errors`")
    const call = (text) => c.rpcs[0].impl.command({ sessionID: "ses_A", text })
    const state = () => JSON.parse(readFileSync(join(dir, "plugin-state.json"), "utf8")).rt.ses_A
    const ev = (type) => ({ id: type + Math.random(), type, data: { sessionID: "ses_A" } })
    const asst = { id: "a1", role: "assistant", content: "ok" }

    // run 1 passes (period long gone), then runs for a while and ends
    c.push(ev("session.execution.started"))
    await sleep(20)
    await c.hooks[0]({ sessionID: "ses_A", messages: [{ id: "u1", role: "user", content: "a" }] })
    assert.equal(c.calls.interrupt, 0)
    c.push(ev("session.execution.succeeded"))
    await sleep(20)
    const end = Date.now()
    // a child's result right after the end: held, due = end + 60 s (not run start + 60 s)
    c.push(ev("session.execution.started"))
    await sleep(20)
    await c.hooks[0]({ sessionID: "ses_A", messages: [{ id: "u1", role: "user", content: "a" }, asst, { id: "u2", role: "user", content: "b" }] })
    assert.equal(c.calls.interrupt, 1, "held: within 60 s of the run's END")
    c.push(ev("session.execution.interrupted")) // the hold's own interrupt: the run end stays put
    await sleep(1200) // one tick saves the state
    let s = state()
    assert.ok(Math.abs(s.lastRunEnd - end) < 100, "lastRunEnd = the passed run's end")
    assert.deepEqual(s.held.ids, ["u2"]); assert.ok(Math.abs(s.due - (s.lastRunEnd + 60e3)) < 5, "due = end + period")

    // RPC replies: a change comes back as text and is NOT echoed; status goes into the transcript
    const before = c.calls.synthetic.length
    const r1 = await call("batch me off")
    assert.match(r1.reply, /me/); assert.equal(c.calls.synthetic.length, before, "no echo for a change")
    const r2 = await call("status")
    assert.match(r2.reply, /held now: 1 since \d\d:\d\d, flush at \d\d:\d\d:\d\d \(in \d+s\)/)
    assert.ok(c.calls.synthetic.length > before, "status echoed into the transcript")
    assert.ok(c.calls.synthetic.every((p) => p.metadata?.pacer?.kind === "status"))

    // flush now: the next tick (<= 1 s) sends one flush that resumes the run
    assert.match((await call("flush")).reply, /flushing 1 held now/)
    await sleep(1300)
    const flush = c.calls.synthetic.filter((p) => p.metadata?.pacer?.kind === "flush")
    assert.equal(flush.length, 1); assert.equal(flush[0].resume, true)
    assert.equal(state().held, null)
    assert.match((await call("flush")).reply, /nothing held/)
    dispose()
    assert.equal(c.disposed, 2, "hook + rpc disposed")
  } finally { delete process.env.PACER_DATA; if (existsSync(dir)) rmSync(dir, { recursive: true, force: true }) }
})

test("restart: a batch held when OpenCode stopped flushes after it starts again, even if a turn was running", async () => {
  const dir = mkdtempSync(join(tmpdir(), "pacer-restart-"))
  process.env.PACER_DATA = dir
  writeFileSync(join(dir, "toggles.json"), JSON.stringify({ week: false, global: false, slots: { main: { sid: "ses_A", title: "A", pace: false, batch: true, period: 60, me: true } } }))
  // saved by another process (pid 1) mid-turn: busy, one item held, already due
  const now = Date.now()
  writeFileSync(join(dir, "plugin-state.json"), JSON.stringify({ pid: 1, rt: { ses_A: { lastRunStart: now - 90e3, lastRunEnd: now - 90e3, held: { ids: ["u2"], since: now - 80e3, kinds: { child: 1 } }, due: now - 20e3, busy: true, ran: true } } }))
  try {
    const url = pathToFileURL(join(import.meta.dirname, "..", "..", "plugins", "pacer", "index.js")).href
    const m = (await import(url + "?copy=restart")).default
    const c = rpcCtx("w1")
    const dispose = await m.setup(c)
    await sleep(1300)
    const flush = c.calls.synthetic.filter((p) => p.metadata?.pacer?.kind === "flush")
    assert.equal(flush.length, 1, "the stale busy from the old process does not block the flush")
    const s = JSON.parse(readFileSync(join(dir, "plugin-state.json"), "utf8"))
    assert.equal(s.pid, process.pid); assert.equal(s.rt.ses_A.held, null); assert.equal(s.rt.ses_A.busy, false)
    dispose()
  } finally { delete process.env.PACER_DATA; if (existsSync(dir)) rmSync(dir, { recursive: true, force: true }) }
})

test("standby: while an older build's core is live, the new one holds nothing; it takes over with the state on disk", async () => {
  const dir = mkdtempSync(join(tmpdir(), "pacer-standby-"))
  process.env.PACER_DATA = dir
  writeFileSync(join(dir, "toggles.json"), JSON.stringify({ week: false, global: false, slots: { main: { sid: "ses_A", title: "A", pace: false, batch: true, period: 60, me: true } } }))
  const reg = (globalThis[Symbol.for("opencode.pacer.core")] ??= {})
  reg.oldbuild = { refs: 3, build: "oldbuild" } // a pre-standby build: no `born`
  try {
    const url = pathToFileURL(join(import.meta.dirname, "..", "..", "plugins", "pacer", "index.js")).href
    const m = (await import(url + "?copy=standby")).default
    const c = rpcCtx("w1")
    const dispose = await m.setup(c)
    const call = (text) => c.rpcs[0].impl.command({ sessionID: "ses_A", text })
    const msgs = [{ id: "u1", role: "user", content: "a" }, { id: "a1", role: "assistant", content: "ok" }, { id: "u2", role: "user", content: "b" }]
    // the older core's state on disk: ses_A just ran, one item held
    const now = Date.now()
    writeFileSync(join(dir, "plugin-state.json"), JSON.stringify({ rt: { ses_A: { lastRunStart: now, lastRunEnd: now, held: { ids: ["x"], since: now, kinds: { shell: 1 } }, due: now + 60e3, busy: false } } }))
    await c.hooks[0]({ sessionID: "ses_A", messages: msgs })
    assert.equal(c.calls.interrupt, 0, "standby: the older core decides")
    assert.match((await call("status")).reply, /held now: 1/, "status reads the older core's state from disk")
    assert.match((await call("flush")).reply, /flush waits/)
    await sleep(1200)
    assert.equal(c.calls.synthetic.filter((p) => p.metadata?.pacer?.kind === "flush").length, 0, "no flush while standby")
    assert.match(readFileSync(join(dir, "plugin.log"), "utf8"), / standby /)
    // the older build unloads: the next tick takes over with the disk state, and flush works
    delete reg.oldbuild
    await sleep(1200)
    assert.match(readFileSync(join(dir, "plugin.log"), "utf8"), / active /)
    assert.match((await call("flush")).reply, /flushing 1 held now/)
    await sleep(1300)
    assert.equal(c.calls.synthetic.filter((p) => p.metadata?.pacer?.kind === "flush").length, 1)
    dispose()
  } finally { delete reg.oldbuild; delete process.env.PACER_DATA; if (existsSync(dir)) rmSync(dir, { recursive: true, force: true }) }
})

test("two fresh module copies (like OpenCode's per-location load) share ONE core: one hold per model call", async () => {
  const dir = mkdtempSync(join(tmpdir(), "pacer-core-"))
  process.env.PACER_DATA = dir
  writeFileSync(join(dir, "toggles.json"), JSON.stringify({ week: false, global: false, slots: { main: { sid: "ses_A", title: "A", pace: false, batch: true, period: 60, me: true } } }))
  try {
    const url = pathToFileURL(join(import.meta.dirname, "..", "..", "plugins", "pacer", "index.js")).href
    const m1 = (await import(url + "?copy=1")).default, m2 = (await import(url + "?copy=2")).default
    const reg = globalThis[Symbol.for("opencode.pacer.core")] ?? {}
    const before = Object.keys(reg).length
    const c1 = fakeCtx("w1"), c2 = fakeCtx("w2")
    const d1 = await m1.setup(c1), d2 = await m2.setup(c2)
    const cores = Object.values(globalThis[Symbol.for("opencode.pacer.core")])
    assert.equal(cores.length - before, 1, "one core for both copies")
    assert.equal(cores.at(-1).refs, 2)
    assert.equal(c1.calls.subscribe + c2.calls.subscribe, 1, "one event subscription per process")
    assert.equal(c1.hooks.length, 1); assert.equal(c2.hooks.length, 1); assert.equal(c1.cmds.length + c2.cmds.length, 2)

    const asst = { id: "a1", role: "assistant", content: "ok" }
    const ev = (msgs) => ({ sessionID: "ses_A", messages: msgs })
    const clone = (e) => JSON.parse(JSON.stringify(e))
    // run 1: period elapsed -> pass, no hold
    const e1 = ev([{ id: "u1", role: "user", content: "a" }])
    await Promise.all([c1.hooks[0](e1), c2.hooks[0](clone(e1))])
    assert.equal(c1.calls.interrupt + c2.calls.interrupt, 0)
    // run 2 inside the period: BOTH hooks fire for the same call -> exactly one hold
    const e2 = ev([{ id: "u1", role: "user", content: "a" }, asst, { id: "u2", role: "user", content: "b" }])
    await Promise.all([c1.hooks[0](e2), c2.hooks[0](clone(e2)), c2.hooks[0](clone(e2))])
    assert.equal(c1.calls.interrupt + c2.calls.interrupt, 1, "one hold for one model call")
    // a different call (new last message) is decided again
    const e3 = ev([...e2.messages, { id: "u3", role: "user", content: "c" }])
    await Promise.all([c2.hooks[0](e3), c1.hooks[0](clone(e3))])
    assert.equal(c1.calls.interrupt + c2.calls.interrupt, 2)
    // status from the second copy: sent once (one synthetic per line, via the first live client)
    await c2.cmds[0].execute({ sessionID: "ses_A", prompt: { text: "status" } })
    const status = [...c1.calls.synthetic, ...c2.calls.synthetic].filter((p) => p.metadata?.pacer?.kind === "status")
    assert.ok(status.length >= 4 && status[0].text.startsWith("[pacer] status"), "status sent")
    assert.equal(status.filter((p) => p.text.startsWith("[pacer] status")).length, 1, "status sent once")

    // owner unloads: the subscription moves to the other instance, the core lives on
    const owner = c1.calls.subscribe ? c1 : c2, other = owner === c1 ? c2 : c1
    ;(owner === c1 ? d1 : d2)()
    await sleep(150)
    assert.equal(owner.calls.aborted, 1); assert.equal(other.calls.subscribe, 1, "failover to the other client")
    assert.equal(cores.at(-1).refs, 1)
    ;(owner === c1 ? d2 : d1)()
    assert.equal(Object.keys(globalThis[Symbol.for("opencode.pacer.core")]).length, before, "last unload stops + drops the core")
    const st = JSON.parse(readFileSync(join(dir, "plugin-state.json"), "utf8"))
    assert.deepEqual(st.rt.ses_A.held.ids, ["u2", "u3"], "one coherent runtime state: both held items")
    assert.equal(st.counters.ses_A.held, 2); assert.equal(st.counters.ses_A.passed, 1)
    assert.equal(c1.disposed + c2.disposed, 4)
  } finally { delete process.env.PACER_DATA; if (existsSync(dir)) rmSync(dir, { recursive: true, force: true }) }
})
