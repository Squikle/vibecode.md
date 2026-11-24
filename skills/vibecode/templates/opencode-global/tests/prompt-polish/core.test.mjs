// node --test tests/   (pure parts of polish-show: toggle file, command args, which message, word diff, view)
import test from "node:test"
import assert from "node:assert/strict"
import {
  parseShow, serializeShow, commandRoute, stateText, latestPolish, wordDiff, hunks, splitNote, view, viewLines, blockFor, MAX_HUNKS,
} from "../../plugins/prompt-polish/polish-core.js"

const ORIG = "Consider making it as another diagram if changing the original one would make it look to complex"
const FINAL = "Consider making it another diagram if changing the original one would make it look too complex"
const user = (text, pp, id = "msg_u") => ({ id, type: "user", text, ...(pp ? { metadata: { promptPolish: pp } } : {}) })
const pp = (original, extra = {}) => ({ original, language: "en", issues: 0, followup: true, model: "x/haiku", ...extra })

test("parseShow: default ON for missing / empty / bad / odd files", () => {
  assert.equal(parseShow(undefined), true)
  assert.equal(parseShow(""), true)
  assert.equal(parseShow("{nope"), true)
  assert.equal(parseShow('{"show":"no"}'), true)
  assert.equal(parseShow('{"show":false}'), false)
  assert.equal(parseShow('{"show":true}'), true)
  assert.equal(parseShow(serializeShow(false, 0)), false)
  assert.match(serializeShow(true, 0), /"updated":"1970-01-01T00:00:00.000Z"/)
})

test("commandRoute: bare/toggle flips, on/off set, status reports, junk errors", () => {
  assert.deepEqual(commandRoute("", true), { next: false })
  assert.deepEqual(commandRoute(undefined, false), { next: true })
  assert.deepEqual(commandRoute(" Toggle ", true), { next: false })
  assert.deepEqual(commandRoute("on", false), { next: true })
  assert.deepEqual(commandRoute("OFF", true), { next: false })
  assert.deepEqual(commandRoute("status", true), { next: undefined })
  assert.match(commandRoute("maybe", true).error, /use \/polish-show \[on\|off\|status\]/)
  assert.match(stateText(true), /ON.*0 tokens/)
  assert.match(stateText(false), /OFF.*still runs/)
})

test("latestPolish: only the latest user message, only when the polisher changed it", () => {
  const polished = user(FINAL, pp(ORIG))
  assert.equal(latestPolish([polished]).final, FINAL)
  assert.equal(latestPolish([polished]).original, ORIG)
  // assistant / synthetic messages after it don't hide it
  assert.ok(latestPolish([polished, { type: "assistant", content: [] }, { type: "synthetic", text: "x" }]))
  // the next prompt (not polished) clears it
  assert.equal(latestPolish([polished, user("next one")]), null)
  // no metadata, identical text, junk
  assert.equal(latestPolish([user("plain")]), null)
  assert.equal(latestPolish([user(ORIG, pp(ORIG))]), null)
  assert.equal(latestPolish([user(FINAL, { original: 5 })]), null)
  assert.equal(latestPolish(undefined), null)
  assert.equal(latestPolish([]), null)
})

test("wordDiff + hunks: small edits found with context", () => {
  const d = wordDiff(ORIG, FINAL)
  assert.deepEqual(d.filter((x) => x.op !== "eq"), [{ op: "del", w: "as" }, { op: "del", w: "to" }, { op: "ins", w: "too" }])
  const hs = hunks(d)
  assert.equal(hs.length, 2)
  assert.deepEqual(hs[0], { before: "Consider making it", del: "as", ins: "", after: "another diagram if", cut: false })
  assert.deepEqual(hs[1], { before: "make it look", del: "to", ins: "too", after: "complex", cut: true })
  assert.deepEqual(wordDiff("a b", "a b").every((x) => x.op === "eq"), true)
  assert.deepEqual(wordDiff("", "new words").map((x) => x.op), ["ins", "ins"])
})

