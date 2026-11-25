import { test } from "node:test"
import assert from "node:assert/strict"
import {
  parseMarkers, applyMarkers, isOpen, checkItem, uncheckItem, openList, historyList,
  pruneItems, pruneChecks, pushToast, freshItems, parseStore, footerText, sidebarLines,
  rowsOpen, rowsHistory, rowsToasts, promptRoute, relTime, HISTORY_MS,
  walkPrompt, walkRow, WALK, WALK_MAX, isLeftClick, SLASH,
  MARKS, textWidth, cutTo, wrapTo, dialogWidth, titleWidth, SIDEBAR_W,
  clipTo, scrollTitle, maxScroll, SCROLL_STEP, PIN_MIN, WALK_HEAD, walkTags, parseDone, applyWalkDone, pruneDones, doneKey,
  shownList, fyiList, needsYou,
} from "../../plugins/news/news-core.js"

const T0 = Date.UTC(2026, 10, 24, 12, 0, 0)
const meta = (over = {}) => ({ sessionID: "ses_a", messageID: "msg_1", sessionTitle: "coord", directory: "C:/p", ...over })

// ---------------------------------------------------------------- parser
test("parser: the three kinds, tag slug lowercased, text trimmed", () => {
  const got = parseMarkers([
    "intro line",
    "📌 [Build-Fix] bug: flaky test in ci  ",
    "📌 [deploy] waits on you: approve the prod push",
    "📌 [v2.4] news: OpenCode 2.4.1 is out",
  ].join("\n"))
  assert.deepEqual(got, [
    { tag: "build-fix", kind: "bug", text: "flaky test in ci" },
    { tag: "deploy", kind: "waits on you", text: "approve the prod push" },
    { tag: "v2.4", kind: "news", text: "OpenCode 2.4.1 is out" },
  ])
})

test("parser: a marker inside a ``` fence (or ~~~) is not an item", () => {
  const text = [
    "📌 [real] news: outside",
    "```md",
    "📌 [fake] bug: inside a fence",
    "```",
    "~~~",
    "📌 [fake2] bug: inside tilde fence",
    "~~~",
    "📌 [after] news: after the fences",
  ].join("\n")
  assert.deepEqual(parseMarkers(text).map((m) => m.tag), ["real", "after"])
})

test("parser: an unclosed fence swallows the rest; a longer fence needs a matching close", () => {
  assert.deepEqual(parseMarkers("```\n📌 [x] bug: y"), [])
  const t = ["````", "```", "📌 [in] bug: still inside", "````", "📌 [out] bug: outside"].join("\n")
  assert.deepEqual(parseMarkers(t).map((m) => m.tag), ["out"])
})

test("parser: bullets / quotes / bold prefix ok; inline code, mid-line and bad lines are not items", () => {
  const t = [
    "- 📌 [a] news: bullet",
    "> 📌 [b] news: quote",
    "**📌 [c] news: bold**",
    "`📌 [d] news: inline code`",
    "see 📌 [e] news: mid-line",
    "📌 [f] question: unknown kind",
    "📌 [] news: empty tag",
    "📌 [g] news:   ",
    "📌 [has space] news: bad tag",
    "📌 [w] waits: alias",
    "📌 [W2] Waits On You: case",
  ].join("\n")
  assert.deepEqual(parseMarkers(t), [
    { tag: "a", kind: "news", text: "bullet" },
    { tag: "b", kind: "news", text: "quote" },
    { tag: "c", kind: "news", text: "bold" },
    { tag: "w", kind: "waits on you", text: "alias" },
    { tag: "w2", kind: "waits on you", text: "case" },
  ])
})

test("parser: same tag twice in one text -> last wins; text capped; CRLF; non-string -> []", () => {
  const long = "x".repeat(500)
  const got = parseMarkers(`📌 [t] news: first\r\n📌 [t] bug: second\r\n📌 [l] news: ${long}`)
  assert.equal(got.length, 2)
  assert.deepEqual(got[0], { tag: "t", kind: "bug", text: "second" })
  assert.ok(got[1].text.length <= 200 && got[1].text.endsWith("…"))
  assert.deepEqual(parseMarkers(undefined), [])
  assert.deepEqual(parseMarkers(""), [])
})

