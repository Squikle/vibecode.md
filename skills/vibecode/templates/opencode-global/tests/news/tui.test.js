import { test } from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, readFileSync, writeFileSync, readdirSync, existsSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { setupNews } from "../../plugins/news/tui.js"

const T0 = Date.UTC(2026, 10, 24, 12, 0, 0)

function itemsFile(dir, items) {
  writeFileSync(join(dir, "items.json"), JSON.stringify({ v: 1, items }))
}
const item = (tag, over = {}) => ({ tag, kind: "news", text: `t-${tag}`, sessionID: "ses_a", messageID: "msg_1", sessionTitle: "coord", created: T0, updated: T0, ...over })

// a fake TUI context. `script` drives dialog.select: each entry gets the options and returns
// { pick: value } | { action: bind, value? } | { esc: true }. Every client access is RECORDED (calls.client),
// so a swallowed error can't hide a send: only the walk-me-through press may touch the client, and only
// client.session.prompt (calls.prompts). Checking must never touch a session (no prompt, no wake).
function fakeContext({ script = [], route = { type: "home" }, layerThrows = false, promptThrows = false, cols } = {}) {
  const calls = { toasts: [], navigate: [], selects: [], layers: [], on: {}, client: [], prompts: [], sizes: [] }
  let pending
  const ctx = {
    client: new Proxy({}, {
      get: (_, k) => {
        calls.client.push(String(k))
        if (k !== "session") throw new Error(`client.${String(k)} must not be used`)
        return { prompt: async (a) => { calls.prompts.push(a); if (promptThrows) throw new Error("server down"); return { data: {} } } }
      },
    }),
    data: {
      on: (type, h) => { calls.on[type] = h; return () => { delete calls.on[type] } },
      session: { get: (id) => (id === "ses_a" ? { id, title: "Coordinator" } : undefined) },
    },
    theme: {},
    ...(cols ? { renderer: { width: cols } } : {}),
    keymap: { layer: (f) => { if (layerThrows) throw new Error("no keymap"); calls.layers.push(f()) } },
    ui: {
      toast: { show: (o) => calls.toasts.push(o) },
      router: { current: () => route, navigate: (d) => calls.navigate.push(d) },
      slot: () => () => {},
      dialog: {
        clear: () => { const p = pending; pending = undefined; p?.(undefined) },
        ...(cols ? { set: (o) => calls.sizes.push({ size: o.size, after: calls.selects.length }) } : {}),
        select: (opts) => new Promise((resolve) => {
          calls.selects.push(opts)
          const step = script.shift() ?? { esc: true }
          const r = step(opts)
          if (r.esc) return resolve(undefined)
          if ("pick" in r) return resolve(r.pick)
          pending = resolve
          const a = opts.actions.find((x) => x.bind === r.action)
          if (!a) throw new Error(`no action ${r.action}`)
          a.selection === "none" ? a.onTrigger() : a.onTrigger(r.value)
          if (pending) { pending = undefined; resolve(undefined) } // action didn't close: treat as Escape
        }),
      },
    },
  }
  return { ctx, calls }
}

const setup = (dir, ctx, over = {}) => setupNews(ctx, { dir, now: () => T0 + 60e3, interval: 0, ui: false, clip: over.clip ?? (() => true), ...over })
const cmds = (calls) => calls.layers.flatMap((l) => l.commands)
const todoCmd = (calls) => cmds(calls).find((c) => c.slash?.name === "todo")
const WALK_ROW = "__walk__"

test("registers /todo (slash + palette); /news is gone (no alias); palette titles match", () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  const { ctx, calls } = fakeContext()
  setup(dir, ctx)
  const c = todoCmd(calls)
  assert.ok(c)
  assert.equal(c.palette, true)
  assert.equal(c.id, "todo.open")
  assert.match(c.title, /^todo: /)
  assert.ok(!c.slash.aliases?.length)
  for (const x of cmds(calls)) {
    assert.notEqual(x.slash?.name, "news")
    assert.ok(!(x.slash?.aliases ?? []).includes("news"))
    assert.match(x.title, /^todo: /)
    assert.ok(!/\/news/.test(x.description ?? ""))
  }
})

