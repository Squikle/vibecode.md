// node --test tests/   (tui.js with a fake OpenCode TUI context: /polish pop-up, toggles file, show overlay, crash safety)
import test from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync, mkdirSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import plugin, { setupPolishPop, readSettings, writeSettings } from "../../plugins/prompt-polish/tui.js"

const tmp = () => mkdtempSync(join(tmpdir(), "polish-pop-"))
const ORIG = "make it look to complex please, with agents diagram"
const FINAL = "make it look too complex please, with the agents diagram"
const polished = (id) => ({ id, type: "user", text: FINAL, metadata: { promptPolish: { original: ORIG, language: "en" } } })

/** picks: what each dialog.select call returns, in order (undefined = Esc) */
function fakeContext({ messages = {}, picks = [], sid = "ses_1" } = {}) {
  const toasts = [], layers = [], selects = [], shown = [], alerts = []
  const ctx = {
    toasts, layers, selects, shown, alerts, messages, route: { type: "session", sessionID: sid },
    ui: {
      toast: { show: (t) => toasts.push(t) },
      slot: () => { throw new Error("polish-pop must not use slots") },
      router: { current: () => ctx.route },
      dialog: {
        select: async (o) => { selects.push(o); return picks.shift() },
        show: (render, onClose) => shown.push({ render, onClose }),
        alert: async (o) => { alerts.push(o) },
        clear: () => {},
      },
    },
    keymap: { layer: (fn) => layers.push(fn()) },
    data: { session: { message: { list: (s) => ctx.messages[s] ?? [] } } },
  }
  return ctx
}
const cmd = (ctx, name) => ctx.layers[0].commands.find((c) => c.slash?.name === name)
const opts = (dir, extra) => ({ dir, interval: false, ...extra })

test("registers /polish (slash + palette) and /polish-show alias; both toggles ON with no file", () => {
  const dir = tmp()
  const ctx = fakeContext()
  const s = setupPolishPop(ctx, opts(dir))
  const c = cmd(ctx, "polish")
  assert.equal(c.slash.arguments, true); assert.equal(c.palette, true)
  assert.ok(cmd(ctx, "polish-show"), "alias kept")
  assert.deepEqual(s.settings(), { polish: true, show: true })
  assert.equal(existsSync(join(dir, "settings.json")), false) // no write until changed
  rmSync(dir, { recursive: true, force: true })
})

test("/polish opens the pop-up; Enter on a row toggles it, saves, reopens on that row; Esc closes", async () => {
  const dir = tmp()
  const ctx = fakeContext({ picks: ["show", "polish", undefined] })
  const s = setupPolishPop(ctx, opts(dir))
  await s.run("")
  assert.equal(ctx.selects.length, 3)
  assert.match(ctx.selects[0].title, /polish ON · show ON/)
  assert.match(ctx.selects[1].options[1].title, /^\[ \] show/)
  assert.equal(ctx.selects[1].current, "show")
  assert.match(ctx.selects[2].title, /polish OFF · show OFF/)
  assert.deepEqual(readSettings(dir), { polish: false, show: false })
  assert.equal(JSON.parse(readFileSync(join(dir, "settings.json"), "utf8")).polish, false) // what index.js reads
  rmSync(dir, { recursive: true, force: true })
})

test("/polish-show is the same pop-up; args work on both; junk -> usage warning", async () => {
  const dir = tmp()
  const ctx = fakeContext({ picks: [undefined] })
  setupPolishPop(ctx, opts(dir))
  cmd(ctx, "polish-show").run("")
  await new Promise((r) => setImmediate(r))
  assert.equal(ctx.selects.length, 1)
  cmd(ctx, "polish").run("off"); await new Promise((r) => setImmediate(r))
  assert.equal(readSettings(dir).polish, false)
  assert.match(ctx.toasts.at(-1).message, /polish OFF/)
  cmd(ctx, "polish-show").run("show off"); await new Promise((r) => setImmediate(r))
  assert.equal(readSettings(dir).show, false)
  cmd(ctx, "polish").run("bogus"); await new Promise((r) => setImmediate(r))
  assert.equal(ctx.toasts.at(-1).variant, "warning")
  // "restart": a fresh setup reads the file
  const s2 = setupPolishPop(fakeContext(), opts(dir))
  assert.deepEqual(s2.settings(), { polish: false, show: false })
  rmSync(dir, { recursive: true, force: true })
})

