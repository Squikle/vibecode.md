// Parse a selection like "3 5-9 tail subagent summary:Context" into item keys.
import type { Item } from "./core.ts"

export function parseSelection(items: Item[], args: string[]): Set<string> {
  const keys = new Set<string>()
  for (const raw of args.flatMap((a) => a.split(","))) {
    const arg = raw.trim()
    if (!arg) continue
    const range = /^(\d+)(?:-(\d+))?$/.exec(arg)
    if (range) {
      const from = Number(range[1])
      const to = Number(range[2] ?? range[1])
      for (let n = from; n <= to; n++) {
        const item = items[n - 1]
        if (!item) throw new Error(`No item #${n} (there are ${items.length}).`)
        keys.add(item.key)
      }
      continue
    }
    if (["summary", "tail", "output", "subagent"].includes(arg)) {
      items.filter((i) => i.group === arg).forEach((i) => keys.add(i.key))
      continue
    }
    const section = /^summary:(.+)$/i.exec(arg)
    if (section) {
      const hit = items.filter((i) => i.group === "summary" && i.title.toLowerCase() === section[1]!.toLowerCase())
      if (!hit.length) throw new Error(`No summary section named "${section[1]}".`)
      hit.forEach((i) => keys.add(i.key))
      continue
    }
    const exact = items.find((i) => i.key === arg)
    if (!exact) throw new Error(`Unknown item "${arg}". Use numbers from --list, a group name, or summary:<Section>.`)
    keys.add(exact.key)
  }
  return keys
}