test("/todo lists open items under a walk row (cursor on the first item); ctrl+f checks; no client use", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a"), w: item("w", { kind: "waits on you", updated: T0 - 1 }) })
  const seen = []
  const { ctx, calls } = fakeContext({
    script: [
      (o) => { seen.push({ v: o.options.map((x) => x.value), current: o.current }); return { action: "ctrl+f", value: "a" } },
      (o) => { seen.push({ v: o.options.map((x) => x.value), current: o.current }); return { esc: true } },
    ],
  })
  const api = setup(dir, ctx)
  await api.open()
  assert.deepEqual(seen[0].v, [WALK_ROW, "w", "a"]) // walk button on top, then waits on you first
  assert.equal(seen[0].current, "w") // a plain Enter jumps to the top item, it never sends by accident
  assert.deepEqual(seen[1].v, [WALK_ROW, "w"])
  const j = JSON.parse(readFileSync(join(dir, "checks.json"), "utf8"))
  assert.equal(j.checks.a.text, "t-a")
  assert.equal(calls.navigate.length, 0)
  assert.deepEqual(calls.client, []) // checking never touches a session
})

test("only waits on you counts: title, walk row, footer; bugs + news are FYI rows under them (bugs first)", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { n: item("n", { updated: T0 + 9 }), g: item("g", { kind: "bug" }), w: item("w", { kind: "waits on you" }) })
  const seen = []
  const { ctx } = fakeContext({ script: [(o) => { seen.push(o); return { esc: true } }] })
  const api = setup(dir, ctx)
  await api.open()
  assert.equal(seen[0].title, "Todo · open: 1 waits on you · 2 FYI")
  assert.deepEqual(seen[0].options.map((x) => [x.value, x.category]), [[WALK_ROW, undefined], ["w", "waits on you"], ["g", "bug · FYI"], ["n", "news · FYI"]])
  assert.match(seen[0].options[0].title, /all 1 /)
  assert.equal(api.views.footer(), "📌 1 waits on you")
  assert.deepEqual(api.views.open().map((i) => i.tag), ["w"])
})

test("walk me through: ctrl+s sends ONE prompt to the session you're in, then closes", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a"), w: item("w", { kind: "waits on you", updated: T0 + 1 }), o: item("o", { kind: "waits on you", sessionID: "ses_o", sessionTitle: "Other" }) })
  const { ctx, calls } = fakeContext({ route: { type: "session", sessionID: "ses_a" }, script: [() => ({ action: "ctrl+s" })] })
  await setup(dir, ctx).open()
  assert.equal(calls.prompts.length, 1)
  assert.equal(calls.prompts[0].sessionID, "ses_a")
  const t = calls.prompts[0].text
  assert.match(t, /^Go through all my open 📌 items in ONE `question` tool call: /)
  assert.match(t, /^1\. \[w\] waits on you: t-w\n/m) // same session: no (from …)
  assert.match(t, /^2\. \[o\] waits on you: t-o \(from Other\)$/m)
  assert.ok(!/\[a\]/.test(t)) // news is FYI: never in the walk
  assert.ok(!/bugs and news/.test(t))
  assert.equal(calls.selects.length, 1) // closed after sending
  assert.ok(calls.toasts.some((x) => /sent/i.test(x.message)))
  assert.equal(calls.navigate.length, 0)
})

test("walk me through: Enter on the walk row does the same", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a", { kind: "waits on you" }) })
  const { ctx, calls } = fakeContext({ route: { type: "session", sessionID: "ses_x" }, script: [() => ({ pick: WALK_ROW })] })
  await setup(dir, ctx).open()
  assert.equal(calls.prompts.length, 1)
  assert.equal(calls.prompts[0].sessionID, "ses_x")
  assert.match(calls.prompts[0].text, /\[a\] waits on you: t-a \(from Coordinator\)/) // live title via data.session.get
  assert.equal(calls.navigate.length, 0)
})