// ---------------------------------------------------------------- update by tag
test("update by tag: same tag updates text + message link, no duplicate; created kept", () => {
  let r = applyMarkers({}, parseMarkers("📌 [deploy] waits on you: approve"), meta(), T0)
  assert.deepEqual(r.changed, ["deploy"])
  const it0 = r.items.deploy
  assert.equal(it0.created, T0)
  r = applyMarkers(r.items, parseMarkers("📌 [deploy] waits on you: approve, now with diff"), meta({ sessionID: "ses_b", messageID: "msg_9", sessionTitle: "coord 2" }), T0 + 1000)
  assert.equal(Object.keys(r.items).length, 1)
  const it = r.items.deploy
  assert.equal(it.text, "approve, now with diff")
  assert.equal(it.sessionID, "ses_b")
  assert.equal(it.messageID, "msg_9")
  assert.equal(it.sessionTitle, "coord 2")
  assert.equal(it.created, T0)
  assert.equal(it.updated, T0 + 1000)
})

test("update by tag: a verbatim repeat (same kind/text/message) is not a change", () => {
  const a = applyMarkers({}, parseMarkers("📌 [x] news: hi"), meta(), T0)
  const b = applyMarkers(a.items, parseMarkers("📌 [x] news: hi"), meta(), T0 + 5)
  assert.deepEqual(b.changed, [])
  assert.equal(b.items.x.updated, T0)
})

test("applyMarkers is pure: the input object is not mutated", () => {
  const items = Object.freeze({})
  const r = applyMarkers(items, parseMarkers("📌 [x] news: hi"), meta(), T0)
  assert.deepEqual(items, {})
  assert.ok(r.items.x)
})

// ---------------------------------------------------------------- check flow
test("check flow: checking is the only way out; a later same-text marker keeps it checked", () => {
  let items = applyMarkers({}, parseMarkers("📌 [a] bug: broken\n📌 [b] news: fyi"), meta(), T0).items
  let checks = {}
  assert.deepEqual(shownList(items, checks).map((i) => i.tag).sort(), ["a", "b"])
  checks = checkItem(checks, items.a, T0 + 10)
  assert.deepEqual(shownList(items, checks).map((i) => i.tag), ["b"])
  assert.equal(isOpen(items.a, checks.a), false)
  // the model repeats the same line: still checked
  items = applyMarkers(items, parseMarkers("📌 [a] bug: broken"), meta({ messageID: "msg_2" }), T0 + 20).items
  assert.equal(isOpen(items.a, checks.a), false)
  // new text under the same tag = news again -> reopens
  items = applyMarkers(items, parseMarkers("📌 [a] bug: broken again, worse"), meta(), T0 + 30).items
  assert.equal(isOpen(items.a, checks.a), true)
  // uncheck from history
  checks = checkItem(checks, items.a, T0 + 40)
  checks = uncheckItem(checks, "a")
  assert.equal(isOpen(items.a, checks.a), true)
})

test("open list: waits on you on top, then newest first", () => {
  let items = {}
  items = applyMarkers(items, parseMarkers("📌 [old-news] news: n1"), meta(), T0).items
  items = applyMarkers(items, parseMarkers("📌 [wait-old] waits on you: w1"), meta(), T0 + 1).items
  items = applyMarkers(items, parseMarkers("📌 [bug-new] bug: b1"), meta(), T0 + 2).items
  items = applyMarkers(items, parseMarkers("📌 [wait-new] waits on you: w2"), meta(), T0 + 3).items
  assert.deepEqual(shownList(items, {}).map((i) => i.tag), ["wait-new", "wait-old", "bug-new", "old-news"])
  // kind order beats age: waits on you, then bugs, then news (a newer news still comes after an older bug)
  items = applyMarkers(items, parseMarkers("📌 [news-newest] news: n2"), meta(), T0 + 9).items
  assert.deepEqual(shownList(items, {}).map((i) => i.tag), ["wait-new", "wait-old", "bug-new", "news-newest", "old-news"])
  // only waits on you is "open" (needs the user); bugs + news are FYI, shown under them
  assert.deepEqual(openList(items, {}).map((i) => i.tag), ["wait-new", "wait-old"])
  assert.deepEqual(fyiList(items, {}).map((i) => i.tag), ["bug-new", "news-newest", "old-news"])
  assert.equal(needsYou(items["wait-new"]), true)
  assert.equal(needsYou(items["bug-new"]), false)
})

// ---------------------------------------------------------------- history pruning
test("history: checked items show 14 days, newest check first; pruning drops older ones", () => {
  let items = applyMarkers({}, parseMarkers("📌 [a] news: a\n📌 [b] news: b\n📌 [c] news: c"), meta(), T0).items
  let checks = {}
  checks = checkItem(checks, items.a, T0)
  checks = checkItem(checks, items.b, T0 + HISTORY_MS - 1000)
  const now = T0 + HISTORY_MS + 1
  assert.deepEqual(historyList(items, checks, now).map((i) => i.tag), ["b"])
  const kept = pruneItems(items, checks, now)
  assert.deepEqual(Object.keys(kept).sort(), ["b", "c"]) // a: checked > 14d -> gone; c: open -> kept forever
  // orphan checks: dropped only when old AND the item is gone
  const c2 = pruneChecks(checks, kept, now)
  assert.deepEqual(Object.keys(c2).sort(), ["b"])
  // an unreadable items file must not wipe checks
  assert.deepEqual(pruneChecks(checks, undefined, now), checks)
})

