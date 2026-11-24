// node --test tests/pacer/   (pure parts of the TUI: status -> rows, row -> /pacer text, TTL warning, slash routing)
import test from "node:test"
import assert from "node:assert/strict"
import { parseStatus, headline, rows, rowAction, newSlotCommand, slashRoute, ttlWarning, TTL_WARN, PERIODS, ago, flagsOf, heldHere, footerText, replyToast } from "../../plugins/pacer/tui-core.js"
import { parseArgs, applyOp, emptyToggles } from "../../plugins/pacer/index.js"

const NOW = 1_800_000_000_000
const tg = { week: true, global: false, slots: {
  main: { sid: "ses_A", title: "coord A", pace: true, batch: true, period: 300, me: false },
  work: { sid: "ses_B", title: "coord B", pace: true, batch: false, period: 60, me: true },
} }
const st = { pace: { ok: true, atMs: NOW - 120e3, mode: "EASE", lanes: 3, usage: { h5: { pct: 41.6 }, d7: { pct: 22 } } }, rt: { ses_B: { lastRun: NOW - 5 * 60e3 } } }
const S = (o = {}) => ({ ...parseStatus(true, JSON.stringify(tg), JSON.stringify(st)), sid: "ses_A", now: NOW, ...o })
const byVal = (rs) => Object.fromEntries(rs.map((r) => [r.value, r]))

test("ttlWarning: only above 240 s", () => {
  assert.equal(ttlWarning(240), ""); assert.equal(ttlWarning(30), "")
  assert.equal(ttlWarning(300), TTL_WARN); assert.equal(ttlWarning("600"), TTL_WARN)
  assert.match(TTL_WARN, /may lose the cache if its TTL is 5 min/)
  const pick = rowAction("period", S())
  assert.deepEqual(pick.options.map((o) => o.value), PERIODS.map((p) => `batch ${p}`))
  assert.deepEqual(pick.options.filter((o) => o.description).map((o) => o.title), ["300s", "600s"])
  assert.equal(pick.current, "batch 300")
})

test("parseStatus: missing dir = unavailable, missing toggles = all off, bad json = unreadable", () => {
  assert.deepEqual(parseStatus(false, "{}", "{}"), { available: false, tg: null, st: null })
  assert.deepEqual(parseStatus(true, null, null).tg, { week: false, global: false, slots: {} })
  assert.equal(parseStatus(true, "{nope", null).tg, null)
  assert.deepEqual(parseStatus(true, '{"week":true}', null).tg.slots, {})
})

test("headline = the usage line (5h, week, mode, lane cap, age) or why not", () => {
  assert.equal(headline(S(), NOW), "pacer · 5h 42% · week 22% · EASE · lanes ≤3 · 2 min ago")
  assert.equal(headline(parseStatus(false), NOW), "pacer · status unavailable here")
  assert.match(headline(parseStatus(true, null, null), NOW), /not read yet/)
  assert.match(headline({ available: true, st: { pace: { ok: false, err: "no token" } } }, NOW), /usage error: no token/)
})

test("rows: groups, flags, warning, slot rows with last-run age", () => {
  const rs = rows(S()), r = byVal(rs)
  assert.equal(r.pace.category, "This session · slot main")
  assert.equal(r.pace.title, "[x] pace"); assert.equal(r.batch.title, "[x] batch"); assert.equal(r.me.title, "[ ] batch me")
  assert.equal(r.period.description, TTL_WARN)
  assert.equal(r.week.title, "[x] week"); assert.equal(r.global.title, "[ ] global"); assert.equal(r.week.category, "Account")
  assert.equal(r["slot:main"].title, "main (here) · coord A")
  assert.equal(r["slot:work"].description, "pace · last run 5 min ago · Enter: move here")
  assert.equal(r["slot:main"].description, "pace + batch 300s + me off · last run never")
  assert.deepEqual(rs.map((x) => x.category).filter((c, i, a) => a.indexOf(c) === i), ["This session · slot main", "Account", "Slots", "Actions"])
  // a session with no slot: not paced, batch me disabled
  const n = byVal(rows(S({ sid: "ses_Z" })))
  assert.equal(n.pace.category, "This session · not paced"); assert.equal(n.pace.title, "[ ] pace"); assert.equal(n.me.disabled, true)
  // status unavailable: no slot rows, toggles shown as [?], actions still there
  const u = rows({ ...parseStatus(false), sid: "ses_A", now: NOW })
  assert.equal(u.some((x) => x.category === "Slots"), false); assert.equal(byVal(u).pace.title, "[?] pace")
  assert.ok(byVal(u).offall && byVal(u).new)
  assert.equal(byVal(rows(S({ tg: emptyToggles() }))).noslots.disabled, true)
})