test("walk me through: empty list -> toast, nothing sent; no session open -> toast, nothing sent", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a") })
  writeFileSync(join(dir, "checks.json"), JSON.stringify({ v: 1, checks: { a: { at: T0, kind: "news", text: "t-a" } } }))
  let r = fakeContext({ route: { type: "session", sessionID: "ses_a" }, script: [(o) => { assert.ok(!o.options.some((x) => x.value === WALK_ROW)); return { action: "ctrl+s" } }] })
  await setup(dir, r.ctx).open()
  assert.equal(r.calls.prompts.length, 0)
  assert.deepEqual(r.calls.client, [])
  assert.ok(r.calls.toasts.some((x) => /nothing open/i.test(x.message)))
  // only FYI (bug + news) open: no walk row either, ctrl+s sends nothing
  itemsFile(dir, { n: item("n"), g: item("g", { kind: "bug" }) })
  r = fakeContext({ route: { type: "session", sessionID: "ses_a" }, script: [(o) => { assert.ok(!o.options.some((x) => x.value === WALK_ROW)); return { action: "ctrl+s" } }] })
  await setup(dir, r.ctx).open()
  assert.equal(r.calls.prompts.length, 0)
  assert.ok(r.calls.toasts.some((x) => /nothing open that waits on you/i.test(x.message)))
  itemsFile(dir, { b: item("b", { kind: "waits on you" }) })
  r = fakeContext({ route: { type: "home" }, script: [() => ({ action: "ctrl+s" })] })
  await setup(dir, r.ctx).open()
  assert.equal(r.calls.prompts.length, 0)
  assert.deepEqual(r.calls.client, [])
  assert.ok(r.calls.toasts.some((x) => /open a session/i.test(x.message)))
})

test("walk me through: send fails -> whole text to the clipboard + toast, no throw", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a", { kind: "waits on you" }) })
  const clips = []
  const { ctx, calls } = fakeContext({ promptThrows: true, route: { type: "session", sessionID: "ses_a" }, script: [() => ({ action: "ctrl+s" })] })
  await setup(dir, ctx, { clip: (t) => { clips.push(t); return true } }).open()
  assert.equal(calls.prompts.length, 1)
  assert.match(clips[0] ?? "", /^Go through all my open 📌 items in ONE `question` tool call/)
  assert.ok(calls.toasts.some((x) => /failed/i.test(x.message) && /Ctrl\+V/.test(x.message)))
})

test("press-only guard: setup, tick (new-item toasts), views, history/toast views, click-check, ctrl+o never touch the client", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a") })
  const { ctx, calls } = fakeContext({
    route: { type: "session", sessionID: "ses_a" },
    script: [() => ({ action: "ctrl+n" }), () => ({ action: "ctrl+n" }), () => ({ action: "ctrl+n" }), () => ({ pick: "a" }), () => ({ action: "ctrl+o", value: "b" })],
  })
  const api = setup(dir, ctx)
  itemsFile(dir, { a: item("a"), b: item("b", { updated: T0 + 5 }) })
  const f = join(dir, "items.json"); const { utimesSync } = await import("node:fs"); utimesSync(f, new Date(), new Date(Date.now() + 5000))
  api.tick()
  api.views.footer(); api.views.sidebar(); api.views.open()
  await api.open() // open -> history -> toasts -> open -> Enter on an item (checks it) -> ctrl+o (jumps, no send)
  assert.ok(calls.toasts.some((x) => /\[b\]/.test(x.message))) // the tick did toast
  assert.equal(calls.navigate.length, 1)
  assert.deepEqual(calls.client, [])
  assert.equal(calls.prompts.length, 0)
})

