// node --test tests/pacer/   (pure logic of plugins/pacer/index.js: classifier, throttle, command parser, slots, global, pace)
import test from "node:test"
import assert from "node:assert/strict"
import {
  itemKind, isDeferrable, runStartTail, stripOwn, parseArgs, applyOp, emptyToggles, slotOf, cfgOf,
  decideBatch, flushText, globalBlocks, paceMessage, statusText, DEFER_MAX, USAGE, validName, uiParts, fromOf,
} from "../../plugins/pacer/index.js"
import plugin from "../../plugins/pacer/index.js"

const you = (t, id = "u" + t) => ({ id, role: "user", content: t })
const sub = (state, running, id = "s" + Math.random()) => ({
  id, role: "user", metadata: { source: "subagent", childID: "ses_c", state },
  content: `<background-result kind="subagent" sessionID="ses_c" state="${running ? "running" : state}" description="d">done${running ? "\n\nBackground work still running:\n- shell (npm test)" : ""}</background-result>`,
})
const shell = (id = "sh1") => ({ id, role: "user", metadata: { source: "shell" }, content: '<background-result kind="shell" id="x" state="completed">ok</background-result>' })
const pacerMsg = (kind, id = "p" + kind) => ({ id, role: "user", metadata: { pacer: { kind } }, content: `[pacer] ${kind}` })
const asst = { id: "a", role: "assistant", content: "hi" }
const tool = { id: "t", role: "tool", content: "result" }

// ---------------------------------------------------------------- classifier
test("classifier: typed prompt, subagent, deferrable subagent, shell, pacer", () => {
  assert.equal(itemKind(you("hello")), "you")
  assert.equal(itemKind(sub("completed", false)), "subagent")
  assert.equal(itemKind(sub("completed", true)), "subagent-wait")
  assert.equal(itemKind(shell()), "shell")
  assert.equal(itemKind(pacerMsg("flush")), "pacer")
  assert.equal(itemKind({ role: "user", metadata: { source: "other" }, content: "x" }), "other")
  // text-only detection (no metadata) still finds a subagent report
  assert.equal(itemKind({ role: "user", content: sub("completed", true).content }), "subagent-wait")
  // empty metadata object = typed
  assert.equal(itemKind({ role: "user", metadata: {}, content: "x" }), "you")
  // a typed prompt another plugin decorated (e.g. prompt-polish) is still yours
  assert.equal(itemKind({ role: "user", metadata: { promptPolish: { v: 1 } }, content: "x" }), "you")
  assert.equal(itemKind({ role: "assistant", metadata: { promptPolish: {} }, content: "x" }), "other")
})
test("classifier: deferrable needs state=running AND the still-running block", () => {
  assert.equal(isDeferrable(sub("completed", true)), true)
  assert.equal(isDeferrable(sub("completed", false)), false)
  assert.equal(isDeferrable({ role: "user", metadata: { source: "subagent" }, content: '<background-result kind="subagent" state="running">x</background-result>' }), false)
  assert.equal(isDeferrable(you('state="running" Background work still running')), false)
})
test("classifier: a running child that asks or reports a verdict is not deferred (a BLOCKED report was held 26 min)", () => {
  const body = (t) => ({ id: "s" + t.length, role: "user", metadata: { source: "subagent", state: "running" },
    content: `<background-result kind="subagent" sessionID="ses_c" state="running" description="d">${t}\n\nBackground work still running:\n- shell (Get-Content x | ? { $_ -match 'y' })</background-result>` })
  assert.equal(isDeferrable(body("The after snapshot is queued behind another run's test-stack lock. Once it finishes I'll diff it, then run the gate.")), true, "narration; a ? in the shell list doesn't count")
  assert.equal(isDeferrable(body("Step 2 is `BLOCKED`, the merge verdict is pending. Say if I should resolve it by hand.")), false)
  assert.equal(isDeferrable(body("Should I merge it now?")), false)
  assert.equal(isDeferrable(body("READY batch-7 4b17abb5; the gate still runs")), false)
  assert.equal(isDeferrable(body("DONE with step 1; step 2 runs")), false)
  assert.equal(isDeferrable(body("waiting; blocked by the lock, nothing to do yet")), true, "lower-case words are narration")
  assert.equal(itemKind(body("BLOCKED on x")), "subagent")
})
test("batcher: held child notes are counted (waits), so the footer can say they only ride along", () => {
  const cfg = { batch: true, period: 30, me: true }
  let r = decideBatch({}, cfg, [sub("completed", true, "w1")], 10 * 60e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.held.waits, 1)
  r = decideBatch(r.st, cfg, [you("hi")], 10 * 60e3 + 1000)
  assert.equal(r.action, "hold"); assert.equal(r.st.held.waits, 1); assert.equal(r.st.held.ids.length, 2)
  assert.equal(r.st.due, 10 * 60e3 + 1000 + 30e3, "a real item opens the 30 s window")
})
test("classifier: run start vs continuation", () => {
  assert.deepEqual(runStartTail([asst, you("a"), you("b")]).map((m) => m.id), ["ua", "ub"])
  assert.equal(runStartTail([you("a"), asst, tool]), null)          // tool result after assistant = continuation
  assert.equal(runStartTail([you("a"), asst]), null)               // nothing new
  assert.deepEqual(runStartTail([you("first")]).length, 1)          // first prompt of a session
  assert.equal(runStartTail([]), null)
})
test("strip: pacer status/notice leave the outgoing call, flush/note/wake stay", () => {
  const ms = [you("a"), pacerMsg("status"), asst, pacerMsg("notice"), pacerMsg("note"), pacerMsg("flush"), you("b")]
  assert.equal(stripOwn(ms), 2)
  assert.deepEqual(ms.map((m) => m.id), ["ua", "a", "pnote", "pflush", "ub"])
})