test("rowAction: Enter -> the /pacer text that flips the row", () => {
  const s = S()
  assert.deepEqual(rowAction("pace", s), { kind: "run", text: "pace off" })
  assert.deepEqual(rowAction("batch", s), { kind: "run", text: "batch off" })
  assert.deepEqual(rowAction("me", s), { kind: "run", text: "batch me on" })
  assert.deepEqual(rowAction("week", s), { kind: "run", text: "week off" })
  assert.deepEqual(rowAction("global", s), { kind: "run", text: "global on" })
  assert.deepEqual(rowAction("slot:work", s), { kind: "run", text: "move work" })
  assert.equal(rowAction("slot:main", s).kind, "none")
  const z = S({ sid: "ses_Z" })
  assert.deepEqual(rowAction("pace", z), { kind: "run", text: "on" })
  assert.deepEqual(rowAction("batch", z), { kind: "run", text: "batch on" })
  assert.equal(rowAction("me", z).kind, "none")
  assert.equal(rowAction("offall", s).kind, "confirm"); assert.equal(rowAction("offall", s).text, "off all")
  assert.equal(rowAction("new", s).kind, "prompt")
  assert.deepEqual(rowAction("status", s), { kind: "run", text: "status", close: true })
  // unknown state: ask on/off instead of guessing
  const u = rowAction("pace", { tg: null, sid: "ses_A" })
  assert.equal(u.kind, "choose"); assert.deepEqual(u.options.map((o) => o.value), ["on", "pace off"])
})

test("every command text the TUI can send parses on the server (no usage replies)", () => {
  const texts = new Set()
  for (const sv of [S(), S({ sid: "ses_Z" }), { tg: null, sid: "ses_A" }]) {
    for (const r of rows({ available: true, st, now: NOW, ...sv })) {
      const a = rowAction(r.value, sv)
      if (a.text) texts.add(a.text)
      for (const o of a.options ?? []) texts.add(o.value)
    }
  }
  texts.add(newSlotCommand("Lane_2").text)
  for (const t of texts) assert.notEqual(parseArgs(t).op, "usage", t)
  assert.ok(texts.size >= 20, `covered ${texts.size}`)
})

test("server: pace on|off keeps batch; the slot goes only when both are off", () => {
  assert.deepEqual(parseArgs("pace off"), { op: "pace", on: false })
  assert.deepEqual(parseArgs("pace on work"), { op: "pace", on: true, name: "work" })
  assert.equal(parseArgs("on pace").op, "usage") // "pace" is a keyword now
  let r = applyOp(emptyToggles(), "ses_A", "A", parseArgs("batch on"))
  r = applyOp(r.tg, "ses_A", "A", parseArgs("on"))
  assert.equal(r.tg.slots.main.pace, true)
  r = applyOp(r.tg, "ses_A", "A", parseArgs("pace off"))
  assert.equal(r.tg.slots.main.pace, false); assert.equal(r.tg.slots.main.batch, true); assert.match(r.reply, /pace off\.$/)
  r = applyOp(r.tg, "ses_A", "A", parseArgs("batch off"))
  assert.equal(r.tg.slots.main, undefined)
  r = applyOp(applyOp(emptyToggles(), "ses_A", "A", parseArgs("on")).tg, "ses_A", "A", parseArgs("pace off"))
  assert.equal(r.tg.slots.main, undefined); assert.match(r.reply, /slot released/)
  assert.equal(applyOp(emptyToggles(), "ses_A", "A", parseArgs("pace off")).changed, false)
})