test("footer click: views.click() opens the pop-up once (a second click while open is ignored); no send", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a") })
  let release
  const { ctx, calls } = fakeContext({ route: { type: "session", sessionID: "ses_a" }, script: [() => new Promise(() => {}) && { esc: true }] })
  // hold the first dialog open until we say so
  ctx.ui.dialog.select = (opts) => { calls.selects.push(opts); return new Promise((r) => { release = r }) }
  const api = setup(dir, ctx)
  api.views.click({ button: 0 })
  api.views.click({ button: 0 })
  api.views.click({ button: 2 }) // right click: ignored
  await new Promise((r) => setImmediate(r))
  assert.equal(calls.selects.length, 1)
  release(undefined) // Escape
  await new Promise((r) => setImmediate(r))
  api.views.click({ button: 0 })
  await new Promise((r) => setImmediate(r))
  assert.equal(calls.selects.length, 2)
  release(undefined)
  assert.equal(calls.prompts.length, 0)
  assert.deepEqual(calls.client, [])
})

test("history view toggle (ctrl+n) shows checked items; ctrl+f there unchecks", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a") })
  writeFileSync(join(dir, "checks.json"), JSON.stringify({ v: 1, checks: { a: { at: T0, kind: "news", text: "t-a" } } }))
  const titles = []
  const { ctx } = fakeContext({
    script: [
      (o) => { titles.push(o.title); return { action: "ctrl+n" } },
      (o) => { titles.push(o.title); assert.deepEqual(o.options.map((x) => x.value), ["a"]); return { action: "ctrl+f", value: "a" } },
      (o) => { titles.push(o.title); return { esc: true } },
    ],
  })
  await setup(dir, ctx).open()
  assert.match(titles[0], /open/i)
  assert.match(titles[1], /history/i)
  const j = JSON.parse(readFileSync(join(dir, "checks.json"), "utf8"))
  assert.equal(j.checks.a, undefined)
})

// a click on a row and Enter both reach the select's onSelect -> the pop-up gets the picked value
test("click / Enter on an item checks it; the pop-up stays open with the cursor on the next item; no nav, no send", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  const wy = { kind: "waits on you" }
  itemsFile(dir, { a: item("a", { ...wy, updated: T0 + 3 }), b: item("b", { ...wy, updated: T0 + 2 }), c: item("c", { ...wy, updated: T0 + 1 }) })
  const seen = []
  const look = (o) => seen.push({ v: o.options.map((x) => x.value), current: o.current })
  const { ctx, calls } = fakeContext({
    script: [
      (o) => { look(o); return { pick: "b" } }, // middle item -> cursor to the one below it
      (o) => { look(o); return { pick: "c" } }, // last item -> cursor to the one above it
      (o) => { look(o); return { pick: "a" } }, // only item -> nothing left
      (o) => { look(o); return { esc: true } },
    ],
  })
  await setup(dir, ctx).open()
  assert.deepEqual(seen[0], { v: [WALK_ROW, "a", "b", "c"], current: "a" })
  assert.deepEqual(seen[1], { v: [WALK_ROW, "a", "c"], current: "c" })
  assert.deepEqual(seen[2], { v: [WALK_ROW, "a"], current: "a" })
  assert.equal(seen[3].v.length, 1) // "nothing open" hint
  assert.equal(calls.selects.length, 4) // stayed open after every check
  const j = JSON.parse(readFileSync(join(dir, "checks.json"), "utf8"))
  assert.deepEqual(Object.keys(j.checks).sort(), ["a", "b", "c"])
  assert.equal(j.checks.b.by, undefined) // a manual check
  assert.equal(calls.navigate.length, 0)
  assert.deepEqual(calls.client, [])
})

test("ctrl+f also keeps the cursor on the next item", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a", { updated: T0 + 3 }), b: item("b", { updated: T0 + 2 }), c: item("c", { updated: T0 + 1 }) })
  const cur = []
  const { ctx } = fakeContext({ script: [() => ({ action: "ctrl+f", value: "a" }), (o) => { cur.push(o.current); return { esc: true } }] })
  await setup(dir, ctx).open()
  assert.deepEqual(cur, ["b"])
})

