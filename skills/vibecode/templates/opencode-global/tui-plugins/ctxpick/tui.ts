// ctxpick TUI plugin: /ctxpick (or the command palette) opens a keep/drop checklist for the current session.
import { Plugin } from "@opencode/plugin/tui"
import { analyze, charsAfter, estimate, fmtTokens, GROUP_LABEL, trim, type ExportData } from "./src/core.ts"

const unwrap = (r: any) => (r && typeof r === "object" && "data" in r && !("messages" in r) ? r.data : r)

export default Plugin.define({
  id: "ctxpick",
  setup(context: any) {
    const options = {
      charsPerToken: Number(context.options?.charsPerToken) > 0 ? Number(context.options.charsPerToken) : undefined,
      minChars: Number(context.options?.minChars) > 0 ? Number(context.options.minChars) : undefined,
    }
    if (options.charsPerToken === undefined) delete options.charsPerToken
    if (options.minChars === undefined) delete options.minChars

    const toast = (message: string, variant = "info") => context.ui.toast.show({ title: "ctxpick", message, variant })

    async function open() {
      const route = context.ui.router.current()
      if (route?.type !== "session" || !route.sessionID) return toast("Open a session first.", "warning")
      const sessionID: string = route.sessionID

      let data: ExportData
      let a: ReturnType<typeof analyze>
      try {
        data = unwrap(await context.client.experimental.session.export({ sessionID }))
        a = analyze(data, options)
      } catch (e) {
        return toast(e instanceof Error ? e.message : String(e), "error")
      }
      if (!a.items.length) return toast("Nothing to pick in this session.", "warning")

      const drop = new Set<string>()
      const tk = (chars: number) => fmtTokens(estimate(chars, a.charsPerToken))
      let current: string | undefined = a.items[0]!.key

      while (true) {
        let next: "apply" | "toggle-group" | "reset" | undefined
        const after = charsAfter(a, drop)
        const picked = await context.ui.dialog.select({
          title: `ctxpick · ≈${tk(a.contextChars)} → ≈${tk(after)} tokens · ${drop.size} to drop`,
          current,
          options: a.items.map((item) => ({
            title: `${drop.has(item.key) ? "[ ]" : "[x]"} ${item.title}`,
            value: item.key,
            description: `≈${fmtTokens(item.tokens)} tok${drop.has(item.key) ? " · drop" : ""}`,
            category: GROUP_LABEL[item.group as keyof typeof GROUP_LABEL],
          })),
          actions: [
            {
              bind: "ctrl+s",
              title: "Apply",
              selection: "none",
              onTrigger: () => {
                next = "apply"
                context.ui.dialog.clear()
              },
            },
            {
              bind: "ctrl+g",
              title: "Toggle group",
              onTrigger: (value: string) => {
                current = value
                next = "toggle-group"
                context.ui.dialog.clear()
              },
            },
            {
              bind: "ctrl+r",
              title: "Keep all",
              side: "right",
              selection: "none",
              onTrigger: () => {
                next = "reset"
                context.ui.dialog.clear()
              },
            },
          ],
        })

        if (typeof picked === "string") {
          // Enter: toggle one item and re-open on it.
          drop.has(picked) ? drop.delete(picked) : drop.add(picked)
          current = picked
          continue
        }
        if (next === "toggle-group") {
          const group = a.items.find((i) => i.key === current)?.group
          const keys = a.items.filter((i) => i.group === group).map((i) => i.key)
          const allDropped = keys.every((k) => drop.has(k))
          keys.forEach((k) => (allDropped ? drop.delete(k) : drop.add(k)))
          continue
        }
        if (next === "reset") {
          drop.clear()
          continue
        }
        if (next !== "apply") return // Escape: cancel, nothing changes.
        if (!drop.size) return toast("Nothing marked to drop.")
        break
      }

      const ok = await context.ui.dialog.confirm({
        title: "ctxpick",
        message:
          `Create a copy of this session without ${drop.size} items ` +
          `(≈${tk(a.contextChars)} → ≈${tk(charsAfter(a, drop))} tokens, estimate)? The original stays as it is.`,
        label: { confirm: "Create copy", cancel: "Back" },
      })
      if (!ok) return

      try {
        const result = trim(data, drop, options)
        const created = unwrap(
          await context.client.experimental.session.import({
            info: result.data.info,
            messages: result.data.messages,
            location: result.data.info.location ?? null,
          }),
        )
        const id: string = created?.id ?? result.sessionID
        context.ui.router.navigate({ type: "session", sessionID: id })
        toast(`Opened the trimmed copy: ${result.dropped.length} items dropped.`, "success")
      } catch (e) {
        toast(`Import failed: ${e instanceof Error ? e.message : String(e)}`, "error")
      }
    }

    context.keymap.layer(() => ({
      mode: "global",
      commands: [
        {
          id: "ctxpick.open",
          title: "ctxpick: choose what stays in context",
          group: "Session",
          palette: true,
          slash: { name: "ctxpick" },
          run: () => {
            void open()
          },
        },
      ],
    }))
  },
})