test("pruneItems: a reopened item (text changed after the check) is never pruned", () => {
  let items = applyMarkers({}, parseMarkers("📌 [a] news: a"), meta(), T0).items
  const checks = checkItem({}, items.a, T0)
  items = applyMarkers(items, parseMarkers("📌 [a] news: a2"), meta(), T0 + 10).items
  assert.ok(pruneItems(items, checks, T0 + HISTORY_MS * 2).a)
})

// ---------------------------------------------------------------- toast log
test("toast log: newest first, capped, 14 days", () => {
  let log = []
  for (let i = 0; i < 310; i++) log = pushToast(log, { message: `m${i}`, variant: "info", source: "self" }, T0 + i)
  assert.equal(log.length, 300)
  assert.equal(log[0].message, "m309")
  log = pushToast(log, { message: "late", variant: "info", source: "bus" }, T0 + HISTORY_MS + 400)
  assert.deepEqual(log.map((t) => t.message), ["late"])
  assert.deepEqual(pushToast(log, { message: "" }, T0), log) // empty message is not logged
})

test("freshItems: first load is a silent baseline; then new/changed open items toast once", () => {
  let items = applyMarkers({}, parseMarkers("📌 [a] news: a"), meta(), T0).items
  let r = freshItems(undefined, items, {})
  assert.deepEqual(r.fresh, [])
  items = applyMarkers(items, parseMarkers("📌 [b] bug: b\n📌 [a] news: a"), meta(), T0 + 1).items
  r = freshItems(r.snap, items, {})
  assert.deepEqual(r.fresh.map((i) => i.tag), ["b"])
  r = freshItems(r.snap, items, {})
  assert.deepEqual(r.fresh, [])
  // a change to a checked item that stays checked: no toast
  const checks = checkItem({}, items.b, T0 + 2)
  items = applyMarkers(items, parseMarkers("📌 [b] bug: b"), meta({ messageID: "m3" }), T0 + 3).items
  assert.deepEqual(freshItems(r.snap, items, checks).fresh, [])
})

// ---------------------------------------------------------------- storage parse (fail-open)
test("parseStore: bad / missing JSON -> empty defaults, ok flag false", () => {
  assert.deepEqual(parseStore(null, "items"), { ok: false, value: {} })
  assert.deepEqual(parseStore("{nope", "items"), { ok: false, value: {} })
  assert.deepEqual(parseStore('{"v":1,"items":{"a":{"tag":"a"}}}', "items"), { ok: true, value: { a: { tag: "a" } } })
  assert.deepEqual(parseStore('{"v":1,"toasts":[{"message":"x"}]}', "toasts"), { ok: true, value: [{ message: "x" }] })
  assert.deepEqual(parseStore('{"v":1,"toasts":"bad"}', "toasts"), { ok: false, value: [] })
})

// ---------------------------------------------------------------- views
test("footer + sidebar text", () => {
  const items = applyMarkers({}, parseMarkers("📌 [w] waits on you: approve\n📌 [b] bug: crash"), meta(), T0).items
  assert.equal(footerText({}, {}), "")
  assert.equal(footerText(items, {}), "📌 1 waits on you") // the bug is FYI: not counted
  const lines = sidebarLines(items, {}, 5)
  assert.deepEqual(lines, ["📌 Todo: 1 waits on you", "⏳ [w]", "  approve", "FYI: 1 bug (/todo)"])
  const many = applyMarkers({}, parseMarkers([1, 2, 3, 4, 5, 6, 7].map((n) => `📌 [n${n}] waits on you: x`).join("\n")), meta(), T0).items
  assert.equal(sidebarLines(many, {}, 5).at(-1), "… +2 more (/todo)")
  // only FYI left: no count anywhere, the footer stays clickable
  const fyi = applyMarkers({}, parseMarkers("📌 [b] bug: crash\n📌 [n] news: a\n📌 [m] news: b"), meta(), T0).items
  assert.equal(footerText(fyi, {}), "📌 FYI")
  assert.deepEqual(sidebarLines(fyi, {}, 5), ["📌 Todo: nothing waits on you", "FYI: 1 bug, 2 news (/todo)"])
  assert.deepEqual(sidebarLines({}, {}, 5), ["📌 Todo: none open"])
})