test("history: click / Enter un-checks the item and stays open", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a"), b: item("b") })
  writeFileSync(join(dir, "checks.json"), JSON.stringify({ v: 1, checks: { a: { at: T0 + 2, kind: "news", text: "t-a", by: "walk" }, b: { at: T0 + 1, kind: "news", text: "t-b" } } }))
  const seen = []
  const { ctx, calls } = fakeContext({
    script: [() => ({ action: "ctrl+n" }), (o) => { seen.push(o.options.map((x) => x.value)); return { pick: "a" } }, (o) => { seen.push({ v: o.options.map((x) => x.value), current: o.current, title: o.title }); return { esc: true } }],
  })
  await setup(dir, ctx).open()
  assert.deepEqual(seen[0], ["a", "b"])
  assert.deepEqual(seen[1].v, ["b"])
  assert.equal(seen[1].current, "b")
  assert.match(seen[1].title, /history/i)
  const j = JSON.parse(readFileSync(join(dir, "checks.json"), "utf8"))
  assert.equal(j.checks.a, undefined)
  assert.ok(j.checks.b)
  assert.equal(calls.navigate.length, 0)
})

test("ctrl+o opens the item's session (the old Enter); shown in the key hints; not in the toast log view", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a", { sessionID: "ses_o" }) })
  const binds = []
  const { ctx, calls } = fakeContext({ script: [(o) => { binds.push(o.actions.map((x) => [x.bind, x.title])); return { action: "ctrl+o", value: "a" } }] })
  await setup(dir, ctx).open()
  assert.deepEqual(calls.navigate, [{ type: "session", sessionID: "ses_o" }])
  assert.equal(calls.selects.length, 1) // closes: you're going somewhere
  const o = binds[0].find(([b]) => b === "ctrl+o")
  assert.match(o[1], /open session/)
  assert.ok(!existsSync(join(dir, "checks.json"))) // opening doesn't check
  let r = fakeContext({ script: [() => ({ action: "ctrl+n" }), (x) => { binds.push(x.actions.map((a) => a.bind)); return { action: "ctrl+o", value: "a" } }] })
  writeFileSync(join(dir, "checks.json"), JSON.stringify({ v: 1, checks: { a: { at: T0, kind: "news", text: "t-a" } } }))
  await setup(dir, r.ctx).open() // history: ctrl+o works there too
  assert.deepEqual(r.calls.navigate, [{ type: "session", sessionID: "ses_o" }])
  r = fakeContext({ script: [() => ({ action: "ctrl+n" }), () => ({ action: "ctrl+n" }), (x) => { binds.push(x.actions.map((a) => a.bind)); return { esc: true } }] })
  await setup(dir, r.ctx).open()
  assert.ok(!binds.at(-1).includes("ctrl+o"))
})

const LONGT = "the corner blocks overlap the minimap when the editor is narrower than 90 columns; pick: clip them, hide them, or move the minimap (my pick: clip)"
const plainSignal = (v) => { let x = v; return [() => x, (n) => { x = typeof n === "function" ? n(x) : n }] }

