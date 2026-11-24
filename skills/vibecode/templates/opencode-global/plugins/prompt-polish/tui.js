// prompt-polish TUI plugin: polish-show. Loaded automatically next to index.js (plugins/prompt-polish/tui.js).
// When the server part (index.js) rewrote your last prompt, a small block above the input shows what changed
// ("✎ prompt-polish · 2 edits", then `to → too`), plus any [prompt-check, auto] note it appended.
// The transcript itself already shows the final (polished) text: the TUI replaces your typed copy with the
// server's stored message. The block is only the marker + diff.
// /polish-show toggles it (also: on | off | status). Default ON. Persisted in <data>/show.json.
// Display only: reads what the TUI already has, sends nothing to any model (0 tokens). Every render and command
// is wrapped: an error renders nothing and goes to <data>/tui.log; the server part keeps polishing either way.
import { Plugin } from "@opencode/plugin/tui"
import { appendFileSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { homedir } from "node:os"
import { blockFor, commandRoute, parseShow, serializeShow, stateText } from "./polish-core.js"

const dataDir = () => process.env.PROMPT_POLISH_DATA || join(homedir(), ".local", "share", "prompt-polish")
const msg = (e) => (e instanceof Error ? e.message : String(e)).slice(0, 300)

export function makeLog(dir = dataDir()) {
  const file = join(dir, "tui.log")
  return (where, e) => {
    try {
      mkdirSync(dir, { recursive: true })
      try { if (statSync(file).size > 256 * 1024) renameSync(file, `${file}.1`) } catch {}
      appendFileSync(file, `${new Date().toISOString()} ${where}: ${msg(e)}\n`)
    } catch {}
  }
}

export function readShow(dir = dataDir()) {
  try { return parseShow(readFileSync(join(dir, "show.json"), "utf8")) } catch { return true }
}

export function writeShow(show, dir = dataDir()) {
  mkdirSync(dir, { recursive: true })
  const file = join(dir, "show.json")
  writeFileSync(`${file}.tmp`, serializeShow(show))
  renameSync(`${file}.tmp`, file)
}

/** the testable part: wires the command + the composer block into an OpenCode TUI plugin context */
export function setupPolishShow(context, { dir = dataDir(), mount } = {}) {
  const log = makeLog(dir)
  let show = readShow(dir)
  const listeners = new Set()
  const toast = (message, variant = "info") => {
    try { context.ui.toast.show({ title: "prompt-polish", message, variant }) } catch (e) { log("toast", e) }
  }

  const api = {
    show: () => show,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    log,
    /** view model for a session, or null; never throws */
    block(sessionID) {
      try {
        if (!show || !sessionID) return null
        return blockFor(show, context.data?.session?.message?.list?.(sessionID))
      } catch (e) { log("block", e); return null }
    },
  }

  function run(input) {
    try {
      const r = commandRoute(input, show)
      if (r.error) return toast(r.error, "warning")
      if (r.next !== undefined && r.next !== show) {
        show = r.next
        try { writeShow(show, dir) } catch (e) { log("save", e); toast(`${stateText(show)} (not saved: ${msg(e)})`, "warning"); notify(); return }
        notify()
      }
      toast(stateText(show), show ? "success" : "info")
    } catch (e) { log("command", e) }
  }
  function notify() { for (const fn of listeners) { try { fn(show) } catch (e) { log("listener", e) } } }

  try {
    context.keymap.layer(() => ({
      mode: "global",
      commands: [
        {
          id: "prompt-polish.show",
          title: "prompt-polish: show edits (toggle)",
          description: "/polish-show [on|off|status]: mark polished prompts above the input (0 tokens)",
          group: "Session",
          palette: true,
          slash: { name: "polish-show", arguments: true },
          run: (input) => { run(input) },
        },
      ],
    }))
  } catch (e) { log("command register", e) }

  let unslot
  const mounting = Promise.resolve()
    .then(() => (mount ? mount(context, api) : import("./composer.jsx").then((m) => m.mountComposer(context, api))))
    .then((u) => { unslot = u })
    .catch((e) => log("composer mount", e))

  const dispose = () => { try { if (typeof unslot === "function") unslot() } catch (e) { log("dispose", e) } }
  return { api, run, dispose, mounting }
}

export default Plugin.define({
  id: "prompt-polish",
  setup(context) {
    try {
      return setupPolishShow(context).dispose
    } catch (e) {
      makeLog()("setup", e)
      return () => {}
    }
  },
})