// ---------------------------------------------------------------- throttle (batcher)
const cfg = { batch: true, period: 60, me: true, from: "reply" } // the throttle tests below pin the reply mode
const arr = { batch: true, period: 60, me: true }                  // no `from` = arrival (the default)
// the hook's copy of OpenCode's auto-compaction continuation (seen live 2026-11-24 15:57 + 16:08: `user:keepID+recent`)
const compact = (id = "c1") => ({ id, role: "user", metadata: { keepID: "msg_fake0keep", recent: [] }, content: "" })
test("compaction: its continuation is my own turn going on, so it always passes (and held items ride along)", () => {
  assert.equal(itemKind(compact()), "compaction")
  for (const c of [arr, cfg, { ...arr, me: false }]) {
    const r = decideBatch({ lastRunStart: 1.8e12 - 1000 }, c, [compact()], 1.8e12)
    assert.equal(r.action, "pass", JSON.stringify(c)); assert.equal(r.why, "compaction continues the turn")
  }
  const held = { lastRunStart: 1000, held: { ids: ["s1"], since: 1500, kinds: { subagent: 1 }, opened: 1500 }, due: 61.5e3 }
  const r = decideBatch(held, arr, [sub("completed", false, "s1"), compact()], 2000)
  assert.equal(r.action, "pass"); assert.equal(r.st.held, null)
})
test("arrival (default): the first item opens a 60 s window, later ones join, the window never restarts", () => {
  const T = 1.8e12
  // a long quiet: reply mode would pass at once, arrival holds the first item for one period
  let r = decideBatch({ lastRunStart: T - 3600e3, lastRunEnd: T - 3000e3 }, arr, [you("a", "y1")], T)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, T + 60e3); assert.equal(r.st.held.opened, T)
  assert.match(r.why, /window 60s from the first arrival/)
  r = decideBatch(r.st, arr, [you("a", "y1"), sub("completed", false, "s1")], T + 20e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, T + 60e3, "a later arrival joins; the window does not restart")
  r = decideBatch(r.st, arr, [you("a", "y1"), sub("completed", false, "s1"), shell("sh1")], T + 59e3)
  assert.equal(r.st.due, T + 60e3); assert.deepEqual(r.st.held.kinds, { you: 1, subagent: 1, shell: 1 })
  // an arrival after the due (before the tick flushed) passes with everything held
  r = decideBatch(r.st, arr, [shell("sh2")], T + 60.5e3)
  assert.equal(r.action, "pass"); assert.equal(r.why, "window over"); assert.equal(r.st.held, null)
  // the next batch opens its own window at its first arrival
  r = decideBatch(r.st, arr, [shell("sh3")], T + 70e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, T + 130e3)
  assert.equal(fromOf(arr), "arrival"); assert.equal(fromOf(cfg), "reply"); assert.equal(fromOf({ from: "junk" }), "arrival")
})
test("arrival: children waiting on their shells keep the 30 min cap; the first real item opens the window", () => {
  let r = decideBatch({}, arr, [sub("completed", true, "w1")], 10 * 60e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, 10 * 60e3 + DEFER_MAX); assert.equal(r.st.held.opened, undefined)
  r = decideBatch(r.st, arr, [sub("completed", true, "w1"), shell("sh1")], 12 * 60e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, 12 * 60e3 + 60e3, "not the 30 min cap"); assert.equal(r.st.held.opened, 12 * 60e3)
  // a held batch from the reply mode keeps its earlier due when the mode flips
  r = decideBatch({ held: { ids: ["x"], since: 1000, kinds: { shell: 1 } }, due: 20e3 }, arr, [shell("x"), shell("y")], 5000)
  assert.equal(r.st.due, 20e3)
})
test("/now: a skip flag passes the next run start once; an expired one is dropped", () => {
  const st = { lastRunStart: 5000, held: { ids: ["s1"], since: 5500, kinds: { subagent: 1 } }, due: 66e3, skipUntil: 20e3 }
  let r = decideBatch(st, arr, [sub("completed", false, "s1"), you("now please", "y1")], 6000)
  assert.equal(r.action, "pass"); assert.equal(r.why, "/now"); assert.equal(r.st.held, null); assert.equal(r.st.skipUntil, null)
  r = decideBatch({ ...st, skipUntil: 5000 }, arr, [you("late", "y2")], 6000)
  assert.equal(r.action, "hold"); assert.equal(r.st.skipUntil, null, "expired: cleared, not kept")
  // a prompt marked /now passes too (pacer metadata kind "now")
  assert.equal(decideBatch({ lastRunStart: 5000 }, arr, [pacerMsg("now")], 6000).why, "/now")
})
test("throttle: first run passes, a second within the period is held until lastRunStart+period", () => {
  const T = 1.8e12 // epoch ms: a fresh session (lastRunStart 0) is always "elapsed"
  let r = decideBatch({}, cfg, [you("a")], T)
  assert.equal(r.action, "pass"); assert.equal(r.st.lastRunStart, T)
  r = decideBatch(r.st, cfg, [sub("completed", false, "s1")], T + 20000)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, T + 60000); assert.deepEqual(r.st.held.ids, ["s1"])
  assert.equal(r.st.held.since, T + 20000); assert.equal(r.st.held.kinds.subagent, 1)
})
test("throttle: held items are counted once by id (the same msg reappears in the next tail)", () => {
  let st = { lastRunStart: 1000 }
  let r = decideBatch(st, cfg, [sub("completed", false, "s1")], 2000)
  r = decideBatch(r.st, cfg, [sub("completed", false, "s1"), shell("sh1")], 3000)
  assert.equal(r.action, "hold"); assert.deepEqual(r.st.held.ids, ["s1", "sh1"])
  assert.deepEqual(r.st.held.kinds, { subagent: 1, shell: 1 }); assert.equal(r.st.held.since, 2000)
})
test("throttle: period elapsed -> pass and clear held", () => {
  const r = decideBatch({ lastRunStart: 0, held: { ids: ["x"], since: 1, kinds: {} }, due: 60000 }, cfg, [shell()], 60000)
  assert.equal(r.action, "pass"); assert.equal(r.st.held, null)
})
test("throttle: pacer flush/wake always passes", () => {
  for (const k of ["flush", "wake"]) {
    const r = decideBatch({ lastRunStart: 5000 }, cfg, [pacerMsg(k), you("x")], 6000)
    assert.equal(r.action, "pass"); assert.equal(r.why, "pacer flush")
  }
})
test("throttle: typed prompt passes at once with me off, waits with me on", () => {
  assert.equal(decideBatch({ lastRunStart: 5000 }, { ...cfg, me: false }, [you("x")], 6000).action, "pass")
  assert.equal(decideBatch({ lastRunStart: 5000 }, cfg, [you("x")], 6000).action, "hold")
})
test("throttle: only deferrable children -> hold even after the period, capped at first held + 30 min", () => {
  let r = decideBatch({ lastRunStart: 0 }, cfg, [sub("completed", true, "w1")], 10 * 60e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, 10 * 60e3 + DEFER_MAX)
  r = decideBatch(r.st, cfg, [sub("completed", true, "w1"), sub("completed", true, "w2")], 20 * 60e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, 10 * 60e3 + DEFER_MAX) // cap keeps the first held time
  // a real item among them -> normal rule (period long elapsed -> pass)
  r = decideBatch(r.st, cfg, [sub("completed", true, "w1"), shell("sh9")], 21 * 60e3)
  assert.equal(r.action, "pass")
})
test("throttle: the period counts from the later of the last run's start and its end", () => {
  // a 5-min turn: started at 0, ended at 300 s. A child's result at 310 s waits until 360 s, not 60 s
  let r = decideBatch({ lastRunStart: 0, lastRunEnd: 300e3 }, cfg, [sub("completed", false, "s1")], 310e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, 360e3)
  r = decideBatch(r.st, cfg, [sub("completed", false, "s1"), shell("sh2")], 330e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, 360e3, "a later item joins the same flush")
  assert.equal(decideBatch({ lastRunStart: 0, lastRunEnd: 300e3 }, cfg, [shell()], 360e3).action, "pass")
  // no end recorded yet (old state file): from the start, as before
  assert.equal(decideBatch({ lastRunStart: 0 }, cfg, [shell()], 30e3).st.due, 60e3)
})
test("throttle: a held batch's due never moves later", () => {
  const st = { lastRunStart: 0, lastRunEnd: 40e3, held: { ids: ["x"], since: 30e3, kinds: { shell: 1 } }, due: 60e3 }
  const r = decideBatch(st, cfg, [shell("x"), shell("y")], 50e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, 60e3)
  assert.deepEqual(r.st.held.ids, ["x", "y"])
})
test("throttle: custom period", () => {
  const r = decideBatch({ lastRunStart: 0 }, { ...cfg, period: 300 }, [shell()], 120e3)
  assert.equal(r.action, "hold"); assert.equal(r.st.due, 300e3)
})
test("flush text: counts by kind + pace line", () => {
  const t = flushText({ ids: ["a", "b", "c"], since: new Date(2026, 10, 24, 9, 5).getTime(), kinds: { subagent: 2, you: 1 } }, "[pacer main] pace", Date.now())
  assert.equal(t, "[pacer] batch: 3 held since 09:05 (2 subagent, 0 shell, 1 you)\n[pacer main] pace")
  assert.equal(flushText(null, "", 0), "")
})

