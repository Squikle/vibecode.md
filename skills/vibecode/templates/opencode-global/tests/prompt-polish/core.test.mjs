// node --test tests/   (polish-core.js: settings, /polish routing, which message pops, diff, overlay text)
import test from "node:test"
import assert from "node:assert/strict"
import {
  DEFAULTS, parseSettings, serializeSettings, commandRoute, statusText, rows, dataDirFrom,
  latestPolish, nextPop, tokens, wordDiff, splitNote, overlay, overlayText, toastText, strike, MAX_CHARS,
} from "../../plugins/prompt-polish/polish-core.js"

const ORIG = "is the readme ok? kinda merge optimizations with agents diagram, or make another one if changing it makes it look to complex"
const FINAL = "Is the readme ok? Merge optimizations with the agents diagram, or make another one if changing it makes it look too complex"
const user = (id, text, original, extra = {}) => ({ id, type: "user", text, metadata: original === undefined ? undefined : { promptPolish: { original, language: "en", ...extra } } })

test("settings: no file / junk / missing keys = both ON; booleans respected", () => {
  assert.deepEqual(DEFAULTS, { polish: true, show: true })
  for (const raw of [undefined, "", "  ", "{", "null", "[]", '{"polish":"no"}']) assert.deepEqual(parseSettings(raw), { polish: true, show: true }, String(raw))
  assert.deepEqual(parseSettings('{"polish":false}'), { polish: false, show: true })
  assert.deepEqual(parseSettings('{"show":false}'), { polish: true, show: false }) // old v1 show.json shape still reads
  const s = JSON.parse(serializeSettings({ polish: false, show: true }, 0))
  assert.equal(s.polish, false); assert.equal(s.show, true); assert.equal(s.updated, "1970-01-01T00:00:00.000Z")
  assert.match(dataDirFrom({}, "/h"), /[\\/]h[\\/]\.local[\\/]share[\\/]prompt-polish$/)
  assert.equal(dataDirFrom({ PROMPT_POLISH_DATA: "/x" }, "/h"), "/x")
})

test("/polish routing: bare = pop-up; on/off = polisher; show on/off; status; junk = usage", () => {
  const s = { polish: true, show: true }
  assert.deepEqual(commandRoute("", s), { open: true })
  assert.deepEqual(commandRoute("   ", s), { open: true })
  assert.deepEqual(commandRoute("status", s), { status: true })
  assert.deepEqual(commandRoute("off", s), { set: { polish: false } })
  assert.deepEqual(commandRoute("ON", s), { set: { polish: true } })
  assert.deepEqual(commandRoute("show off", s), { set: { show: false } })
  assert.deepEqual(commandRoute("show", s), { set: { show: false } }) // bare key = toggle
  assert.deepEqual(commandRoute("polish", { polish: false, show: true }), { set: { polish: true } })
  assert.deepEqual(commandRoute("polish off", s), { set: { polish: false } })
  for (const bad of ["bogus", "show maybe", "show on now", "off off"]) assert.ok(commandRoute(bad, s).error, bad)
})

test("status line and pop-up rows show both toggles; 'last' disabled when nothing polished", () => {
  assert.equal(statusText({ polish: true, show: true }), "polish ON · show ON")
  assert.match(statusText({ polish: false, show: true }), /polish OFF .*as typed/)
  const r = rows({ polish: true, show: false }, null)
  assert.deepEqual(r.map((x) => x.value), ["polish", "show", "last"])
  assert.match(r[0].title, /^\[x\] polish/); assert.match(r[1].title, /^\[ \] show/)
  assert.equal(r[2].disabled, true)
  assert.equal(rows(DEFAULTS, { id: "m" })[2].disabled, undefined)
})

test("latestPolish: only the newest user message, only if polish changed it", () => {
  assert.equal(latestPolish(undefined), null)
  assert.equal(latestPolish([user("a", FINAL, ORIG), user("b", "plain")]), null) // newest wasn't polished
  assert.equal(latestPolish([user("a", ORIG, ORIG)]), null) // unchanged
  const p = latestPolish([user("a", "x"), user("b", FINAL, ORIG), { id: "c", type: "assistant" }])
  assert.deepEqual(p, { id: "b", original: ORIG, final: FINAL, language: "en" })
})

