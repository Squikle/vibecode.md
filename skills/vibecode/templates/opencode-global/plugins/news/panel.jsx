// news-panel UI slots, in their own file so a JSX/slot failure leaves /todo alone (tui.js imports it in a try):
//   sidebar.content      "📌 Todo: N waits on you" + top 5 of those: "⏳ [tag]" then 1-2 text lines cut with
//                        "…", then one "FYI: 2 bugs, 10 news (/todo)" line (full text only in the /todo pop-up)
//   prompt.footer.status "📌 N waits on you" ("📌 FYI" when only bugs/news are left, empty when nothing is);
//                        a left click opens /todo
//   home.footer.status   same count + click on the home screen
//   session.panel        full open list when opened via the palette command "todo: toggle side panel"
// views = { footer(), sidebar(max), click(ev, where), mounted(slot, up) } from tui.js (cached reads of
// items.json + checks.json). Re-read every 2s.
// Clicks: OpenCode compiles this file with babel-preset-solid (generate "universal", moduleName
// "@opentui/solid"), so onMouseUp={f} becomes setProp(node, "onMouseUp", f) -> the opentui Renderable setter
// (_mouseListeners.up), the same path the built-in TUI uses. The box catches clicks that land on the text too
// (mouse events bubble up to parents).
import { createSignal, onCleanup, onMount, For, Show } from "solid-js"
// tui.js uses the TUI's own solid createSignal for the pop-up rows' live titleView (inline ←/→ scroll)
export { createSignal }

function ticker(ms) {
  const [n, setN] = createSignal(0)
  const t = setInterval(() => setN((x) => x + 1), ms)
  onCleanup(() => clearInterval(t))
  return n
}

export function mountPanel(context, views) {
  const base = context.theme?.text?.base
  const muted = context.theme?.text?.muted ?? base
  const safe = (f, d) => { try { return f() } catch { return d } }
  const offs = []
  const claim = (spec) => { try { const off = context.ui.slot(spec); if (typeof off === "function") offs.push(off) } catch {} }
  const logged = new Set()

  const List = (max) => {
    const n = ticker(2000)
    const lines = () => { n(); return safe(() => views.sidebar(max), []) }
    return (
      <box flexDirection="column">
        {/* "⏳ [tag]" head lines in the base colour, the indented (already cut) text lines muted; no re-wrap */}
        <For each={lines()}>{(l) => <text wrapMode="none" fg={l.startsWith("  ") ? muted : base}>{l}</text>}</For>
      </box>
    )
  }
  const Count = (slot) => {
    const n = ticker(2000)
    const line = () => { n(); return safe(() => views.footer(), "") }
    let node
    onMount(() => {
      if (logged.has(slot)) return
      logged.add(slot)
      safe(() => views.mounted?.(slot, typeof node?._mouseListeners?.up), undefined)
    })
    return (
      <box ref={(el) => { node = el }} onMouseUp={(ev) => safe(() => views.click?.(ev, slot), undefined)}>
        <text fg={muted}>{line()}</text>
      </box>
    )
  }

  claim({ append: "sidebar.content", render: () => List(5) })
  claim({ append: "prompt.footer.status", render: () => Count("prompt.footer.status") })
  claim({ append: "home.footer.status", render: () => Count("home.footer.status") })
  claim({ append: "session.panel", render: (props) => <Show when={props?.name === "todo"}>{List(40)}</Show> })
  return offs
}
