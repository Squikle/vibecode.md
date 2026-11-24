// node --test tests/   (server index.js <-> TUI contract: the hook's output is what polish-pop reads; the "show"
// toggle never changes what reaches the model; the "polish" toggle turns the hook off with 0 model calls)
import test from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, rmSync, readFileSync, writeFileSync, readdirSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import server from "../../plugins/prompt-polish/index.js"
import { latestPolish, overlay, overlayText, toastText } from "../../plugins/prompt-polish/polish-core.js"
import { setupPolishPop, writeSettings } from "../../plugins/prompt-polish/tui.js"

const ORIG = "is the readme ok? kinda merge optimizations with agents diagram, or make another one if changing it makes it look to complex"
const POLISHED = "Is the readme ok? Merge optimizations with the agents diagram, or make another one if changing it makes it look too complex"

/** runs the real hook once. settings: undefined = no settings.json; object = written first; string = raw file */
async function runHook(reply, { text = ORIG, settings } = {}) {
  const cfg = mkdtempSync(join(tmpdir(), "polish-cfg-"))
  const data = mkdtempSync(join(tmpdir(), "polish-data-"))
  const old = { x: process.env.XDG_CONFIG_HOME, d: process.env.PROMPT_POLISH_DATA }
  process.env.XDG_CONFIG_HOME = cfg // no command templates on disk
  process.env.PROMPT_POLISH_DATA = data
  if (typeof settings === "string") writeFileSync(join(data, "settings.json"), settings)
  else if (settings) writeSettings(settings, data)
  let hook
  const calls = []
  const ctx = {
    location: { directory: cfg },
    session: {
      hook: async (name, fn) => { assert.equal(name, "prompt"); hook = fn },
      get: async () => ({ model: { providerID: "anthropic", id: "claude-opus-5-5" } }),
      context: async () => [],
    },
    experimental: { generate: { text: async (req) => { calls.push(req); return { text: JSON.stringify(reply) } } } },
  }
  try {
    await server.setup(ctx)
    const event = { sessionID: "ses_X", prompt: { text, files: [], agents: [] }, metadata: undefined }
    await hook(event)
    return { event, calls, files: readdirSync(data) }
  } finally {
    for (const [k, v] of [["XDG_CONFIG_HOME", old.x], ["PROMPT_POLISH_DATA", old.d]]) if (v === undefined) delete process.env[k]; else process.env[k] = v
    rmSync(cfg, { recursive: true, force: true })
    rmSync(data, { recursive: true, force: true })
  }
}
const OK = { language: "en", prompt: POLISHED, issues: [], questions: [] }

// the TUI store entry = {id, type:"user", ...payload} where payload = {text, files, agents, metadata}
const storeEntry = (event) => ({ id: "msg_1", type: "user", text: event.prompt.text, files: event.prompt.files, agents: event.prompt.agents, metadata: event.metadata })

test("polished prompt: exactly 1 model call; metadata.promptPolish.original = typed text; overlay diffs it", async () => {
  const { event, calls } = await runHook(OK)
  assert.equal(calls.length, 1)
  assert.equal(event.prompt.text, POLISHED)
  assert.equal(event.metadata.promptPolish.original, ORIG)
  assert.deepEqual(Object.keys(event.metadata.promptPolish).sort(), ["followup", "issues", "language", "model", "original"])
  const v = overlay(latestPolish([storeEntry(event)]))
  assert.equal(v.title, "✎ polished")
  assert.match(overlayText(v), /\[-to-\] \{\+too\+\}/)
  assert.match(toastText(v), /t̶o̶ too complex/)
})

test("HARD RULE: show ON vs OFF -> model input and stored message byte-identical", async () => {
  const runs = []
  for (const settings of [undefined, { polish: true, show: true }, { polish: true, show: false }]) runs.push(await runHook(OK, { settings }))
  const bytes = (r) => JSON.stringify({ event: r.event, calls: r.calls })
  assert.equal(bytes(runs[1]), bytes(runs[0]))
  assert.equal(bytes(runs[2]), bytes(runs[0]))
  for (const r of runs) assert.equal(r.calls.length, 1)
  assert.equal(runs[0].files.length, 0) // the server never writes the data dir
})

test("polish OFF: text, files, metadata untouched and 0 model calls; junk settings file = ON", async () => {
  const off = await runHook(OK, { settings: { polish: false, show: true } })
  assert.equal(off.calls.length, 0)
  assert.deepEqual(off.event, { sessionID: "ses_X", prompt: { text: ORIG, files: [], agents: [] }, metadata: undefined })
  for (const raw of ["{", "", '{"polish":"no"}', "null"]) assert.equal((await runHook(OK, { settings: raw })).calls.length, 1, raw)
})

test("conflict flagged: the appended note is the overlay's note, not a diffed edit", async () => {
  const { event } = await runHook({ language: "en", prompt: POLISHED, issues: ["Wants one diagram and two."], questions: ["One or two?"] })
  assert.match(event.prompt.text, /\n\n\[prompt-check, auto\] Possible conflict: Wants one diagram and two\. Ask: One or two\?/)
  const v = overlay(latestPolish([storeEntry(event)]))
  assert.match(v.title, /conflict note/)
  assert.equal(v.segs.at(-1).k, "note")
  assert.ok(!v.segs.some((s) => s.k !== "note" && s.t.includes("prompt-check")))
})

test("nothing changed: no metadata, no overlay", async () => {
  const { event } = await runHook({ language: "en", prompt: ORIG, issues: [], questions: [] })
  assert.equal(event.prompt.text, ORIG)
  assert.equal(event.metadata, undefined)
  assert.equal(latestPolish([storeEntry(event)]), null)
})

test("TUI end-to-end on the hook's real output: one toast, store entry not mutated", async () => {
  const { event } = await runHook(OK)
  const dir = mkdtempSync(join(tmpdir(), "polish-e2e-"))
  const toasts = []
  const msgs = { ses_X: [] }
  const ctx = {
    ui: { toast: { show: (t) => toasts.push(t) }, router: { current: () => ({ type: "session", sessionID: "ses_X" }) }, dialog: {} },
    keymap: { layer: () => {} },
    data: { session: { message: { list: (s) => msgs[s] } } },
  }
  const s = setupPolishPop(ctx, { dir, interval: false })
  s.tick()
  const entry = storeEntry(event)
  const before = JSON.stringify(entry)
  msgs.ses_X = [Object.freeze(entry)]
  s.tick()
  assert.equal(toasts.length, 1)
  assert.equal(JSON.stringify(entry), before)
  rmSync(dir, { recursive: true, force: true })
})

test("TUI files never touch the prompt path: no hook, no prompt send, no generate, no store writes", () => {
  const src = ["tui.js", "diff.jsx", "polish-core.js"].map((f) => readFileSync(new URL(`../../plugins/prompt-polish/${f}`, import.meta.url), "utf8")).join("\n")
  for (const bad of ["session.hook", "session.prompt", "generate", "client.rpc", "fetch(", "client.session", "setStore", "produce("]) assert.equal(src.includes(bad), false, bad)
})