test("held here: footer timer text, and a `flush now` row only while this slot holds something", () => {
  const held = (o = {}) => ({ ...st, rt: { ...st.rt, ses_A: { held: { ids: ["x", "y"], since: NOW - 5e3, kinds: {} }, due: NOW + 22_400, busy: false, ...o } } })
  assert.deepEqual(heldHere(held(), "ses_A", NOW), { n: 2, left: 23, when: "flush in 23s" })
  assert.equal(heldHere(held({ due: NOW - 1 }), "ses_A", NOW).when, "flushing…")
  assert.equal(heldHere(held({ busy: true }), "ses_A", NOW).when, "flush after this turn")
  assert.equal(heldHere(held({ held: null }), "ses_A", NOW), null); assert.equal(heldHere(held(), null, NOW), null)
  assert.equal(footerText(JSON.stringify(held()), "ses_A", NOW), "pacer: 2 held · flush in 23s")
  assert.equal(footerText(JSON.stringify(held()), "ses_B", NOW), "", "another session: empty")
  assert.equal(footerText("{bad", "ses_A", NOW), ""); assert.equal(footerText("", "ses_A", NOW), "")
  // nothing held: the slot's flags show, so the footer proves the pacer is live; no slot here = empty
  const tgs = JSON.stringify({ slots: { main: { sid: "ses_A", batch: true, period: 60 }, off: { sid: "ses_C" } } })
  assert.equal(footerText(JSON.stringify(held({ held: null })), "ses_A", NOW, tgs), "pacer: batch 60s · nothing held")
  assert.equal(footerText("", "ses_A", NOW, tgs), "pacer: batch 60s · nothing held", "no state file yet")
  assert.equal(footerText(JSON.stringify(held()), "ses_A", NOW, tgs), "pacer: 2 held · flush in 23s", "held wins")
  assert.equal(footerText("", "ses_B", NOW, tgs), "", "no slot here")
  assert.equal(footerText("", "ses_C", NOW, tgs), "", "a slot with nothing on")
  assert.equal(footerText("", "ses_A", NOW, "{bad"), "")
  // the row: after `batch me`, Enter flushes and closes; absent with nothing held or no slot here
  const s = S({ st: held() }), rs = rows(s), r = byVal(rs)
  assert.equal(r.flush.description, "2 held · flush in 23s"); assert.equal(rs.indexOf(r.flush), rs.indexOf(r.me) + 1)
  assert.deepEqual(rowAction("flush", s), { kind: "run", text: "flush", close: true })
  assert.equal(byVal(rows(S())).flush, undefined)
  assert.equal(byVal(rows(S({ st: held(), sid: "ses_Z" }))).flush, undefined)
  assert.notEqual(parseArgs("flush").op, "usage")
})

test("replyToast: status goes to the transcript; other replies toast without the [pacer] tag", () => {
  assert.equal(replyToast("status", "[pacer] slots: …"), null); assert.equal(replyToast(" STATUS ", "x"), null)
  assert.equal(replyToast("batch on", ""), null); assert.equal(replyToast("batch on", undefined), null)
  assert.deepEqual(replyToast("batch on", "[pacer] slot main: batch on (60 s)."), { message: "slot main: batch on (60 s).", variant: "success" })
  assert.equal(replyToast("x y z", "[pacer] usage: /pacer …").variant, "warning")
  assert.equal(replyToast("on", "[pacer] error: bad name").variant, "error")
  assert.ok(replyToast("on", "[pacer] " + "a".repeat(500)).message.length <= 300)
})

test("newSlotCommand + slashRoute", () => {
  assert.deepEqual(newSlotCommand(" Lane2 "), { text: "on lane2" })
  assert.match(newSlotCommand("batch").error, /bad slot name/); assert.match(newSlotCommand("a b").error, /bad slot name/)
  assert.equal(newSlotCommand("").error, "no name given")
  assert.deepEqual(slashRoute(""), { kind: "open" }); assert.deepEqual(slashRoute(undefined), { kind: "open" }); assert.deepEqual(slashRoute("   "), { kind: "open" })
  assert.deepEqual(slashRoute(" batch on "), { kind: "forward", text: "batch on" })
  assert.equal(ago(0, NOW), "never"); assert.equal(ago(NOW - 3 * 3600e3, NOW), "3 h ago"); assert.equal(flagsOf({}), "-")
})
