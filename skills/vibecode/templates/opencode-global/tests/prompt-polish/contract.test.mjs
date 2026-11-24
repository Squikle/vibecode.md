// node --test tests/   (server index.js -> TUI core contract: the hook's output is what polish-show reads,
// and polish-show adds nothing to the prompt or the stored message)
import test from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, rmSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import server from "../../plugins/prompt-polish/index.js"
import { latestPolish, view, viewLines } from "../../plugins/prompt-polish/polish-core.js"

const ORIG = "is the readme ok? kinda merge optimizations with agents diagram, or make another one if changing it makes it look to complex"
const POLISHED = "Is the readme ok? Merge optimizations with the agents diagram, or make another one if changing it makes it look too complex"

async function runHook(reply, text = ORIG) {
  const cfg = mkdtempSync(join(tmpdir(), "polish-cfg-"))
  const old = process.env.XDG_CONFIG_HOME
  process.env.XDG_CONFIG_HOME = cfg // no command templates on disk
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
    return { event, calls }
  } finally {
    if (old === undefined) delete process.env.XDG_CONFIG_HOME
    else process.env.XDG_CONFIG_HOME = old
    rmSync(cfg, { recursive: true, force: true })
  }
}

// the TUI store entry = {id, type:"user", ...payload} where payload = {text, files, agents, metadata}
const storeEntry = (event) => ({ id: "msg_1", type: "user", text: event.prompt.text, files: event.prompt.files, agents: event.prompt.agents, metadata: event.metadata })

test("polished prompt: metadata.promptPolish.original is the typed text; polish-show diffs it", async () => {
  const { event, calls } = await runHook({ language: "en", prompt: POLISHED, issues: [], questions: [] })
  assert.equal(calls.length, 1) // exactly the polisher's own call; polish-show makes none
  assert.equal(event.prompt.text, POLISHED)
  assert.equal(event.metadata.promptPolish.original, ORIG)
  assert.deepEqual(Object.keys(event.metadata.promptPolish).sort(), ["followup", "issues", "language", "model", "original"])
  const v = view(latestPolish([storeEntry(event)]))
  assert.match(v.title, /✎ prompt-polish · \d edits/)
  assert.ok(viewLines(v).some((l) => l.includes("to → too")))
})

test("conflict flagged: the appended note is shown as the note line", async () => {
  const { event } = await runHook({ language: "en", prompt: POLISHED, issues: ["Wants one diagram and two."], questions: ["One or two?"] })
  assert.match(event.prompt.text, /\n\n\[prompt-check, auto\] Possible conflict: Wants one diagram and two\. Ask: One or two\?/)
  const v = view(latestPolish([storeEntry(event)]))
  assert.match(v.title, /conflict note added/)
  assert.match(v.note, /^\[prompt-check, auto\] Possible conflict/)
  assert.ok(!v.hunks.some((h) => h.some((s) => s.t.includes("prompt-check")))) // the note isn't diffed as an edit
})

test("nothing changed: no metadata, polish-show renders nothing", async () => {
  const { event } = await runHook({ language: "en", prompt: ORIG, issues: [], questions: [] })
  assert.equal(event.prompt.text, ORIG)
  assert.equal(event.metadata, undefined)
  assert.equal(view(latestPolish([storeEntry(event)])), null)
})

test("tui.js never touches the prompt path: no hook, no session.prompt, no generate", () => {
  const src = readFileSync(new URL("../../plugins/prompt-polish/tui.js", import.meta.url), "utf8") + readFileSync(new URL("../../plugins/prompt-polish/composer.jsx", import.meta.url), "utf8")
  for (const bad of ["session.hook", "session.prompt", "generate", "client.rpc", "fetch("]) assert.equal(src.includes(bad), false, bad)
})