// ---------------------------------------------------------------- command parser
test("parser: every /pacer form", () => {
  const P = parseArgs
  assert.deepEqual(P(""), { op: "status" }); assert.deepEqual(P("status"), { op: "status" }); assert.deepEqual(P("  STATUS "), { op: "status" })
  assert.deepEqual(P("on"), { op: "pace", on: true }); assert.deepEqual(P("on work"), { op: "pace", on: true, name: "work" })
  assert.deepEqual(P("off"), { op: "off" }); assert.deepEqual(P("off work"), { op: "off", name: "work" }); assert.deepEqual(P("off all"), { op: "offall" })
  assert.deepEqual(P("move"), { op: "move" }); assert.deepEqual(P("move alpha"), { op: "move", name: "alpha" })
  assert.deepEqual(P("week on"), { op: "week", on: true }); assert.deepEqual(P("global off"), { op: "global", on: false })
  assert.deepEqual(P("batch on"), { op: "batch", on: true }); assert.deepEqual(P("batch off w2"), { op: "batch", on: false, name: "w2" })
  assert.deepEqual(P("batch 120"), { op: "period", period: 120 }); assert.deepEqual(P("batch 15 x"), { op: "period", period: 15, name: "x" })
  assert.deepEqual(P("batch me off"), { op: "me", on: false }); assert.deepEqual(P("batch me on w"), { op: "me", on: true, name: "w" })
  assert.deepEqual(P("flush"), { op: "flush" }); assert.deepEqual(P("flush w2"), { op: "flush", name: "w2" })
  assert.deepEqual(P("flush a b"), { op: "usage" }); assert.equal(validName("flush"), false)
  assert.ok(USAGE.includes("flush [slot]"))
  assert.deepEqual(P("batch from arrival"), { op: "from", from: "arrival" }); assert.deepEqual(P("batch from Reply w2"), { op: "from", from: "reply", name: "w2" })
  assert.deepEqual(P("now"), { op: "now" }); assert.equal(validName("from"), false); assert.equal(validName("now"), false)
  assert.ok(USAGE.includes("batch from arrival|reply [slot]") && USAGE.includes("| now |"))
})
test("parser: junk -> usage", () => {
  for (const s of ["batch 14", "batch 601", "batch", "batch me", "week", "global maybe", "on a b", "on all", "on Me", "move on", "frobnicate", "batch 60 bad/name", "batch from", "batch from later", "now x"])
    assert.deepEqual(parseArgs(s), { op: "usage" }, s)
  assert.equal(validName("main"), true); assert.equal(validName("a".repeat(33)), false); assert.equal(validName("-x"), false)
  assert.match(applyOp(emptyToggles(), "s1", "T", { op: "usage" }).reply, /usage:/)
  assert.ok(USAGE.includes("move [slot]"))
})