test("inline scroll (live signal): →/← slide the selected row, home resets, other rows stay; dialog stays up", async () => {
  for (const cols of [40, 80]) {
    const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
    itemsFile(dir, { w: item("w", { kind: "waits on you", text: LONGT }), b: item("b", { text: LONGT }) })
    const got = {}
    const { ctx, calls } = fakeContext({
      cols,
      script: [(o) => {
        const act = (bind, v) => o.actions.find((a) => a.bind === bind).onTrigger(v)
        const row = (v) => o.options.find((x) => x.value === v)
        got.t0 = row("w").titleView()
        act("right", "w"); got.t1 = row("w").titleView(); got.b1 = row("b").titleView()
        act("right", "w"); got.t2 = row("w").titleView()
        act("left", "w"); got.t3 = row("w").titleView()
        for (let n = 0; n < 50; n++) act("right", "w")
        got.end = row("w").titleView()
        act("home", "w"); got.t4 = row("w").titleView()
        act("right", "b"); got.w5 = row("w").titleView(); got.b5 = row("b").titleView() // another row: the first resets
        got.binds = o.actions.map((a) => a.bind)
        return { esc: true }
      }],
    })
    await setup(dir, ctx, { createSignal: plainSignal }).open()
    const w = titleWidthOf(cols, "1m")
    assert.equal(calls.selects.length, 1, "scrolling never re-opens the dialog")
    assert.ok(got.t0.startsWith("⏳ [w] the corner"))
    assert.ok(got.t1.startsWith("⏳ [w] ") && !got.t1.startsWith("⏳ [w] the corner"), `${cols}: ${got.t1}`)
    assert.notEqual(got.t2, got.t1)
    assert.equal(got.t3, got.t1)
    assert.ok(got.end.endsWith("(my pick: clip)"), `${cols}: ${got.end}`)
    assert.equal(got.t4, got.t0)
    assert.equal(got.w5, got.t0)
    assert.notEqual(got.b5, got.b1)
    for (const t of [got.t0, got.t1, got.t2, got.end, got.b5]) {
      assert.ok(!t.includes("…"))
      assert.ok([...t].length <= w, `${cols}: "${t}" fits ${w}`)
    }
    for (const k of ["left", "right", "home", "ctrl+o", "ctrl+f"]) assert.ok(got.binds.includes(k), k)
  }
})

// 1 bar + 3 pad + title + 1 + footer + 4 = width - 4 (list) ... as titleWidth() in news-core
const titleWidthOf = (cols, footer) => Math.min(88, cols - 2) - 13 - (footer.length + 1)

test("inline scroll fallback (no live signal): → re-shows the pop-up with that row slid, cursor kept", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { w: item("w", { kind: "waits on you", text: LONGT }), b: item("b") })
  const got = []
  const { ctx } = fakeContext({
    cols: 80,
    script: [
      (o) => { got.push(o.options.find((x) => x.value === "w").title); return { action: "right", value: "w" } },
      (o) => { got.push(o.options.find((x) => x.value === "w").title, o.current); return { action: "home", value: "w" } },
      (o) => { got.push(o.options.find((x) => x.value === "w").title); return { esc: true } },
    ],
  })
  await setup(dir, ctx).open()
  assert.ok(got[0].startsWith("⏳ [w] the corner"))
  assert.ok(!got[1].startsWith("⏳ [w] the corner") && got[1].startsWith("⏳ [w] "))
  assert.equal(got[2], "w")
  assert.equal(got[3], got[0])
})

// ---------------------------------------------------------------- walk -> 📌 [tag] done -> checked
const donesFile = (dir, dones) => writeFileSync(join(dir, "dones.json"), JSON.stringify({ v: 1, dones }))
const bump = async (f, s = 5) => { const { utimesSync } = await import("node:fs"); utimesSync(f, new Date(), new Date(Date.now() + s * 1000)) }