test("v1 show.json still honoured when settings.json is missing", () => {
  const dir = tmp()
  writeFileSync(join(dir, "show.json"), '{"show":false}')
  assert.deepEqual(readSettings(dir), { polish: true, show: false })
  writeSettings({ polish: true, show: true }, dir)
  assert.deepEqual(readSettings(dir), { polish: true, show: true })
  rmSync(dir, { recursive: true, force: true })
})

test("show ON: a newly polished prompt pops ONE toast with the corrected text; old ones don't; OFF = none", () => {
  const dir = tmp()
  const ctx = fakeContext({ messages: { ses_1: [polished("m_old")] } })
  const s = setupPolishPop(ctx, opts(dir))
  s.tick()
  assert.equal(ctx.toasts.length, 0) // message from before the TUI looked
  ctx.messages.ses_1 = [polished("m_old"), polished("m_new")]
  s.tick(); s.tick()
  assert.equal(ctx.toasts.length, 1)
  assert.equal(ctx.toasts[0].title, "✎ polished")
  assert.equal(ctx.toasts[0].message, "make it look t̶o̶ too complex please, with the agents diagram")
  assert.doesNotMatch(ctx.toasts[0].message + ctx.toasts[0].title, /edits?/)
  writeSettings({ polish: true, show: false }, dir)
  const ctx2 = fakeContext({ messages: { ses_1: [] } })
  const s2 = setupPolishPop(ctx2, opts(dir))
  s2.tick()
  ctx2.messages.ses_1 = [polished("m_3")]
  s2.tick()
  assert.equal(ctx2.toasts.length, 0)
  rmSync(dir, { recursive: true, force: true })
})

test("'show my last prompt' opens the colour diff dialog (JSX renderer), or a plain alert if it fails", async () => {
  const dir = tmp()
  const seen = []
  const ctx = fakeContext({ messages: { ses_1: [polished("m_1")] }, picks: ["last"] })
  const s = setupPolishPop(ctx, opts(dir, { renderDiff: async () => (c, v) => { seen.push(v); return "jsx" } }))
  await s.run("")
  assert.equal(ctx.selects[0].options[2].disabled, undefined)
  assert.equal(ctx.shown.length, 1)
  assert.equal(ctx.shown[0].render(), "jsx")
  assert.equal(seen[0].title, "✎ polished")
  const ctx2 = fakeContext({ messages: { ses_1: [polished("m_1")] }, picks: ["last"] })
  const s2 = setupPolishPop(ctx2, opts(dir, { renderDiff: async () => { throw new Error("no jsx") } }))
  await s2.run("")
  assert.equal(ctx2.shown.length, 0)
  assert.match(ctx2.alerts[0].message, /\[-to-\] \{\+too\+\}/)
  assert.match(readFileSync(join(dir, "tui.log"), "utf8"), /diff render: no jsx/)
  rmSync(dir, { recursive: true, force: true })
})

test("crash safety: broken store/toast/dialog/keymap never throw; errors go to tui.log", async () => {
  const dir = tmp()
  const ctx = fakeContext()
  ctx.data = { session: { message: { list: () => { throw new Error("store boom") } } } }
  ctx.ui.toast.show = () => { throw new Error("toast boom") }
  ctx.ui.dialog.select = async () => { throw new Error("dialog boom") }
  const s = setupPolishPop(ctx, opts(dir))
  s.tick()
  await s.run("")
  await s.run("status")
  const bad = fakeContext()
  bad.keymap.layer = () => { throw new Error("keymap boom") }
  assert.doesNotThrow(() => setupPolishPop(bad, opts(dir)))
  const log = readFileSync(join(dir, "tui.log"), "utf8")
  for (const w of ["store boom", "dialog boom", "toast boom", "keymap boom"]) assert.match(log, new RegExp(w))
  // the default export's setup never throws either, and returns a dispose
  const prev = process.env.PROMPT_POLISH_DATA
  process.env.PROMPT_POLISH_DATA = dir
  try {
    const d = plugin.setup({})
    assert.equal(typeof d, "function"); d()
  } finally { if (prev === undefined) delete process.env.PROMPT_POLISH_DATA; else process.env.PROMPT_POLISH_DATA = prev }
  rmSync(dir, { recursive: true, force: true })
})
