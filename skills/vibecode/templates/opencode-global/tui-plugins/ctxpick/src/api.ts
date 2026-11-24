// Talks to a running OpenCode server. Used by the CLI; the TUI plugin uses the client it is given instead.
import { readFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import type { ExportData } from "./core.ts"

export interface Api {
  exportSession(sessionID: string): Promise<ExportData>
  importSession(data: ExportData): Promise<{ id: string }>
}

const unwrap = (body: any) => (body && typeof body === "object" && "data" in body ? body.data : body)

function serviceFile(): any {
  const state = process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state")
  try {
    return JSON.parse(readFileSync(path.join(state, "opencode", "service.json"), "utf8"))
  } catch {
    return undefined
  }
}

/** Server URL and password: flags, then OPENCODE_SERVER_URL / OPENCODE_SERVER_PASSWORD, then the local service file. */
export function httpApi(input: { url?: string; password?: string } = {}): Api {
  const service = serviceFile()
  let url = input.url || process.env.OPENCODE_SERVER_URL || service?.url
  if (!url) throw new Error("No OpenCode server found. Start opencode, or pass --url.")
  url = String(url).replace("://0.0.0.0", "://127.0.0.1").replace(/\/+$/, "")
  const password = input.password || process.env.OPENCODE_SERVER_PASSWORD || service?.password
  const user = process.env.OPENCODE_SERVER_USERNAME || "opencode"
  const headers: Record<string, string> = { "content-type": "application/json" }
  if (password) headers.authorization = "Basic " + Buffer.from(`${user}:${password}`).toString("base64")

  const call = async (method: string, route: string, body?: unknown) => {
    const res = await fetch(url + route, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
    const text = await res.text()
    if (!res.ok) throw new Error(`${method} ${route} -> HTTP ${res.status}: ${text.slice(0, 500)}`)
    return unwrap(text ? JSON.parse(text) : undefined)
  }

  return {
    exportSession: (id) => call("GET", `/api/experimental/session/${encodeURIComponent(id)}/export`),
    importSession: (data) =>
      call("POST", "/api/experimental/session/import", {
        info: data.info,
        messages: data.messages,
        location: data.info.location ?? null,
      }),
  }
}
