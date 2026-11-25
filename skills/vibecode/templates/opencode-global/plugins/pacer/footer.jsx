// pacer's live timer in the prompt footer: "pacer: 2 held · flush in 23s", empty while nothing is held in
// this session. Its own file so a JSX failure leaves the pop-up alone (tui.js imports it in a try).
// read(sessionID) -> the line (tui-core.js footerText over plugin-state.json); re-read every second.
// onClick(ev): a mouse-up on the label (opentui Renderable onMouseUp, bubbles from the text to the box) opens
// the pop-up. mounted(up): called once after mount with typeof the node's mouse-up listener (log evidence).
import { createSignal, onCleanup, onMount } from "solid-js"

export function mountFooter(context, read, onClick, mounted) {
  let logged = false
  return context.ui.slot({
    append: "prompt.footer.status",
    render: (props) => {
      const [now, setNow] = createSignal(Date.now())
      const timer = setInterval(() => setNow(Date.now()), 1000)
      onCleanup(() => clearInterval(timer))
      const line = () => { now(); try { return read(props?.sessionID) } catch { return "" } }
      const fg = context.theme?.text?.muted ?? context.theme?.text?.base
      let node
      onMount(() => { if (logged) return; logged = true; try { mounted?.(typeof node?._mouseListeners?.up) } catch {} })
      return (
        <box ref={(el) => { node = el }} onMouseUp={(ev) => { try { onClick?.(ev) } catch {} }}>
          <text fg={fg}>{line()}</text>
        </box>
      )
    },
  })
}
