// prompt-polish TUI part ("polish-pop"): /polish pop-up with two toggles, both ON by default:
//   polish = the server hook (index.js) polishes prompts at all (it reads <data>/settings.json per prompt)
//   show   = after a polished prompt, a toast shows your message as corrected (removed words struck through);
//            "show my last prompt" in the pop-up opens the full colour diff (removed red+struck, added green).
// Display only: reads the message the TUI already has (text + metadata.promptPolish.original), never writes
// the message store, never sends anything to a model (0 tokens). /polish-show = alias of /polish.
// Every render/command is wrapped: errors go to <data>/tui.log; the server part keeps polishing either way.
import { Plugin } from "@opencode/plugin/tui"
import { appendFileSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { homedir } from "node:os"
import {
  DEFAULTS, SETTINGS_FILE, commandRoute, dataDirFrom, latestPolish, nextPop, overlay, overlayText,
  parseSettings, rows, serializeSettings, statusText, toastText,
} from "./polish-core.js"

const dataDir = () => dataDirFrom(process.env, homedir())
const msg = (e) => (e instanceof Error ? e.message : String(e)).slice(0, 300)
const TICK_MS = 1000

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

/** settings.json, else the v1 show.json ({show}), else both ON */
export function readSettings(dir = dataDir()) {
  try { return parseSettings(readFileSync(join(dir, SETTINGS_FILE), "utf8")) } catch {}
  try { return parseSettings(readFileSync(join(dir, "show.json"), "utf8")) } catch {}
  return { ...DEFAULTS }
}

export function writeSettings(s, dir = dataDir()) {
  mkdirSync(dir, { recursive: true })
  const file = join(dir, SETTINGS_FILE)
  writeFileSync(`${file}.tmp`, serializeSettings(s))
  renameSync(`${file}.tmp`, file)
}

const loadDiff = () => import("./diff.jsx").then((m) => m.renderDiff)

/** the testable part. opts: dir, interval (ms | false), renderDiff (async loader of (context, view) => JSX) */
export function setupPolishPop(context, { dir = dataDir(), interval = TICK_MS, renderDiff = loadDiff } = {}) {
  const log = makeLog(dir)
  let s = readSettings(dir)
  let seen = {}

  const toast = (message, variant = "info", extra = {}) => {
    try { context.ui.toast.show({ title: "prompt-polish", message, variant, ...extra }) } catch (e) { log("toast", e) }
  }
  const sessionID = () => {
    try { const r = context.ui.router.current(); return r?.type === "session" ? r.sessionID : undefined } catch { return undefined }
  }
  const list = (sid) => {
    try { return sid ? context.data?.session?.message?.list?.(sid) ?? [] : [] } catch (e) { log("store", e); return [] }
  }

  function set(patch) {
    s = { ...s, ...patch }
    try { writeSettings(s, dir); return true } catch (e) { log("save", e); toast(`${statusText(s)} (not saved: ${msg(e)})`, "warning"); return false }
  }

  /** watch the current session; pop the overlay once per newly polished prompt (tracking runs even when show is OFF) */
  function tick() {
    try {
      const sid = sessionID()
      const r = nextPop(seen, sid, list(sid))
      seen = r.seen
      if (!r.pop || !s.show) return
      const v = overlay(r.pop)
      if (!v) return
      context.ui.toast.show({ title: v.title, message: toastText(v), variant: "info", duration: v.duration })
    } catch (e) { log("overlay", e) }
  }

  async function showDiff(p) {
    const v = overlay(p)
    if (!v) return toast("nothing to show: polish only changed spacing")
    let render
    try { render = await renderDiff() } catch (e) { log("diff render", e) }
    try {
      if (typeof render === "function") return context.ui.dialog.show(() => render(context, v))
    } catch (e) { log("diff dialog", e) }
    try { await context.ui.dialog.alert({ title: v.title, message: overlayText(v) }) } catch (e) { log("diff alert", e) }
  }

  async function open() {
    let current
    while (true) {
      const last = latestPolish(list(sessionID()))
      const picked = await context.ui.dialog.select({ title: statusText(s), current, options: rows(s, last) })
      if (picked === undefined) return
      if (picked === "last") return last && showDiff(last)
      if (picked === "polish" || picked === "show") set({ [picked]: !s[picked] })
      current = picked // reopen on the same row
    }
  }

  async function run(input) {
    try {
      const r = commandRoute(input, s)
      if (r.error) return toast(r.error, "warning")
      if (r.open) return await open()
      if (r.set && !set(r.set)) return
      toast(statusText(s), s.polish ? "success" : "info")
    } catch (e) { log("command", e) }
  }

  const command = (name, title) => ({
    id: `prompt-polish.${name}`,
    title,
    description: "/polish: pop-up with the polish + show toggles (also /polish on|off, /polish show on|off, /polish status)",
    group: "Session",
    palette: name === "polish",
    slash: { name, arguments: true },
    run: (input) => { void run(input) },
  })
  try {
    context.keymap.layer(() => ({
      mode: "global",
      commands: [command("polish", "prompt-polish: toggles (polish / show)"), command("polish-show", "prompt-polish: toggles (alias of /polish)")],
    }))
  } catch (e) { log("command register", e) }

  let timer
  if (interval) {
    try { timer = setInterval(tick, interval); timer.unref?.() } catch (e) { log("timer", e) }
  }
  const dispose = () => { try { if (timer) clearInterval(timer) } catch (e) { log("dispose", e) } }
  return { run, tick, dispose, settings: () => ({ ...s }) }
}

export default Plugin.define({
  id: "prompt-polish",
  setup(context) {
    try {
      return setupPolishPop(context).dispose
    } catch (e) {
      makeLog()("setup", e)
      return () => {}
    }
  },
})