test("walk records {sid, tags, at} before sending; a later `📌 [tag] done` from that session checks it (by: walk) + toast", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a"), w: item("w", { kind: "waits on you" }), x: item("x") })
  const { ctx, calls } = fakeContext({ route: { type: "session", sessionID: "ses_a" }, script: [() => ({ action: "ctrl+s" })] })
  let t = T0 + 60e3
  const api = setupNews(ctx, { dir, now: () => t, interval: 0, ui: false, clip: () => true })
  await api.open()
  const wk = JSON.parse(readFileSync(join(dir, "walk.json"), "utf8")).walk
  assert.deepEqual(wk, { sid: "ses_a", tags: ["w"], at: T0 + 60e3 }) // waits only: news is never walked
  t = T0 + 90e3
  donesFile(dir, [
    { tag: "w", sessionID: "ses_a", messageID: "msg_r", role: "assistant", at: T0 + 80e3 },
    { tag: "a", sessionID: "ses_a", messageID: "msg_r", role: "assistant", at: T0 + 80e3 }, // news from ses_a: the AI closes it
  ])
  api.tick()
  const c = JSON.parse(readFileSync(join(dir, "checks.json"), "utf8")).checks
  assert.equal(c.w.by, "walk")
  assert.equal(c.a.by, "ai")
  assert.equal(c.x, undefined)
  assert.ok(calls.toasts.some((x) => x.message === "walk: 1 checked · ai: 1 closed"))
  assert.match(readFileSync(join(dir, "tui.log"), "utf8"), /walk-done \{"tags":\["w"\],"closed":\["a"\]/)
  // un-check one in history (a click); the same done line must not re-check it on later ticks
  const r = fakeContext({ script: [() => ({ action: "ctrl+n" }), () => ({ pick: "w" })] })
  await setupNews(r.ctx, { dir, now: () => t, interval: 0, ui: false }).open()
  assert.equal(JSON.parse(readFileSync(join(dir, "checks.json"), "utf8")).checks.w, undefined)
  await bump(join(dir, "dones.json"))
  api.tick(); api.tick()
  // even a fresh TUI (hot reload) remembers it was handled
  setupNews(fakeContext().ctx, { dir, now: () => t, interval: 0, ui: false }).tick()
  assert.equal(JSON.parse(readFileSync(join(dir, "checks.json"), "utf8")).checks.w, undefined)
  assert.equal(calls.toasts.filter((x) => /^walk: /.test(x.message)).length, 1)
})

test("walk done guards in the TUI: wrong session, not in the walk, before the walk, user message -> nothing checked, logged", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { v: item("v", { kind: "waits on you" }), w: item("w", { kind: "waits on you" }) })
  writeFileSync(join(dir, "walk.json"), JSON.stringify({ v: 1, walk: { sid: "ses_a", tags: ["w"], at: T0 + 50e3 }, seen: [] }))
  const { ctx, calls } = fakeContext()
  donesFile(dir, [
    { tag: "w", sessionID: "ses_b", messageID: "m1", role: "assistant", at: T0 + 55e3 },
    { tag: "v", sessionID: "ses_a", messageID: "m2", role: "assistant", at: T0 + 55e3 },
    { tag: "w", sessionID: "ses_a", messageID: "m3", role: "assistant", at: T0 + 40e3 },
    { tag: "w", sessionID: "ses_a", messageID: "m4", role: "user", at: T0 + 55e3 },
  ])
  const api = setup(dir, ctx)
  api.tick(); api.tick()
  assert.ok(!existsSync(join(dir, "checks.json")))
  assert.ok(!calls.toasts.some((x) => /^walk: /.test(x.message)))
  const log = readFileSync(join(dir, "tui.log"), "utf8")
  for (const why of ["other session", "tag not in the walk", "before the walk", "not an assistant message"]) {
    assert.equal(log.split("\n").filter((l) => l.includes("walk-done-ignored") && l.includes(why)).length, 1, why) // logged once
  }
  assert.deepEqual(calls.client, [])
})

test("ctrl+y: other session -> navigate with prompt prefill; same session -> clipboard + toast", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a") })
  let r = fakeContext({ script: [() => ({ action: "ctrl+y", value: "a" })] })
  await setup(dir, r.ctx).open()
  assert.equal(r.calls.navigate[0].prompt.text, "[a] ")
  const clips = []
  r = fakeContext({ route: { type: "session", sessionID: "ses_a" }, script: [() => ({ action: "ctrl+y", value: "a" })] })
  await setup(dir, r.ctx, { clip: (t) => { clips.push(t); return true } }).open()
  assert.deepEqual(clips, ["[a] "])
  assert.equal(r.calls.navigate.length, 0)
  assert.ok(r.calls.toasts.some((t) => /Ctrl\+V/i.test(t.message)))
})

