// pacer TUI plugin: the one /pacer. Bare /pacer (or the command palette) opens a pop-up; `/pacer <args>` goes
// to the server unchanged. Loaded automatically next to index.js (plugins/pacer/tui.js). It reads pacer's
// status files and never writes them: every change goes through the server's `pacer` RPC (one writer; the
// server falls back to a /pacer command only where it has no RPC). A live timer sits in the prompt footer.
import { Plugin } from "@opencode/plugin/tui"
import { readFileSync, statSync, existsSync } from "node:fs"
import { join } from "node:path"
import { homedir } from "node:os"
import { parseStatus, headline, rows, rowAction, newSlotCommand, slashRoute, replyToast, footerText } from "./tui-core.js"

const DATA = process.env.PACER_DATA || join(homedir(), ".local", "share", "pacer")
const TOGGLES = join(DATA, "toggles.json"), STATE = join(DATA, "plugin-state.json")
const text = (p) => { try { return readFileSync(p, "utf8") } catch { return null } }
const mtime = (p) => { try { return statSync(p).mtimeMs } catch { return 0 } }
const readStatus = () => { try { return parseStatus(existsSync(DATA), text(TOGGLES), text(STATE)) } catch { return parseStatus(false) } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const msg = (e) => (e instanceof Error ? e.message : String(e)).slice(0, 200)

export default Plugin.define({
  id: "pacer",
  /** @param {import("@opencode/plugin/tui").Plugin.Context} context */
  setup(context) {
    /** @param {string} message @param {"info" | "success" | "warning" | "error"} [variant] */
    const toast = (message, variant = "info") => { try { context.ui.toast.show({ title: "pacer", message, variant }) } catch {} }
    const sessionNow = () => {
      try { const r = context.ui.router.current(); return r?.type === "session" ? r.sessionID : undefined } catch { return undefined }
    }

    // the RPC contract, imported lazily so a missing @opencode/plugin/rpc only costs the RPC path
    let rpcDef // undefined = not tried yet, null = not available here
    const pacerRpc = async () => {
      if (rpcDef === undefined) { try { rpcDef = (await import("./rpc.js")).PacerRpc ?? null } catch { rpcDef = null } }
      return rpcDef
    }
    // the one write path: the server's RPC (it returns the reply: a status goes into the transcript, anything
    // else comes back here as a toast). Old server without the RPC: its /pacer command posts the reply itself.
    const send = async (sessionID, cmd, wait = true) => {
      const def = await pacerRpc()
      let rpcErr
      if (def) {
        try {
          const r = await context.client.rpc(def).command({ sessionID, text: cmd })
          const t = replyToast(cmd, r?.reply)
          if (t) toast(t.message, t.variant)
          return
        } catch (e) { rpcErr = e }
      }
      const before = mtime(TOGGLES)
      try { await context.client.session.command({ sessionID, name: "pacer", text: cmd }) }
      catch (e) { throw rpcErr ?? e } // the RPC's error says more
      if (wait) for (let i = 0; i < 15 && mtime(TOGGLES) === before; i++) await sleep(100) // so the reopened list shows it
    }

    async function open(sid) {
      let current
      while (true) {
        const s = readStatus()
        let next
        const picked = await context.ui.dialog.select({
          title: headline(s, Date.now()),
          current,
          options: rows({ ...s, sid, now: Date.now() }),
          actions: [
            { bind: "ctrl+r", title: "Refresh", selection: "none", onTrigger: () => { next = "refresh"; context.ui.dialog.clear() } },
            { bind: "ctrl+o", title: "Off all", side: "right", selection: "none", onTrigger: () => { next = "offall"; context.ui.dialog.clear() } },
          ],
        })
        if (next === "refresh") continue
        const value = picked ?? (next === "offall" ? "offall" : undefined)
        if (value === undefined) return // Escape
        if (picked !== undefined) current = picked // Enter reopens on the same row
        const act = rowAction(value, { ...s, sid })
        let cmd
        if (act.kind === "run") cmd = act.text
        else if (act.kind === "choose") {
          cmd = await context.ui.dialog.select({ title: `pacer: ${act.title}`, current: act.current, options: act.options })
          if (cmd === undefined) continue
        } else if (act.kind === "confirm") {
          if (!(await context.ui.dialog.confirm({ title: act.title, message: act.message, label: act.label }))) continue
          cmd = act.text
        } else if (act.kind === "prompt") {
          const name = await context.ui.dialog.prompt({ title: act.title, description: act.description, placeholder: act.placeholder })
          if (name === undefined) continue
          const r = newSlotCommand(name)
          if (r.error) { toast(r.error, "warning"); continue }
          cmd = r.text
        } else { if (act.reason) toast(act.reason); continue }
        try { await send(sid, cmd, !act.close) } catch (e) { toast(`/pacer ${cmd} failed: ${msg(e)}`, "error"); continue }
        if (act.close) return
      }
    }

    async function run(input) {
      try {
        const sid = sessionNow()
        if (!sid) return toast("Open a session first", "warning")
        const r = slashRoute(input)
        if (r.kind === "forward") await send(sid, r.text, false)
        else await open(sid)
      } catch (e) { toast(`pacer: ${msg(e)}`, "error") }
    }

    try {
      context.keymap.layer(() => ({
        mode: "global",
        commands: [
          {
            id: "pacer.open",
            title: "pacer: pace, batch, slots",
            description: "pacer panel; /pacer <args> runs the server command",
            group: "Session",
            palette: true,
            // arguments: the TUI owns submit for /pacer; typed args go to the server's RPC unchanged
            slash: { name: "pacer", arguments: true },
            run: (input) => { void run(input) },
          },
        ],
      }))
    } catch (e) { toast(`pacer TUI off: ${msg(e)}`, "error") }

    // the live timer ("pacer: 2 held · flush in 23s"), in its own JSX file: if it fails to load, the pop-up stays
    let unslot
    const footerFor = (sessionID) => footerText(text(STATE), sessionID ?? sessionNow(), Date.now(), text(TOGGLES))
    import("./footer.jsx")
      .then((m) => { unslot = m.mountFooter(context, footerFor) })
      .catch((e) => toast(`pacer timer off: ${msg(e)}`, "warning"))
    return () => { try { if (typeof unslot === "function") unslot() } catch {} }
  },
})