const LONG = "the corner blocks overlap the minimap when the editor is narrower than 90 columns; pick: clip them, hide them, or move the minimap left of the gutter (my pick: clip)"

test("side panel: mark + tag on one line, text on at most 2 indented lines cut with …, each within the width", () => {
  const items = applyMarkers({}, parseMarkers(`📌 [block-corners] waits on you: ${LONG}\n📌 [short] news: ok`), meta(), T0).items
  for (const w of [SIDEBAR_W, 24, 60]) {
    const lines = sidebarLines(items, {}, 5, w)
    const i = lines.indexOf("⏳ [block-corners]")
    assert.ok(i > 0, `head line at width ${w}`)
    const end = lines.findIndex((l, n) => n > i && !l.startsWith("  "))
    const body = lines.slice(i + 1, end < 0 ? undefined : end)
    assert.ok(body.length >= 1 && body.length <= 2, `1-2 text lines at width ${w}, got ${body.length}`)
    for (const l of lines) assert.ok(textWidth(l) <= w, `"${l}" fits ${w}`)
    assert.ok(body.every((l) => l.startsWith("  ")))
    assert.ok(body.at(-1).endsWith("…"), "long text ends with …")
  }
  assert.equal(SIDEBAR_W, 36) // built-in sidebar is 42 wide: 2+2 padding, 1 gap, 1 scrollbar
})

// ---------------------------------------------------------------- width (what the dialog + slots measure)
test("marks: ⏳ waits on you, 🐞 bug, 📰 news; each is 2 columns wide (Emoji_Presentation, East Asian Wide)", () => {
  assert.deepEqual(MARKS, { "waits on you": "⏳", bug: "🐞", news: "📰" })
  for (const m of Object.values(MARKS)) assert.equal(textWidth(m), 2)
  assert.equal(textWidth("abc"), 3)
  assert.equal(textWidth("é"), 1) // combining accent
  assert.equal(textWidth("日本"), 4)
  assert.equal(textWidth("📌 [x] y"), 8)
})

test("cutTo / wrapTo: never wider than the limit, … only when cut, no split emoji", () => {
  assert.equal(cutTo("short", 10), "short")
  assert.equal(cutTo("exactly10!", 10), "exactly10!")
  assert.equal(cutTo("this is too long", 10), "this is t…")
  assert.equal(cutTo("ab🐞cd", 4), "ab…") // the 2-wide emoji would need columns 3-4, the … needs 4
  assert.equal(textWidth(cutTo("ab🐞cd", 5)), 5)
  assert.equal(cutTo("abc", 0), "")
  assert.deepEqual(wrapTo("one two three", 20, 2), ["one two three"])
  const w = wrapTo(LONG, 30, 2)
  assert.equal(w.length, 2)
  assert.ok(w.every((l) => textWidth(l) <= 30))
  assert.ok(w[1].endsWith("…"))
  assert.deepEqual(wrapTo("aaaaaaaaaaaaaaa", 6, 3), ["aaaaaa", "aaaaaa", "aaa"]) // a long word is hard-split
  assert.deepEqual(wrapTo("  a\n\nb  ", 10, 2), ["a b"]) // whitespace + newlines collapse
})

test("dialog widths match the v2.4.1 dialog: min(medium 60 | large 88, cols - 2); title = width - 13 - footer", () => {
  assert.equal(dialogWidth(80, "medium"), 60)
  assert.equal(dialogWidth(80, "large"), 78)
  assert.equal(dialogWidth(40, "large"), 38)
  assert.equal(dialogWidth(200, "large"), 88)
  assert.equal(titleWidth(80, ""), 65)
  assert.equal(titleWidth(80, "5m"), 62)
  assert.equal(titleWidth(40, "14d"), 21)
  assert.equal(dialogWidth(undefined, "large"), 78) // unknown terminal width -> 80
})

// ---------------------------------------------------------------- rename + walk me through
test("command name: /todo (no /news anywhere)", () => {
  assert.equal(SLASH, "todo")
})

const walkItems = () => {
  let items = applyMarkers({}, parseMarkers("📌 [old] news: oldest\n📌 [w] waits on you: approve"), meta(), T0).items
  items = applyMarkers(items, parseMarkers("📌 [b] bug: crash\n📌 [o] waits on you: pick one"), meta({ sessionID: "ses_b", sessionTitle: "other coord" }), T0 + 60e3).items
  items = applyMarkers(items, parseMarkers("📌 [done] news: finished"), meta(), T0 + 120e3).items
  return items
}

