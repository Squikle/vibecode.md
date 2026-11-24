// prompt-polish — OpenCode v2 server plugin (from the vibecode opencode-global kit; see its README.md)
// Cleans up messy / non-English user prompts with a cheap model BEFORE they reach the main model:
//   - minimal edits: fixes grammar/typos, translates to English, never expands or adds structure to short stuff
//   - flags real self-contradictions -> tells the main model to ask you narrowing questions first
// Uses the v2 `session.hook("prompt")` (runs once at prompt admission; the edit becomes the stored user message).
// Original text is kept in message metadata (not sent to the model).
//
// v2 changes:
//   - slash commands: the hook only sees the EXPANDED command template (no leading "/"), so it now skips
//     (a) text matching any command file in ~/.config/opencode/command(s)/ or <project>/.opencode/command(s)/,
//     (b) machine-looking / structured text (markdown headings, XML tags, 3+ list lines, $ARGUMENTS),
//     (c) prompts with skills attached by something other than an @mention (commands/plugins do that).
//   - less verbose: minimal-edit instructions, last chat turns given as context, follow-ups only flag hard
//     contradictions, short English follow-ups skipped, bloated rewrites discarded.
//
// Bypass for one message: start it with "!!"  (e.g. "!! leave this exactly as typed")
// Disable entirely: set ENABLED = false below, or add "-prompt-polish" to "plugins" in opencode.jsonc.
// SET UP: POLISH_MODEL = your cheapest cloud model (provider id, model id, optional variant, as in your config);
// ONLY_PROVIDERS = the provider ids whose sessions it may polish (the cloud ones you already send prompts to).
//
// PRIVACY: only runs when the session's model is from a provider in ONLY_PROVIDERS. Local-model sessions are never
// sent to Anthropic. If the session model can't be determined, it does nothing (fail closed).
// SAFETY: any error / timeout / weird output -> your original text goes through untouched.