// ---------------------------------------------------------------- slots (Addendum 1 + 2)
const A = "ses_A", B = "ses_B", C = "ses_C"
const on = (tg, sid, title, s) => applyOp(tg, sid, title, parseArgs(s))
test("slots: on defaults to main; a second session's bare on is refused with the hint", () => {
  let r = on(emptyToggles(), A, "Alpha coord", "on")
  assert.equal(r.changed, true); assert.equal(slotOf(r.tg, A), "main"); assert.equal(r.tg.slots.main.pace, true)
  const r2 = on(r.tg, B, "Other coord", "on")
  assert.equal(r2.changed, false)
  assert.equal(r2.reply, "[pacer] slot main is in Alpha coord: `/pacer move` takes it over, `/pacer on <name>` starts a second coordinator.")
  const r3 = on(r.tg, B, "Other coord", "batch on")
  assert.equal(r3.changed, false); assert.match(r3.reply, /slot main is in Alpha coord/)
})
test("slots: two slots run side by side, each with its own flags and period", () => {
  let tg = on(emptyToggles(), A, "Alpha", "on").tg
  tg = on(tg, B, "Proj2", "on work").tg
  tg = on(tg, B, "Proj2", "batch 120").tg      // no name -> this session's slot (work)
  tg = on(tg, A, "Alpha", "batch on").tg
  assert.deepEqual(Object.keys(tg.slots).sort(), ["main", "work"])
  assert.equal(cfgOf(tg, B).period, 120); assert.equal(cfgOf(tg, A).period, 60)
  assert.equal(cfgOf(tg, B).batch && cfgOf(tg, A).batch, true)
  // a session holding a slot can't grab a second one
  const r = on(tg, A, "Alpha", "on other"); assert.equal(r.changed, false); assert.match(r.reply, /already holds slot main/)
  // batch me needs an existing slot
  const r4 = on(emptyToggles(), C, "X", "batch me off"); assert.equal(r4.changed, false); assert.match(r4.reply, /batch on/)
  assert.equal(on(tg, B, "Proj2", "batch me off").tg.slots.work.me, false)
  // batch from: needs a slot too; sets the mode on this session's slot only
  const r5 = on(emptyToggles(), C, "X", "batch from reply"); assert.equal(r5.changed, false); assert.match(r5.reply, /batch on/)
  const r6 = on(tg, B, "Proj2", "batch from reply")
  assert.equal(r6.tg.slots.work.from, "reply"); assert.equal(r6.tg.slots.main.from, undefined); assert.match(r6.reply, /from reply: the 120s count from my last run/)
  assert.match(on(r6.tg, B, "Proj2", "batch from arrival").reply, /from arrival: the first held item opens a 120s window/)
})
test("slots: off [name], batch off, off all", () => {
  let tg = on(emptyToggles(), A, "Alpha", "on").tg
  tg = on(tg, B, "Proj2", "on work").tg
  let r = on(tg, A, "Alpha", "off"); assert.equal(r.tg.slots.main, undefined); assert.ok(r.tg.slots.work); assert.match(r.reply, /slot main off \(was in Alpha\)/)
  r = on(tg, A, "Alpha", "off work"); assert.equal(r.tg.slots.work, undefined); assert.ok(r.tg.slots.main)
  r = on(tg, C, "X", "off"); assert.equal(r.changed, false); assert.match(r.reply, /holds no slot/)
  r = on(tg, C, "X", "off nope"); assert.equal(r.changed, false)
  // batch off keeps a paced slot, releases a batch-only one
  let t2 = on(on(emptyToggles(), A, "Alpha", "batch on").tg, A, "Alpha", "batch off")
  assert.equal(t2.tg.slots.main, undefined); assert.match(t2.reply, /slot released/)
  t2 = on(on(tg, A, "Alpha", "batch on").tg, A, "Alpha", "batch off"); assert.equal(t2.tg.slots.main.pace, true)
  r = on({ ...tg, week: true, global: true }, C, "X", "off all")
  assert.deepEqual(r.tg, { week: false, global: false, slots: {} }); assert.match(r.reply, /2 slots cleared/)
})
test("slots: move — named, single, several, already here, holder busy", () => {
  let tg = on(emptyToggles(), A, "Alpha", "on").tg
  tg = on(tg, A, "Alpha", "batch 90").tg
  let r = on(tg, B, "New coord", "move")
  assert.equal(r.changed, true); assert.equal(r.reply, "[pacer] slot main moved from Alpha to New coord.")
  assert.equal(r.tg.slots.main.sid, B); assert.equal(r.tg.slots.main.period, 90); assert.equal(r.tg.slots.main.pace, true)
  assert.equal(slotOf(r.tg, A), null) // cleared there
  assert.match(on(tg, A, "Alpha", "move").reply, /already here/)
  assert.match(on(emptyToggles(), A, "Alpha", "move").reply, /nothing to move/)
  tg = on(tg, B, "Proj2", "on work").tg
  r = on(tg, C, "Third", "move")
  assert.equal(r.changed, false); assert.match(r.reply, /several slots: main \(Alpha\), work \(Proj2\)/)
  r = on(tg, C, "Third", "move work")
  assert.equal(r.tg.slots.work.sid, C); assert.equal(r.tg.slots.main.sid, A); assert.match(r.reply, /moved from Proj2 to Third/)
  r = on(tg, B, "Proj2", "move main"); assert.equal(r.changed, false); assert.match(r.reply, /already holds slot work/)
})
test("status: lists every slot with title, flags, last-run age", () => {
  let tg = on(emptyToggles(), A, "Alpha", "on").tg
  tg = on(tg, B, "Proj2", "batch 120 work").tg
  const now = Date.now()
  const t = statusText({ tg, sid: A, pace: null, counters: {}, rt: { [A]: { lastRun: now - 5 * 60e3 } }, now })
  assert.match(t, /- main: Alpha \(this\) \| pace \| last run 5 min ago/)
  assert.match(t, /- work: Proj2 \| batch 120s from arrival \| last run never/)
  assert.match(statusText({ tg: on(tg, B, "Proj2", "batch from reply").tg, sid: A, pace: null, counters: {}, rt: {}, now }), /- work: Proj2 \| batch 120s from reply \|/)
  assert.match(t, /this session: slot main/)
  assert.match(statusText({ tg: emptyToggles(), sid: C, pace: null, now }), /slots: none/)
  // held right now: count, since, when it flushes, how to flush now
  const held = { held: { ids: ["x", "y"], since: now - 10e3, kinds: {} }, due: now + 23e3, busy: false }
  const t2 = statusText({ tg, sid: A, pace: null, counters: { [A]: { held: 1 } }, rt: { [A]: held }, now })
  assert.match(t2, /held now: 2 since \d\d:\d\d, flush at \d\d:\d\d:\d\d \(in 23s\); `\/pacer flush` sends it now/)
  assert.match(statusText({ tg, sid: A, pace: null, counters: { [A]: {} }, rt: { [A]: { ...held, busy: true } }, now }), /flush after the running turn/)
  assert.doesNotMatch(statusText({ tg, sid: A, pace: null, counters: { [A]: {} }, rt: { [A]: { held: null } }, now }), /held now/)
})