test("wordDiff: big inputs fall back to prefix/suffix only (no huge table)", () => {
  const a = Array.from({ length: 3000 }, (_, i) => `a${i}`).join(" ")
  const b = Array.from({ length: 3000 }, (_, i) => `b${i}`).join(" ")
  const t0 = Date.now()
  const d = wordDiff(`start ${a} end`, `start ${b} end`)
  assert.ok(Date.now() - t0 < 2000)
  assert.equal(d[0].op, "eq"); assert.equal(d.at(-1).op, "eq")
  assert.equal(d.filter((x) => x.op === "del").length, 3000)
})

test("splitNote: strips the [prompt-check, auto] note and the reply-language suffix", () => {
  const note = "[prompt-check, auto] Possible conflict: X vs Y. Ask: which? — if it's real and context doesn't settle it, ask me before starting; otherwise ignore this."
  assert.deepEqual(splitNote(`do X\n\n${note}`), { body: "do X", note })
  assert.deepEqual(splitNote(`do X\n\n${note}\n\n(Reply in the language with ISO code "ru".)`), { body: "do X", note })
  assert.deepEqual(splitNote("just text"), { body: "just text", note: null })
})

test("view: edits mode marks changed words; text lines read well", () => {
  const v = view(latestPolish([user(FINAL, pp(ORIG))]))
  assert.equal(v.mode, "edits")
  assert.equal(v.title, "✎ prompt-polish · 2 edits")
  assert.deepEqual(viewLines(v), [
    "✎ prompt-polish · 2 edits",
    "  Consider making it as (removed) another diagram if…",
    "  …make it look to → too complex…",
  ])
  assert.deepEqual(v.hunks[1].map((s) => s.k), ["ctx", "del", "arrow", "ins", "ctx"])
})

test("view: insertion-only edit shows +word", () => {
  const v = view({ original: "merge optimizations with agents diagram", final: "merge optimizations with the agents diagram", language: "en" })
  assert.deepEqual(viewLines(v).slice(1), ["  merge optimizations with +the agents diagram…"])
})

test("view: conflict note is shown, even when the body is unchanged", () => {
  const note = "[prompt-check, auto] Possible conflict: wants both A and not A. Ask: A or not A? — if it's real and context doesn't settle it, ask me before starting; otherwise ignore this."
  const v = view({ original: "do A and not A", final: `do A and not A\n\n${note}`, issues: 1 })
  assert.equal(v.title, "✎ prompt-polish · conflict note added")
  assert.equal(v.note, note)
  assert.equal(viewLines(v).at(-1), `  ${note}`)
})

test("view: translation / big rewrite -> 'sent as' excerpt instead of hunks", () => {
  const v = view({ original: "привет, почини тесты пожалуйста", final: "Hi, please fix the tests", language: "ru" })
  assert.equal(v.mode, "rewrite")
  assert.equal(v.title, "✎ prompt-polish · translated from ru")
  assert.equal(v.excerpt, "Hi, please fix the tests")
  const long = view({ original: "x", final: "word ".repeat(200), language: "en" })
  assert.equal(long.excerpt.length, 280); assert.ok(long.excerpt.endsWith("…"))
})

test("view: many edits are capped, rest counted", () => {
  const orig = Array.from({ length: 40 }, (_, i) => (i % 5 === 0 ? `typo${i}` : `w${i}`)).join(" ")
  const fin = orig.replace(/typo(\d+)/g, "fixed$1")
  const v = view({ original: orig, final: fin })
  assert.equal(v.hunks.length, MAX_HUNKS)
  assert.equal(v.more, 8 - MAX_HUNKS)
  assert.match(viewLines(v).at(-1), /\+4 more \(full text in the message above\)/)
})

test("view: whitespace-only change renders nothing; blockFor honours the toggle", () => {
  assert.equal(view({ original: "a  b", final: "a b" }), null)
  assert.equal(view(null), null)
  const msgs = [user(FINAL, pp(ORIG))]
  assert.ok(blockFor(true, msgs))
  assert.equal(blockFor(false, msgs), null)
})
