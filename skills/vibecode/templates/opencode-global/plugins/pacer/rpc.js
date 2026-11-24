// pacer RPC: the TUI's one way into the server plugin. The server registers this instead of a /pacer
// command, so the slash list shows one /pacer (the TUI's pop-up). Loaded by OpenCode as the plugin's "rpc"
// part and imported by index.js (server) and tui.js (client).
import { Rpc } from "@opencode/plugin/rpc"

export const PacerRpc = Rpc.define({
  id: "pacer",
  methods: {
    // text = the /pacer arguments ("" or "status", "batch on", "flush", ...); reply = the server's answer
    command: {
      input: {
        type: "object",
        properties: { sessionID: { type: "string" }, text: { type: "string" } },
        required: ["sessionID"],
        additionalProperties: false,
      },
      output: {
        type: "object",
        properties: { reply: { type: "string" } },
        required: ["reply"],
        additionalProperties: false,
      },
      errors: {},
    },
  },
  // OpenCode 2.4.1's register walks this map: without it, register throws "Object.entries requires that input
  // parameter not be null or undefined" and the server falls back to its own /pacer command
  events: {},
})
