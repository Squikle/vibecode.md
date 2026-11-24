// prompt-polish colour diff, shown in a dialog (ui.dialog.show, inside the TUI's plugin crash boundary).
// Its own file so a JSX failure leaves the /polish pop-up working (tui.js falls back to a plain alert).
// view = polish-core.js overlay(): { title, segs:[{t, k: eq|del|ins|gap|note}] }. Display only, 0 tokens.
const BOLD = 1, STRIKE = 128 // opentui text attribute bits

export function renderDiff(context, view) {
  const t = context.theme?.text
  const fb = t?.feedback
  const style = (k) => {
    if (k === "del") return { fg: fb?.error?.base ?? t?.muted, attributes: STRIKE }
    if (k === "ins") return { fg: fb?.success?.base ?? t?.base, attributes: BOLD }
    if (k === "note") return { fg: fb?.warning?.base ?? t?.base }
    if (k === "gap") return { fg: t?.muted }
    return { fg: t?.base }
  }
  return (
    <box flexDirection="column" paddingLeft={2} paddingRight={2} paddingBottom={1} gap={1}>
      <box flexDirection="row" justifyContent="space-between">
        <text fg={fb?.info?.base ?? t?.base}><b>{view.title}</b></text>
        <text fg={t?.muted}>esc</text>
      </box>
      <text wrapMode="word">
        {view.segs.map((s) => <span style={style(s.k)}>{s.t}</span>)}
      </text>
      <text fg={t?.muted}>red struck = removed · green = added · the model got it without the struck words</text>
    </box>
  )
}
