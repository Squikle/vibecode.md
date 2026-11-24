// polish-show block in the session.composer.top slot (just above the input). Its own file so a JSX failure
// leaves the /polish-show command alone (tui.js imports it in a catch). api.block(sessionID) -> view or null
// (polish-core.js view(); never throws). Reactive on the TUI's message store and on the /polish-show toggle.
import { createSignal, onCleanup } from "solid-js"

export function mountComposer(context, api) {
  const [show, setShow] = createSignal(api.show())
  const unsub = api.subscribe((v) => setShow(v))
  const color = (k) => {
    const t = context.theme?.text
    const fb = t?.feedback
    if (k === "ins") return fb?.success?.base ?? t?.base
    if (k === "del") return fb?.error?.base ?? t?.muted
    if (k === "title") return fb?.info?.base ?? t?.muted
    if (k === "note") return fb?.warning?.base ?? t?.base
    return t?.muted ?? t?.base
  }
  const rows = (sessionID) => {
    try {
      if (!show()) return []
      const v = api.block(sessionID)
      if (!v) return []
      const out = [<text fg={color("title")}>{v.title}</text>]
      if (v.excerpt) out.push(<text wrapMode="word" fg={color("ins")}>{`  sent as: ${v.excerpt}`}</text>)
      for (const h of v.hunks)
        out.push(
          <text wrapMode="word">
            <span style={{ fg: color("ctx") }}>{"  "}</span>
            {h.map((s) => (s.k === "ins" ? <b><span style={{ fg: color("ins") }}>{s.t}</span></b> : <span style={{ fg: color(s.k) }}>{s.t}</span>))}
          </text>,
        )
      if (v.more) out.push(<text fg={color("ctx")}>{`  +${v.more} more (full text in the message above)`}</text>)
      if (v.note) out.push(<text wrapMode="word" fg={color("note")}>{`  ${v.note.length > 300 ? `${v.note.slice(0, 299)}…` : v.note}`}</text>)
      return out
    } catch (e) {
      api.log("render", e)
      return []
    }
  }
  const unslot = context.ui.slot({
    append: "session.composer.top",
    render: (props) => (
      <box flexDirection="column" flexShrink={0}>
        {rows(props?.sessionID)}
      </box>
    ),
  })
  return () => {
    try { unsub() } catch {}
    if (typeof unslot === "function") unslot()
  }
}
