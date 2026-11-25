// pacer TUI plugin: the one /pacer. Bare /pacer (or the command palette) opens a pop-up; `/pacer <args>` goes
// to the server unchanged. Loaded automatically next to index.js (plugins/pacer/tui.js). It reads pacer's
// status files and never writes them: every change goes through the server's `pacer` RPC (one writer; the
// server falls back to a /pacer command only where it has no RPC). A live timer sits in the prompt footer.
import { Plugin } from "@opencode/plugin/tui"
import { readFileSync, statSync, existsSync } from "node:fs"
import { join } from "node:path"
import { homedir } from "node:os"
import { parseStatus, headline, rows, rowAction, newSlotCommand, slashRoute, nowRoute, replyToast, footerText, footerClick } from "./tui-core.js"
import { appendFileSync, mkdirSync } from "node:fs"

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
    const send = async (sessionID, cmd, wait = true, quiet = false) => {
      const def = await pacerRpc()
      let rpcErr
      if (def) {
        try {
          const r = await context.client.rpc(def).command({ sessionID, text: cmd })
          const t = replyToast(cmd, r?.reply)
          if (t && !(quiet && t.variant === "success")) toast(t.message, t.variant)
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

    // /now <prompt>: a one-shot pass on the server (awaited, so it is set before the prompt lands), then the
    // prompt as typed. Bare /now = flush what is held now. No pass flag (old server): the prompt still goes in,
    // it just takes the normal batch wait.
    async function now(input) {
      try {
        const sid = sessionNow()
        if (!sid) return toast("Open a session first", "warning")
        const r = nowRoute(input)
        if (r.kind === "flush") return await send(sid, "flush", false)
        try { await send(sid, "now", false, true) } catch (e) { toast(`/now: no pass (${msg(e)}); the prompt waits like any other`, "warning") }
        await context.client.session.prompt({ sessionID: sid, text: r.text })
      } catch (e) { toast(`/now failed: ${msg(e)}`, "error") }
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
          {
            id: "pacer.now",
            title: "pacer: send now",
            description: "/now <prompt> skips the batch wait (held items ride along); bare /now flushes",
            group: "Session",
            palette: true,
            slash: { name: "now", arguments: true },
            run: (input) => { void now(input) },
          },
        ],
      }))
    } catch (e) { toast(`pacer TUI off: ${msg(e)}`, "error") }

    // the live timer ("pacer: 2 held · flush in 23s"), in its own JSX file: if it fails to load, the pop-up stays
    let unslot
    const footerFor = (sessionID) => footerText(text(STATE), sessionID ?? sessionNow(), Date.now(), text(TOGGLES))
    // a left click on the footer label opens the pop-up (same as bare /pacer). tui.log (own file, never a
    // status file) records mounts + clicks so a click can be proven from the log.
    const tuiLog = (what, obj) => { try { mkdirSync(DATA, { recursive: true }); appendFileSync(join(DATA, "tui.log"), `${new Date().toISOString()} ${what} ${JSON.stringify(obj ?? {})}\n`) } catch {} }
    let opening = 0
    const onClick = (ev) => {
      try {
        let sel = ""
        try { sel = context.renderer?.getSelection?.()?.getSelectedText?.() ?? "" } catch {}
        if (!footerClick(ev, sel)) return
        tuiLog("click", { button: ev?.button, busy: !!opening })
        if (opening && Date.now() - opening < 60e3) return // double click: one pop-up
        opening = Date.now()
        void run("").finally(() => { opening = 0 })
      } catch {}
    }
    import("./footer.jsx")
      .then((m) => { unslot = m.mountFooter(context, footerFor, onClick, (up) => tuiLog("footer-mounted", { up })) })
      .catch((e) => toast(`pacer timer off: ${msg(e)}`, "warning"))
    return () => { try { if (typeof unslot === "function") unslot() } catch {} }
  },
})
