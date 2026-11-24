// pacer's live timer in the prompt footer: "pacer: 2 held · flush in 23s", else the slot's batch setting
// ("pacer: batch 60s · nothing held"), empty in a session with no slot. Its own file so a JSX failure leaves the pop-up alone (tui.js imports it in a try).
// read(sessionID) -> the line (tui-core.js footerText over plugin-state.json); re-read every second.
import { createSignal, onCleanup } from "solid-js"

export function mountFooter(context, read) {
  return context.ui.slot({
    append: "prompt.footer.status",
    render: (props) => {
      const [now, setNow] = createSignal(Date.now())
      const timer = setInterval(() => setNow(Date.now()), 1000)
      onCleanup(() => clearInterval(timer))
      const line = () => { now(); try { return read(props?.sessionID) } catch { return "" } }
      const fg = context.theme?.text?.muted ?? context.theme?.text?.base
      return <text fg={fg}>{line()}</text>
    },
  })
}