import { readdir, readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"

const ENABLED = true
const POLISH_MODEL = { providerID: "anthropic", id: "claude-haiku-5-5" } // a cheap, fast model; add variant: "low" if it has one
const ONLY_PROVIDERS = ["anthropic"] // main-model providers this runs for
const MIN_CHARS = 80 // first message of a session: shorter English prompts are left alone
const FOLLOWUP_MIN_CHARS = 300 // later messages: shorter English prompts are left alone (context makes them obvious)
const TIMEOUT_MS = 30_000
const MIN_LENGTH_RATIO = 0.35 // rewrite shorter than this fraction of the original = probably dropped content -> discard
const MAX_GROWTH = 1.2 // English rewrite longer than original * this (+40 chars) = bloat -> discard
const CONTEXT_ASSISTANT_CHARS = 1500 // tail of the last assistant reply given to Haiku as context
const CONTEXT_USER_CHARS = 600 // tail of the previous user message given as context
const REPLY_IN_ORIGINAL_LANGUAGE = false // true: when translating, ask the main model to answer in your language
const BYPASS_PREFIX = "!!"

const INSTRUCTIONS = `You are a prompt pre-processor. You do NOT answer or execute the request. You make the smallest edits needed so another AI model reads it correctly.

Do this, and nothing more:
1. Translate to English if any part isn't English.
2. Fix typos, grammar and punctuation that could cause misreading. Drop pure filler words.
3. Leave everything else as the user wrote it. If it is already clear, return it unchanged.

Length and shape:
- The result must NOT be longer than the original (translation aside). Never expand, explain, restate or elaborate.
- Do NOT add bullets, numbering, headings or bold. Keep the user's own line breaks/lists if they had them.
- Short, casual, terse messages stay short, casual and terse. Keep the user's voice and tone words ("quick", "don't overdo it", swearing).
- References like "that", "it", "option 2", "do the same for X" are fine as-is; do not spell them out.

Issues — flag almost never:
{{ISSUE_RULE}}
- Never flag vagueness, missing detail, style, or anything an assistant can sensibly decide or infer from the conversation.
- At most 2 issues. When in doubt, empty arrays.

Hard rules:
- Never drop, invent or weaken a requirement, detail, number, name, constraint or question.
- Copy VERBATIM (no translation, no edits): code, commands, file paths, URLs, identifiers, version numbers, error messages, quoted strings, and any token starting with @ or /.
- Do not add advice, answers, assumptions or new requirements. Do not translate proper nouns / product names.

Output ONLY a JSON object, no code fence, no commentary:
{"language":"<ISO 639-1 code of the original>","prompt":"<the edited prompt>","issues":["<short sentence>"],"questions":["<one short narrowing question per issue, with options>"]}
`

const ISSUE_RULE_FIRST =
  `- contradictions: two requirements in the message that cannot both be true;\n` +
  `- blocking ambiguity: a decision that changes the result a lot AND that an assistant cannot reasonably guess.`
const ISSUE_RULE_FOLLOWUP =
  `- ONLY hard contradictions: two requirements in the NEW message that cannot both be true. This is a follow-up in an` +
  ` ongoing chat, so never flag ambiguity — the conversation resolves it.`

// ---------- helpers ----------

function nonLatinRatio(text) {
  const letters = text.match(/\p{L}/gu) ?? []
  if (letters.length === 0) return 0
  const nonLatin = letters.filter((c) => !/\p{Script=Latin}/u.test(c)).length
  return nonLatin / letters.length
}

const norm = (s) => s.replace(/\s+/g, " ").trim().toLowerCase()

// Structured / machine-written text: command templates, pasted docs, agent prompts. Don't touch.
function looksStructured(text) {
  if (/\$ARGUMENTS|\$\d\b/.test(text)) return true
  if (/^#{1,6}\s+\S/m.test(text)) return true // markdown heading
  if (/<\/?[a-z][\w-]*(\s[^>]*)?>\s*$/im.test(text) && /<\/[a-z][\w-]*>/i.test(text)) return true // XML-ish block
  const listLines = text.match(/^\s*(?:[-*+]|\d+[.)])\s+\S/gm) ?? []
  return listLines.length >= 3
}

// Fingerprints of command templates on disk: longest static chunk of each template body.
async function commandFingerprints(dirs) {
  const prints = []
  for (const dir of dirs) {
    let names = []
    try {
      names = await readdir(dir)
    } catch {
      continue
    }
    for (const name of names) {
      if (!name.endsWith(".md")) continue
      try {
        const raw = await readFile(join(dir, name), "utf8")
        const body = raw.replace(/^﻿?---\r?\n[\s\S]*?\r?\n---\r?\n/, "")
        const chunks = body
          .split(/\$ARGUMENTS|\$\d+|!`[^`]*`|@\S+/)
          .map(norm)
          .filter((c) => c.length >= 20)
          .sort((a, b) => b.length - a.length)
        if (chunks[0]) prints.push(chunks[0].slice(0, 200))
      } catch {}
    }
  }
  return prints
}

function commandDirs(projectDir) {
  const configHome = process.env.XDG_CONFIG_HOME || join(homedir(), ".config")
  const roots = [join(configHome, "opencode")]
  if (process.env.OPENCODE_CONFIG_DIR) roots.push(process.env.OPENCODE_CONFIG_DIR)
  if (projectDir) roots.push(join(projectDir, ".opencode"))
  return roots.flatMap((r) => [join(r, "command"), join(r, "commands")])
}

function parseJson(raw) {
  if (typeof raw !== "string") return undefined
  const start = raw.indexOf("{")
  const end = raw.lastIndexOf("}")
  if (start === -1 || end <= start) return undefined
  try {
    const obj = JSON.parse(raw.slice(start, end + 1))
    if (typeof obj?.prompt !== "string" || obj.prompt.trim() === "") return undefined
    const strs = (a) => (Array.isArray(a) ? a.filter((s) => typeof s === "string" && s.trim()).slice(0, 2) : [])
    return {
      language: typeof obj.language === "string" ? obj.language.toLowerCase() : "en",
      prompt: obj.prompt.trim(),
      issues: strs(obj.issues),
      questions: strs(obj.questions),
    }
  } catch {
    return undefined
  }
}

// Re-point @file / agent / skill mention offsets at the rewritten text. Missing mentions get re-appended.
function remapMentions(prompt, text) {
  let out = text
  const lists = [prompt.files, prompt.agents, prompt.skills].filter(Array.isArray)
  for (const list of lists) {
    for (const item of list) {
      const m = item?.mention
      if (!m || typeof m.text !== "string" || m.text === "") continue
      let at = out.indexOf(m.text)
      if (at === -1) {
        out = `${out}\n${m.text}`
        at = out.length - m.text.length
      }
      m.start = at
      m.end = at + m.text.length
    }
  }
  return out
}

function withTimeout(promise, ms) {
  let timer
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`timeout after ${ms}ms`)), ms)
    }),
  ]).finally(() => clearTimeout(timer))
}

async function sessionInfo(ctx, sessionID) {
  try {
    const s = await ctx.session.get({ sessionID })
    const info = s?.data ?? s
    return {
      model: info?.model,
      isChild: Boolean(info?.parentID),
      directory: info?.location?.directory ?? info?.directory ?? ctx.location?.directory,
    }
  } catch {
    return { model: undefined, isChild: false, directory: ctx.location?.directory }
  }
}

// Last user + assistant text from the session (before this prompt is admitted).
async function recentTurns(ctx, sessionID) {
  try {
    const res = await ctx.session.context({ sessionID })
    const msgs = Array.isArray(res) ? res : Array.isArray(res?.data) ? res.data : []
    let lastUser, lastAssistant
    for (let i = msgs.length - 1; i >= 0 && (!lastUser || !lastAssistant); i--) {
      const m = msgs[i]
      if (!lastAssistant && m?.type === "assistant" && Array.isArray(m.content)) {
        const t = m.content.filter((p) => p?.type === "text" && typeof p.text === "string").map((p) => p.text).join("\n")
        if (t.trim()) lastAssistant = t
      } else if (!lastUser && m?.type === "user" && typeof m.text === "string" && m.text.trim()) {
        lastUser = m.text
      }
    }
    return { hasHistory: Boolean(lastUser || lastAssistant), lastUser, lastAssistant }
  } catch {
    return { hasHistory: false }
  }
}

const tail = (s, n) => (s.length > n ? `…${s.slice(-n)}` : s)

function buildPrompt(original, turns) {
  let p = INSTRUCTIONS.replace("{{ISSUE_RULE}}", turns.hasHistory ? ISSUE_RULE_FOLLOWUP : ISSUE_RULE_FIRST)
  if (turns.hasHistory) {
    p += `\nCONVERSATION SO FAR (context only — do not edit, summarize or copy it into the output):\n`
    if (turns.lastUser) p += `[previous user message]\n${tail(turns.lastUser, CONTEXT_USER_CHARS)}\n`
    if (turns.lastAssistant) p += `[assistant's last reply]\n${tail(turns.lastAssistant, CONTEXT_ASSISTANT_CHARS)}\n`
  }
  return `${p}\nNEW USER MESSAGE TO EDIT:\n<<<\n${original}\n>>>`
}

// ---------- plugin ----------

export default {
  id: "prompt-polish",
  async setup(ctx) {
    if (!ENABLED) return

    await ctx.session.hook("prompt", async (event) => {
      try {
        const original = event.prompt?.text
        if (typeof original !== "string") return
        const trimmed = original.trim()

        // Manual bypass.
        if (trimmed.startsWith(BYPASS_PREFIX)) {
          const stripped = trimmed.slice(BYPASS_PREFIX.length).trimStart()
          const offset = original.indexOf(stripped)
          event.prompt.text = stripped
          for (const list of [event.prompt.files, event.prompt.agents, event.prompt.skills].filter(Array.isArray)) {
            for (const item of list) {
              if (item?.mention) {
                item.mention.start -= offset
                item.mention.end -= offset
              }
            }
          }
          return
        }

        // Slash commands / empty / structured (command templates, pasted docs) -> leave alone.
        if (trimmed === "" || trimmed.startsWith("/")) return
        if (looksStructured(trimmed)) return
        // Skills attached without an @mention = added by a command/plugin, not typed by you.
        if (Array.isArray(event.prompt.skills) && event.prompt.skills.some((s) => !s?.mention)) return

        const foreign = nonLatinRatio(trimmed) > 0.15
        if (!foreign && trimmed.length < MIN_CHARS) return

        // Privacy gate: only for cloud Claude sessions, never subagent (machine-written) prompts.
        const info = await sessionInfo(ctx, event.sessionID)
        if (info.isChild || !info.model || !ONLY_PROVIDERS.includes(info.model.providerID)) return

        // Expanded custom command template? (hook only sees the expanded text, never the "/name")
        const normText = norm(trimmed)
        const prints = await commandFingerprints(commandDirs(info.directory))
        if (prints.some((fp) => normText.includes(fp))) return

        const turns = await recentTurns(ctx, event.sessionID)
        if (turns.hasHistory && !foreign && trimmed.length < FOLLOWUP_MIN_CHARS) return

        const res = await withTimeout(
          ctx.experimental.generate.text({ model: POLISH_MODEL, prompt: buildPrompt(original, turns) }),
          TIMEOUT_MS,
        )
        const parsed = parseJson(res?.text)
        if (!parsed) return
        if (trimmed.length >= 200 && parsed.prompt.length < trimmed.length * MIN_LENGTH_RATIO) return
        if (!foreign && parsed.prompt.length > trimmed.length * MAX_GROWTH + 40) {
          console.error(`[prompt-polish] discarded bloated rewrite (${trimmed.length} -> ${parsed.prompt.length} chars)`)
          return
        }
        // Nothing worth changing -> keep your exact text.
        if (parsed.issues.length === 0 && norm(parsed.prompt) === normText) return

        let text = parsed.prompt
        if (parsed.issues.length > 0) {
          const qs = parsed.questions.length ? ` Ask: ${parsed.questions.join(" / ")}` : ""
          text +=
            `\n\n[prompt-check, auto] Possible conflict: ${parsed.issues.join(" ")}${qs}` +
            ` — if it's real and context doesn't settle it, ask me before starting; otherwise ignore this.`
        }
        if (REPLY_IN_ORIGINAL_LANGUAGE && parsed.language && parsed.language !== "en") {
          text += `\n\n(Reply in the language with ISO code "${parsed.language}".)`
        }

        event.prompt.text = remapMentions(event.prompt, text)
        event.metadata = {
          ...event.metadata,
          promptPolish: {
            original,
            language: parsed.language,
            issues: parsed.issues.length,
            followup: turns.hasHistory,
            model: `${POLISH_MODEL.providerID}/${POLISH_MODEL.id}`,
          },
        }
      } catch (err) {
        // Never block the prompt: fall through with the original text.
        console.error(`[prompt-polish] skipped: ${err?.message ?? err}`)
      }
    })
  },
}