test("toast log: bus tui.toast.show + own toasts land in toasts.json; toasts view lists them", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, {})
  const { ctx, calls } = fakeContext({
    script: [() => ({ action: "ctrl+n" }), () => ({ action: "ctrl+n" }), (o) => { assert.ok(o.options.some((x) => /from bus/.test(x.title))); return { esc: true } }],
  })
  const api = setup(dir, ctx)
  calls.on["tui.toast.show"]({ type: "tui.toast.show", data: { title: "srv", message: "from bus", variant: "info" } })
  await api.open()
  const j = JSON.parse(readFileSync(join(dir, "toasts.json"), "utf8"))
  assert.equal(j.toasts[0].message, "from bus")
  assert.equal(j.toasts[0].source, "bus")
})

test("poll: a new open item toasts once (with sessionID) and is logged as own toast; baseline silent", () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { a: item("a") })
  const { ctx, calls } = fakeContext()
  const api = setup(dir, ctx)
  api.tick()
  assert.equal(calls.toasts.length, 0)
  itemsFile(dir, { a: item("a"), b: item("b", { kind: "bug", text: "crash" }) })
  api.tick()
  api.tick()
  assert.equal(calls.toasts.length, 1)
  assert.equal(calls.toasts[0].sessionID, "ses_a")
  assert.match(calls.toasts[0].message, /\[b\] crash/)
  const j = JSON.parse(readFileSync(join(dir, "toasts.json"), "utf8"))
  assert.equal(j.toasts[0].source, "self")
})

test("fail-open: keymap throws, files missing/corrupt, clip throws -> no throw", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  writeFileSync(join(dir, "items.json"), "{corrupt")
  const { ctx } = fakeContext({ layerThrows: true, script: [() => ({ action: "ctrl+y", value: "nope" })] })
  const api = setup(dir, ctx, { clip: () => { throw new Error("no clip") } })
  api.tick()
  await api.open()
  api.dispose()
  // the TUI never writes items.json: a corrupt file is left for the server (its only writer)
  assert.ok(!readdirSync(dir).some((f) => f.startsWith("items.json.bad")))
  assert.equal(readFileSync(join(dir, "items.json"), "utf8"), "{corrupt")
})

test("pop-up rows: cut to the terminal width (large dialog), detail = full text, truncateTitle off, size set after each select", async () => {
  const LONG = "x".repeat(20) + " " + "lorem ipsum dolor sit amet ".repeat(6)
  for (const cols of [40, 80]) {
    const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
    itemsFile(dir, { w: item("w", { kind: "waits on you", text: LONG }), b: item("b", { kind: "bug", text: "short" }) })
    const opts = []
    const { ctx, calls } = fakeContext({ cols, script: [(o) => { opts.push(o); return { action: "ctrl+n" } }, (o) => { opts.push(o); return { esc: true } }] })
    await setup(dir, ctx).open()
    const rows = opts[0].options.filter((o) => o.value !== WALK_ROW)
    const w = rows.find((r) => r.value === "w")
    assert.ok(w.title.startsWith("⏳ [w] xxxx"))
    assert.ok(!w.title.includes("…"))
    const dw = Math.min(88, cols - 2)
    assert.ok([...w.title].length <= dw - 13 - (w.footer.length + 1), `${cols}: ${w.title}`)
    assert.equal(w.truncateTitle, false)
    assert.match(w.detail, new RegExp(`^${LONG}\\nCoordinator · `))
    assert.equal(rows.find((r) => r.value === "b").title, "🐞 [b] short")
    // dialog.select resets the size to medium on every open: set large right after each select call
    assert.deepEqual(calls.sizes, [{ size: "large", after: 1 }, { size: "large", after: 2 }])
  }
})

test("pop-up without renderer width or dialog.set still works (80-column fallback)", async () => {
  const dir = mkdtempSync(join(tmpdir(), "news-tui-"))
  itemsFile(dir, { w: item("w", { kind: "waits on you", text: "y ".repeat(80) }) })
  const opts = []
  const { ctx } = fakeContext({ script: [(o) => { opts.push(o); return { esc: true } }] })
  await setup(dir, ctx).open()
  const w = opts[0].options.find((o) => o.value === "w")
  assert.ok(!w.title.includes("…"))
  assert.ok([...w.title].length <= 78 - 13)
})