test("nextPop: first look at a session only records; a new polished id pops once", () => {
  let seen = {}
  let r = nextPop(seen, "s1", [user("a", FINAL, ORIG)])
  assert.equal(r.pop, null) // old message, no overlay
  r = nextPop(r.seen, "s1", [user("a", FINAL, ORIG)])
  assert.equal(r.pop, null)
  r = nextPop(r.seen, "s1", [user("a", FINAL, ORIG), user("b", "typed, not polished yet")])
  assert.equal(r.pop, null) // optimistic entry before the hook finishes
  r = nextPop(r.seen, "s1", [user("a", FINAL, ORIG), user("b", FINAL, ORIG)])
  assert.equal(r.pop.id, "b")
  r = nextPop(r.seen, "s1", [user("a", FINAL, ORIG), user("b", FINAL, ORIG)])
  assert.equal(r.pop, null) // once
  assert.equal(nextPop(r.seen, undefined, []).pop, null)
})

test("tokens/wordDiff keep whitespace and mark only real changes", () => {
  assert.deepEqual(tokens("  a  b\n\n\n\nc"), [{ w: "a", sp: "  " }, { w: "b", sp: "\n\n" }, { w: "c", sp: "" }])
  const d = wordDiff("make it look to complex", "make it look too complex")
  assert.deepEqual(d.map((x) => `${x.op}:${x.w}`), ["eq:make", "eq:it", "eq:look", "del:to", "ins:too", "eq:complex"])
  assert.deepEqual(wordDiff("same", "same").map((x) => x.op), ["eq"])
})

test("splitNote pulls the [prompt-check, auto] note and the reply-language suffix off", () => {
  const n = splitNote(`${FINAL}\n\n[prompt-check, auto] Possible conflict: x. Ask: y?\n\n(Reply in the language with ISO code "ru".)`)
  assert.equal(n.body, FINAL)
  assert.match(n.note, /^\[prompt-check, auto\] Possible conflict/)
  assert.deepEqual(splitNote("plain"), { body: "plain", note: null })
})

test("overlay: corrected text with del/ins segments, no edit count in the title", () => {
  const v = overlay({ original: ORIG, final: FINAL, language: "en" })
  assert.equal(v.title, "✎ polished")
  assert.doesNotMatch(v.title, /edit/)
  const txt = overlayText(v)
  assert.match(txt, /\[-to-\] \{\+too\+\}/)
  assert.match(txt, /\[-kinda merge-\] \{\+Merge\+\}|\[-kinda-\] \[-merge-\] \{\+Merge\+\}/)
  assert.ok(v.duration >= 6000 && v.duration <= 20000)
  assert.equal(overlay({ original: "a  b", final: "a b" }), null) // whitespace only
  assert.equal(overlay(null), null)
})

test("overlay: translation / heavy rewrite shows the final text only; note kept", () => {
  const v = overlay({ original: "привет как дела с проектом", final: "Hi, how is the project going?", language: "ru" })
  assert.equal(v.title, "✎ polished · translated from ru")
  assert.deepEqual(v.segs, [{ t: "Hi, how is the project going?", k: "eq" }])
  const c = overlay({ original: ORIG, final: `${FINAL}\n\n[prompt-check, auto] Possible conflict: x.` })
  assert.match(c.title, /conflict note/)
  assert.equal(c.segs.at(-1).k, "note")
})

test("long messages are shortened around unchanged runs, changes always kept", () => {
  const filler = Array.from({ length: 200 }, (_, i) => `word${i}`).join(" ")
  const v = overlay({ original: `${filler} teh end`, final: `${filler} the end` })
  const txt = overlayText(v)
  assert.ok(txt.length < MAX_CHARS + 50, String(txt.length))
  assert.match(txt, /…/); assert.match(txt, /\[-teh-\] \{\+the\+\}/)
})

test("toastText: removed words struck with U+0336, added words plain, nothing else added", () => {
  assert.equal(strike("to"), "t̶o̶")
  assert.equal(strike("a b"), "a̶ b̶")
  const t = toastText(overlay({ original: "make it look to complex", final: "make it look too complex" }))
  assert.equal(t, "make it look t̶o̶ too complex")
  assert.equal(toastText(null), "")
})
