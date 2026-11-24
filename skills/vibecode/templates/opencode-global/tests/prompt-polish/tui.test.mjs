// node --test tests/   (tui.js with a fake OpenCode TUI context: command, toggle file, crash safety)
import test from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync, mkdirSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import plugin, { setupPolishShow, readShow, writeShow } from "../../plugins/prompt-polish/tui.js"

const tmp = () => mkdtempSync(join(tmpdir(), "polish-show-"))
const ORIG = "make it look to complex please, with agents diagram"
const FINAL = "make it look too complex please, with the agents diagram"

function fakeContext(messages = {}) {
  const toasts = [], layers = []
  return {
    toasts, layers,
    ui: { toast: { show: (t) => toasts.push(t) }, slot: () => () => {} },
    keymap: { layer: (fn) => layers.push(fn()) },
    data: { session: { message: { list: (sid) => messages[sid] ?? [] } } },
  }
}
const cmd = (ctx) => ctx.layers[0].commands[0]

test("registers /polish-show (slash + palette) and defaults to ON with no file", async () => {
  const dir = tmp()
  const ctx = fakeContext()
  const s = setupPolishShow(ctx, { dir, mount: () => () => {} })
  await s.mounting
  const c = cmd(ctx)
  assert.equal(c.slash.name, "polish-show"); assert.equal(c.slash.arguments, true); assert.equal(c.palette, true)
  assert.equal(s.api.show(), true)
  assert.equal(existsSync(join(dir, "show.json")), false) // no write until changed
  rmSync(dir, { recursive: true, force: true })
})

test("toggle persists across a restart; status reports without changing", async () => {
  const dir = tmp()
  const ctx = fakeContext()
  const s = setupPolishShow(ctx, { dir, mount: () => () => {} })
  const seen = []
  s.api.subscribe((v) => seen.push(v))
  cmd(ctx).run("")
  assert.equal(s.api.show(), false)
  assert.equal(JSON.parse(readFileSync(join(dir, "show.json"), "utf8")).show, false)
  assert.match(ctx.toasts.at(-1).message, /polish-show OFF/)
  // "restart": a fresh setup reads the file
  const ctx2 = fakeContext()
  const s2 = setupPolishShow(ctx2, { dir, mount: () => () => {} })
  assert.equal(s2.api.show(), false)
  cmd(ctx2).run("status")
  assert.equal(s2.api.show(), false)
  assert.match(ctx2.toasts.at(-1).message, /OFF/)
  cmd(ctx2).run("on")
  assert.equal(readShow(dir), true)
  assert.deepEqual(seen, [false])
  cmd(ctx2).run("bogus")
  assert.equal(ctx2.toasts.at(-1).variant, "warning")
  rmSync(dir, { recursive: true, force: true })
})

test("block(): shows for the polished latest message, nothing when off / unchanged / no session", () => {
  const dir = tmp()
  const msgs = { ses_A: [{ id: "m1", type: "user", text: FINAL, metadata: { promptPolish: { original: ORIG, language: "en", issues: 0 } } }], ses_B: [{ id: "m2", type: "user", text: "plain" }] }
  const ctx = fakeContext(msgs)
  const s = setupPolishShow(ctx, { dir, mount: () => () => {} })
  const v = s.api.block("ses_A")
  assert.equal(v.title, "✎ prompt-polish · 2 edits")
  assert.equal(s.api.block("ses_B"), null)
  assert.equal(s.api.block(undefined), null)
  cmd(ctx).run("off")
  assert.equal(s.api.block("ses_A"), null)
  rmSync(dir, { recursive: true, force: true })
})

test("crash safety: a throwing store / toast / slot logs to tui.log and never throws", async () => {
  const dir = tmp()
  const ctx = fakeContext()
  ctx.data = { session: { message: { list: () => { throw new Error("store boom") } } } }
  ctx.ui.toast.show = () => { throw new Error("toast boom") }
  const s = setupPolishShow(ctx, { dir, mount: () => { throw new Error("slot boom") } })
  await s.mounting
  assert.equal(s.api.block("ses_A"), null)
  assert.doesNotThrow(() => cmd(ctx).run("off"))
  assert.doesNotThrow(() => s.dispose())
  const log = readFileSync(join(dir, "tui.log"), "utf8")
  assert.match(log, /composer mount: slot boom/)
  assert.match(log, /block: store boom/)
  assert.match(log, /toast: toast boom/)
  rmSync(dir, { recursive: true, force: true })
})

test("crash safety: unwritable data dir still toggles in memory and warns", () => {
  const dir = tmp()
  const blocker = join(dir, "file")
  writeFileSync(blocker, "x") // a FILE where the data dir should be -> mkdir/write fail
  const ctx = fakeContext()
  const s = setupPolishShow(ctx, { dir: blocker, mount: () => () => {} })
  cmd(ctx).run("off")
  assert.equal(s.api.show(), false)
  assert.equal(ctx.toasts.at(-1).variant, "warning")
  assert.match(ctx.toasts.at(-1).message, /not saved/)
  rmSync(dir, { recursive: true, force: true })
})

test("default export: real setup with the JSX composer missing under node -> logs, no throw", async () => {
  const dir = tmp()
  process.env.PROMPT_POLISH_DATA = dir
  try {
    const ctx = fakeContext()
    const dispose = plugin.setup(ctx)
    assert.equal(typeof dispose, "function")
    assert.equal(plugin.id, "prompt-polish")
    // node can't load .jsx: the mount fails in the background and is logged
    for (let i = 0; i < 50 && !existsSync(join(dir, "tui.log")); i++) await new Promise((r) => setTimeout(r, 20))
    assert.match(readFileSync(join(dir, "tui.log"), "utf8"), /composer mount:/)
    dispose()
    // and a broken context (no keymap) doesn't throw either
    assert.doesNotThrow(() => plugin.setup({ ui: {} }))
  } finally {
    delete process.env.PROMPT_POLISH_DATA
    rmSync(dir, { recursive: true, force: true })
  }
})

test("writeShow is atomic-ish (tmp + rename) and readable", () => {
  const dir = tmp()
  mkdirSync(dir, { recursive: true })
  writeShow(false, dir)
  assert.equal(readShow(dir), false)
  assert.equal(existsSync(join(dir, "show.json.tmp")), false)
  rmSync(dir, { recursive: true, force: true })
})