const ITEM_LINE = /^\d+\. \[/
test("walkPrompt: `question`-tool instruction first, then ONLY the waits-on-you items (newest first), (from …) for other sessions, no ids", () => {
  let items = walkItems()
  items = applyMarkers(items, parseMarkers("📌 [fresh] news: newest news"), meta(), T0 + 200e3).items
  const checks = checkItem({}, items.done, T0 + 130e3)
  const p = walkPrompt(items, checks, "ses_a", (sid) => (sid === "ses_b" ? "Other live title" : undefined))
  const lines = p.split("\n")
  assert.equal(lines[0], "Go through all my open 📌 items in ONE `question` tool call: one question per item that waits on me (your pick first, marked Recommended). After my answers, end your reply with one `📌 [tag] done` line per item my answers settled; skip any I left open.")
  assert.equal(lines[0], WALK_HEAD)
  assert.ok(!/bugs and news/.test(p))
  const list = lines.filter((l) => ITEM_LINE.test(l))
  assert.deepEqual(list, [
    "1. [o] waits on you: pick one (from Other live title)",
    "2. [w] waits on you: approve",
  ])
  assert.ok(!/ses_|msg_|\[done\]|\[b\]|\[old\]|\[fresh\]/.test(p)) // bugs + news are FYI: never in the walk
  assert.match(p, /^## Open 📌 items \(2\)$/m) // a heading: prompt-polish leaves structured text alone
})

test("walkPrompt: falls back to the stored title, then the session id; cut at ~30 with a count", () => {
  const items = walkItems()
  assert.match(walkPrompt(items, {}, "ses_a", () => undefined), /\[o\] waits on you: pick one \(from other coord\)/)
  const bare = { ...items, o: { ...items.o, sessionTitle: undefined } }
  assert.match(walkPrompt(bare, {}, "ses_a", () => undefined), /\(from ses_b\)/)
  const text = Array.from({ length: 45 }, (_, n) => `📌 [t${n}] waits on you: item ${n}`).join("\n")
  const big = applyMarkers({}, parseMarkers(text), meta(), T0).items
  const p = walkPrompt(big, {}, "ses_a")
  assert.equal(WALK_MAX, 30)
  assert.equal(p.split("\n").filter((l) => ITEM_LINE.test(l)).length, 30)
  assert.match(p, /^30\. \[t29\] waits on you: item 29$/m)
  assert.match(p, /^- … and 15 more$/m)
  assert.match(p, /^## Open 📌 items \(45\)$/m)
})

test("walkPrompt: nothing waits on you -> null (nothing to send), even with bugs + news open; walkRow + isLeftClick", () => {
  const items = walkItems()
  const all = Object.values(items).reduce((c, i) => checkItem(c, i, T0), {})
  assert.equal(walkPrompt(items, all, "ses_a"), null)
  assert.equal(walkPrompt({}, {}, "ses_a"), null)
  const waitsDone = checkItem(checkItem({}, items.w, T0), items.o, T0)
  assert.equal(walkPrompt(items, waitsDone, "ses_a"), null) // b, old, done still unchecked: FYI only
  const r = walkRow(3)
  assert.equal(r.value, WALK)
  assert.match(r.title, /walk me through all 3/)
  assert.ok(!/^[a-z0-9]/.test(WALK)) // can never equal a tag
  assert.equal(isLeftClick({ button: 0 }), true)
  assert.equal(isLeftClick(undefined), true) // no event info: treat as a click
  assert.equal(isLeftClick({ button: 2 }), false)
  assert.equal(isLeftClick({ button: 4 }), false) // wheel
})

test("rows: mark, tag, text on one line; footer = age; detail = full text + session + time", () => {
  const items = applyMarkers({}, parseMarkers("📌 [w] waits on you: approve"), meta(), T0).items
  const [row] = rowsOpen(items, {}, T0 + 5 * 60e3, () => "Coordinator", 80)
  assert.equal(row.value, "w")
  assert.equal(row.title, "⏳ [w] approve")
  assert.equal(row.truncateTitle, false) // we cut by display width ourselves; the built-in counts UTF-16 units
  assert.equal(row.category, "waits on you")
  assert.equal(row.footer, "5m")
  assert.equal(row.detail, "approve\nCoordinator · 5m ago")
  const checks = checkItem({}, items.w, T0 + 60e3)
  const [h] = rowsHistory(items, checks, T0 + 3 * 3600e3, () => undefined, 80)
  assert.equal(h.title, "⏳ [w] approve")
  assert.equal(h.footer, "✓ 2h")
  assert.equal(h.detail, "approve\ncoord · checked 2h ago") // falls back to the stored title
  const [t] = rowsToasts([{ at: T0, title: "news", message: "hello", variant: "info", source: "self" }], T0 + 30e3, 80)
  assert.match(t.title, /hello/)
  assert.match(t.footer, /30s/)
  assert.match(t.detail, /^news: hello\n/)
  assert.deepEqual(rowsOpen({}, {}, T0, () => "")[0].disabled, true) // empty list -> one disabled hint row
})

test("rows at 40 and 80 columns: one line each, clipped (no …) to the title width, full text in detail", () => {
  const items = applyMarkers({}, parseMarkers(`📌 [block-corners] waits on you: ${LONG}\n📌 [b] bug: crash 🐞 in the 日本 parser`), meta(), T0).items
  for (const cols of [40, 80]) {
    const rows = rowsOpen(items, {}, T0 + 14 * 864e5, () => "Coordinator", cols)
    for (const r of rows) {
      assert.ok(textWidth(r.title) <= titleWidth(cols, r.footer), `${cols}: "${r.title}"`)
      assert.ok(!r.title.includes("\n"))
    }
    const long = rows.find((r) => r.value === "block-corners")
    assert.ok(long.title.startsWith(cols === 40 ? "⏳ [block-corners] t" : "⏳ [block-corners] the corner blocks"))
    assert.ok(!long.title.includes("…"))
    assert.equal(textWidth(long.title), titleWidth(cols, long.footer)) // uses the whole row
    assert.equal(long.detail, `${LONG}\nCoordinator · 14d ago`)
  }
  assert.equal(rowsOpen(items, {}, T0, () => "", 80).find((r) => r.value === "b").title, "🐞 [b] crash 🐞 in the 日本 parser") // fits: no …
  // no width given -> 80 columns
  assert.equal(rowsOpen(items, {}, T0, () => "")[0].title, rowsOpen(items, {}, T0, () => "", 80)[0].title)
})

test("promptRoute + relTime", () => {
  assert.deepEqual(promptRoute({ tag: "x", sessionID: "ses_a" }), {
    type: "session", sessionID: "ses_a", prompt: { text: "[x] ", files: [], agents: [], pasted: [] },
  })
  assert.equal(relTime(T0 - 10e3, T0), "10s")
  assert.equal(relTime(T0 - 90 * 60e3, T0), "1h")
  assert.equal(relTime(T0 - 3 * 864e5, T0), "3d")
})

// ---------------------------------------------------------------- todo-click-check: inline scroll, done lines
test("clipTo: cut at the width with NO …, never splits an emoji", () => {
  assert.equal(clipTo("short", 10), "short")
  assert.equal(clipTo("this is too long", 10), "this is to")
  assert.equal(clipTo("ab🐞cd", 3), "ab") // the 2-wide emoji would need columns 3-4
  assert.equal(clipTo("ab🐞cd", 4), "ab🐞")
  assert.equal(clipTo("abc", 0), "")
})

test("scroll: the selected row slides its text by an offset at 40 and 80 cols; mark + tag stay put; no …", () => {
  const items = applyMarkers({}, parseMarkers(`📌 [block-corners] waits on you: ${LONG}\n📌 [b] bug: short`), meta(), T0).items
  assert.ok(SCROLL_STEP >= 4 && SCROLL_STEP <= 12)
  for (const cols of [40, 80]) {
    const rows = rowsOpen(items, {}, T0 + 60e3, () => "", cols)
    const r = rows.find((x) => x.value === "block-corners")
    const w = titleWidth(cols, r.footer)
    assert.equal(scrollTitle(r, 0), r.title)
    const max = maxScroll(r)
    const HEAD = "⏳ [block-corners] "
    const pinned = w - textWidth(HEAD) >= PIN_MIN // 80 cols: yes; 40 cols: only 2 columns left -> the whole line slides
    assert.equal(pinned, cols === 80)
    assert.equal(max, pinned ? textWidth(LONG) - (w - textWidth(HEAD)) : textWidth(HEAD + LONG) - w)
    for (const off of [0, SCROLL_STEP, 2 * SCROLL_STEP, max, max + 50]) {
      const t = scrollTitle(r, off)
      if (pinned) assert.ok(t.startsWith(HEAD), `${cols}/${off}: head pinned`)
      assert.ok(!t.includes("…"))
      assert.ok(textWidth(t) <= w, `${cols}/${off}: "${t}" fits ${w}`)
    }
    if (pinned) assert.equal(scrollTitle(r, SCROLL_STEP), HEAD + clipTo(LONG.slice(SCROLL_STEP), w - textWidth(HEAD)))
    else assert.equal(scrollTitle(r, SCROLL_STEP), clipTo([...(HEAD + LONG)].slice(SCROLL_STEP - 1).join(""), w)) // ⏳ is 2 wide = 1 code point
    assert.ok(scrollTitle(r, max).endsWith("(my pick: clip)")) // the end is reachable
    assert.equal(scrollTitle(r, max + 50), scrollTitle(r, max)) // clamped
    assert.equal(scrollTitle(r, -5), r.title)
    const s = rows.find((x) => x.value === "b")
    assert.equal(maxScroll(s), 0) // fits: nothing to scroll
    assert.equal(scrollTitle(s, 30), s.title)
  }
  // the walk row and hint rows don't scroll (and don't break)
  assert.equal(maxScroll(walkRow(3, 40)), 0)
  assert.ok(!walkRow(3, 20).title.includes("…"))
  assert.equal(scrollTitle({ title: "x" }, 4), "x")
})

test("history: a walk check shows 'walk' in the footer + detail; a manual one doesn't", () => {
  const items = applyMarkers({}, parseMarkers("📌 [w] waits on you: approve\n📌 [m] news: x"), meta(), T0).items
  let checks = checkItem({}, items.w, T0 + 60e3, "walk")
  checks = checkItem(checks, items.m, T0 + 60e3)
  assert.equal(checks.w.by, "walk")
  assert.equal("by" in checks.m, false)
  assert.equal(isOpen(items.w, checks.w), false) // `by` doesn't change open/closed
  const rows = rowsHistory(items, checks, T0 + 3 * 3600e3, () => undefined, 80)
  const w = rows.find((r) => r.value === "w"), m = rows.find((r) => r.value === "m")
  assert.equal(w.footer, "✓ walk 2h")
  assert.match(w.detail, /checked by walk 2h ago$/)
  assert.equal(m.footer, "✓ 2h")
})

test("walkTags: exactly the tags listed in the walk prompt (open, pop-up order, max 30)", () => {
  const items = walkItems()
  const checks = checkItem({}, items.done, T0)
  assert.deepEqual(walkTags(items, checks), ["o", "w"])
  const big = applyMarkers({}, parseMarkers(Array.from({ length: 45 }, (_, n) => `📌 [t${n}] waits on you: ${n}`).join("\n")), meta(), T0).items
  assert.equal(walkTags(big, {}).length, WALK_MAX)
})

test("parseDone: `📌 [tag] done` lines (bullets, bold, inline code, ': why' ok); fenced / other kinds / words are not", () => {
  const text = [
    "Settled:",
    "📌 [a] done",
    "- 📌 [B] done: you approved",
    "**📌 [c] done**",
    "`📌 [d] done`",
    "1. 📌 [e] done — fixed in 2.4.2",
    "📌 [f] done.",
    "📌 [g] news: done", // a news marker whose text is 'done'
    "📌 [h] doneish",
    "see 📌 [i] done", // not at line start
    "```",
    "📌 [j] done",
    "```",
    "📌 [Bad Tag] done",
    "📌 [a] done", // repeat: once
  ].join("\n")
  assert.deepEqual(parseDone(text).map((d) => d.tag), ["a", "b", "c", "d", "e", "f"])
  assert.deepEqual(parseDone(""), [])
  assert.deepEqual(parseDone(undefined), [])
  // done lines are never items
  assert.deepEqual(parseMarkers("📌 [a] done\n📌 [b] done: ok").map((m) => m.tag), [])
})

const WALK_AT = T0 + 100e3
const walkRec = { sid: "ses_a", tags: ["w", "b", "old"], at: WALK_AT }
const dn = (tag, over = {}) => ({ tag, sessionID: "ses_a", messageID: "msg_9", role: "assistant", at: WALK_AT + 5e3, ...over })

test("applyWalkDone: checks a walked tag from an assistant reply in the walk's session after the walk, by: walk", () => {
  const items = walkItems()
  const r = applyWalkDone({}, items, walkRec, [dn("w")], WALK_AT + 9e3)
  assert.deepEqual(r.checked, ["w"])
  assert.deepEqual(r.closed, [])
  assert.deepEqual(r.ignored, [])
  assert.deepEqual(r.checks.w, { at: WALK_AT + 9e3, kind: "waits on you", text: "approve", by: "walk" })
  assert.equal(isOpen(items.w, r.checks.w), false)
  // un-check (a click in history) removes it like any check
  assert.equal(uncheckItem(r.checks, "w").w, undefined)
})

test("applyWalkDone guards: wrong session, tag not in the walk, before the walk, a user message, no walk -> ignored", () => {
  const items = walkItems()
  const cases = [
    [dn("w", { sessionID: "ses_b" }), "other session"],
    [dn("nope"), "tag not in the walk"],
    [dn("o", { sessionID: "ses_a" }), "tag not in the walk"], // a waits item the walk didn't list
    [dn("w", { at: WALK_AT - 1 }), "before the walk"],
    [dn("w", { at: WALK_AT }), "before the walk"],
    [dn("w", { role: "user" }), "not an assistant message"],
    [dn("w", { role: undefined }), "not an assistant message"],
  ]
  for (const [d, why] of cases) {
    const r = applyWalkDone({}, items, walkRec, [d], WALK_AT + 9e3)
    assert.deepEqual(r.checked, [], why)
    assert.deepEqual(r.ignored, [{ tag: d.tag, why }])
    assert.deepEqual(r.checks, {})
  }
  assert.deepEqual(applyWalkDone({}, items, null, [dn("w")], 1).ignored, [{ tag: "w", why: "no walk sent" }])
  // already checked by hand: left alone (keeps the manual check), not counted
  const manual = checkItem({}, items.w, T0)
  const r = applyWalkDone(manual, items, walkRec, [dn("w")], WALK_AT + 9e3)
  assert.deepEqual(r.checked, [])
  assert.equal(r.checks.w.by, undefined)
  assert.equal(r.ignored[0].why, "already checked")
  // junk entries never throw
  assert.deepEqual(applyWalkDone({}, items, walkRec, [null, 5, {}], 1).checked, [])
})

test("FYI (bug / news): a done line from the item's OWN session closes it (by: ai), no walk needed; other session / user / older line -> ignored", () => {
  const items = walkItems() // old: news ses_a T0; b: bug ses_b T0+60s; done: news ses_a T0+120s
  const at = T0 + 300e3
  // no walk at all: still closes; the bug from ses_b closes from ses_b
  const r = applyWalkDone({}, items, null, [dn("old", { at }), dn("b", { sessionID: "ses_b", at })], at + 1)
  assert.deepEqual(r.closed, ["old", "b"])
  assert.deepEqual(r.checked, [])
  assert.deepEqual(r.checks.old, { at: at + 1, kind: "news", text: "oldest", by: "ai" })
  assert.equal(r.checks.b.by, "ai")
  // a walk record doesn't matter for FYI (tag not listed, other walk session)
  assert.deepEqual(applyWalkDone({}, items, { sid: "ses_z", tags: [], at: at + 99 }, [dn("old", { at })], at).closed, ["old"])
  const cases = [
    [dn("b", { at }), "other session"], // the bug came from ses_b, the line from ses_a
    [dn("old", { at, sessionID: "ses_b" }), "other session"],
    [dn("old", { at, role: "user" }), "not an assistant message"],
    [dn("done", { at: T0 + 119e3 }), "before the item"], // older than the item's last update
  ]
  for (const [d, why] of cases) {
    const x = applyWalkDone({}, items, walkRec, [d], at)
    assert.deepEqual([x.checked, x.closed, x.ignored], [[], [], [{ tag: d.tag, why }]], why)
  }
  // closed by the user already: left alone
  const m = applyWalkDone(checkItem({}, items.old, T0), items, null, [dn("old", { at })], at)
  assert.deepEqual([m.closed, m.ignored[0].why, m.checks.old.by], [[], "already checked", undefined])
})

test("rows: bugs + news sit in their own FYI groups under the waits; history footer says who closed it", () => {
  const items = walkItems()
  const rows = rowsOpen(items, {}, T0 + 300e3, () => "", 80)
  assert.deepEqual(rows.map((r) => r.value), ["o", "w", "b", "done", "old"])
  assert.deepEqual([...new Set(rows.map((r) => r.category))], ["waits on you", "bug · FYI", "news · FYI"])
  const checks = checkItem(checkItem({}, items.old, T0 + 1, "ai"), items.w, T0 + 1, "walk")
  const h = rowsHistory(items, checks, T0 + 3 * 3600e3, () => "", 80)
  assert.deepEqual(h.map((r) => r.footer).sort(), ["✓ ai 2h", "✓ walk 2h"])
})

test("pruneDones: newest first, 14 days, cap 200; doneKey is per message + tag + time", () => {
  const now = T0 + 20 * 864e5
  const list = [{ tag: "a", at: now - 1 }, { tag: "old", at: now - 15 * 864e5 }, ...Array.from({ length: 250 }, (_, n) => ({ tag: `t${n}`, at: now - 2 - n }))]
  const p = pruneDones(list, now)
  assert.equal(p.length, 200)
  assert.equal(p[0].tag, "a")
  assert.ok(!p.some((d) => d.tag === "old"))
  assert.notEqual(doneKey(dn("w")), doneKey(dn("w", { messageID: "msg_10" })))
  assert.equal(doneKey(dn("w")), doneKey(dn("w")))
})