// ---------------------------------------------------------------- global
test("global: only HOLD/LIMITED, never a paced slot or its descendants (any slot)", () => {
  let tg = on(emptyToggles(), A, "Alpha", "on").tg
  tg = on(tg, B, "Proj2", "on work").tg
  tg = { ...tg, global: true }
  assert.equal(globalBlocks(tg, "HOLD", C, []), true)
  assert.equal(globalBlocks(tg, "LIMITED", C, ["ses_x"]), true)
  assert.equal(globalBlocks(tg, "GO", C, []), false)
  assert.equal(globalBlocks(tg, "HOLD", A, []), false)
  assert.equal(globalBlocks(tg, "HOLD", "child", ["mid", B]), false) // grandchild of slot work
  assert.equal(globalBlocks({ ...tg, global: false }, "HOLD", C, []), false)
  const batchOnly = { global: true, slots: { main: { sid: A, batch: true } } }
  assert.equal(globalBlocks(batchOnly, "HOLD", "child", [A]), true) // batch-only slot is not a paced tree
})

// ---------------------------------------------------------------- pace messages
const P = (mode, p5, p7 = 20, week = false) => ({ ok: true, mode, lanes: { GO: 5, EASE: 3, SLOW: 2, HOLD: 0, BURST: 7, LIMITED: 0 }[mode], week, usage: { h5: { pct: p5 }, d7: { pct: p7 } }, line: `PACE 10:00 | 5h ${p5}% | mode ${mode}` })
test("pace: reset wakes, mode up wakes only when idle, lanes down is a note, LIMITED says nothing", () => {
  const now = 10e6, idle = { busy: false, idleSince: now - 20 * 60e3, lastSend: 0 }
  let m = paceMessage(P("LIMITED", 100), P("GO", 2), idle, now, "work")
  assert.equal(m.why, "RESET"); assert.equal(m.wake, true); assert.match(m.text, /^\[pacer work\] /)
  assert.equal((m.text.match(/5h 2%/g) ?? []).length, 1) // usage line once
  m = paceMessage(P("SLOW", 60), P("GO", 61), idle, now); assert.equal(m.wake, true); assert.match(m.text, /lanes<=5/)
  m = paceMessage(P("SLOW", 60), P("GO", 61), { ...idle, idleSince: now - 60e3 }, now); assert.equal(m.wake, false) // busy-ish: note
  m = paceMessage(P("GO", 60), P("SLOW", 61), idle, now); assert.equal(m.wake, false); assert.match(m.why, /GO->SLOW/)
  assert.equal(paceMessage(P("GO", 60), P("LIMITED", 100), idle, now), null)
  assert.equal(paceMessage(P("GO", 10), P("GO", 11), idle, now), null)
  // HOLD warm-only ping after 50 min idle and 50 min since the last send
  m = paceMessage(P("HOLD", 92), P("HOLD", 93), { busy: false, idleSince: now - 51 * 60e3, lastSend: now - 51 * 60e3 }, now)
  assert.equal(m.why, "HOLD"); assert.match(m.text, /warm-only/)
  // week reset only counts with week on
  assert.equal(paceMessage(P("GO", 10, 98), P("GO", 10, 2), idle, now), null)
  assert.equal(paceMessage(P("GO", 10, 98, true), P("GO", 10, 2, true), idle, now).why, "RESET")
})

test("ui parts: status/notice split per line (UI rows), note/flush one message with a joined description", () => {
  assert.deepEqual(uiParts("[pacer] status 10:00\ntoggles: week off\n\n- main: A", "status").map((p) => p.description),
    ["[pacer] status 10:00", "toggles: week off", "- main: A"])
  assert.deepEqual(uiParts("x", "notice"), [{ text: "x", description: "x" }])
  const f = uiParts("\n[pacer] batch: 1 held\n[pacer main] pace", "flush")
  assert.equal(f.length, 1); assert.equal(f[0].text, "\n[pacer] batch: 1 held\n[pacer main] pace")
  assert.equal(f[0].description, "[pacer] batch: 1 held | [pacer main] pace")
})

test("plugin: default export is { id: 'pacer', setup }", () => {
  assert.equal(plugin.id, "pacer"); assert.equal(typeof plugin.setup, "function")
})
