"""Where a session's tokens went: per session, its turns and summed context, the tool kinds that cost most, its biggest
outputs and one SMELLS line. The coordinator's recurring token audit runs it (docs/workflow/coordinator.md).

Usage: python -I -X utf8 tools/work/session-audit.py [--peek] <name>=<session id or export .json> [...]
  A session id is read with `opencode session export <id>`; a path ending in .json is read as such an export.
  --peek: one line per session instead (minutes since its last turn, turns, context, finish, its last text): the
  heartbeat's look at running sessions without reading their chats.

ctxSum is the real prompt size summed over every turn (input + cache read + cache write), from the provider's counts.
reRead is an estimate: a tool output's chars/4, times the turns after it (each later turn re-sends it; compaction is
ignored, so it is an upper bound). waits counts shell calls that sleep between checks: each such turn re-sends the
whole context to learn nothing (one blocking wait on a launched run is not counted). mapPy counts `map.py` lookups;
greps count `git grep` too. A review pack (`pack*.md`) is read whole on purpose: never a "big" line. Check a smell against the export
before fixing it: a count alone can be a false alarm. It prints to stdout and writes nothing.
"""
import collections, json, pathlib, re, shutil, subprocess, sys, time

KINDS = [("wait/poll", r"wait-process|start-sleep|get-process|\bsleep \d|\bwait \$"),
         ("e2e", r"playwright|\be2e\b|cypress"), ("tests", r"pytest|vitest|jest|go test|cargo test|unittest"),
         ("build", r"\bnpm |\bpnpm |\byarn |\btsc\b|cargo build|\bmake\b"), ("docker", r"docker"), ("ssh", r"\bssh\b"),
         ("log-read", r"\.log\b|get-content.*-tail|\btail\b"), ("git", r"\bgit\b"),
         ("grep", r"select-string|\brg\b|\bgrep\b"), ("read", r"get-content|\bcat\b|\bhead\b")]
# Polling = sleeping between checks. One blocking `Wait-Process`/`wait $pid` per launched run is not polling.
WAIT = re.compile(r"start-sleep|\bsleep \d")
# Greps: a smell only above ~1 per 3 turns; a bug hunt's targeted greps are normal (two audits: 55 and 58).
BIG_CHARS, SAME_CMD, GREP_PER_TURN, LAST_CTX, AVG_CTX, POLLS, WIDE_GREPS = 15000, 3, 0.34, 150000, 120000, 3, 3
GIT_GREP = re.compile(r"\bgit\b[^|;]*\bgrep\b")
# A grep that prints a whole definition (-A20 and up) where `tools/work/map.py` gives its line numbers.
WIDE_GREP = re.compile(r"\bgrep\b[^|;]*-a\s*[2-9]\d|select-string[^|;]*-context\s+\d+\s*,\s*[2-9]\d")  # lowercased
# A review pack (`.work/<id>/pack*.md`) is meant to be read whole, once (reviewer-brief): it still counts in the
# totals, but is never a "big read".
PACK = re.compile(r"(^|[\\/\s])pack[^\\/\s]*\.md\b", re.I)


def kind(tool, inp):
    if tool not in ("shell", "bash"):
        return tool
    c = (inp.get("command") or "").lower()
    return next(("sh:" + k for k, p in KINDS if re.search(p, c)), "sh:other")


def load(src):
    if src.endswith(".json"):
        p = pathlib.Path(src)
        text = p.read_text(encoding="utf-8-sig") if p.is_file() else ""
    else:
        exe = shutil.which("opencode") or "opencode"
        text = subprocess.run([exe, "session", "export", src], capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout
    i = text.find("{")
    return json.loads(text[i:]) if i >= 0 else None


def audit(name, o):
    msgs = [m for m in o.get("messages", []) if m.get("type") == "assistant"]
    n, tot, out, last, first = len(msgs), 0, 0, 0, 0
    calls, chars, weight, big, cmds = (collections.Counter(), collections.Counter(), collections.Counter(), [], [])
    for i, m in enumerate(msgs):
        t = m.get("tokens") or {}
        c = t.get("cache") or {}
        ctx = t.get("input", 0) + c.get("read", 0) + c.get("write", 0)
        first = first or ctx
        tot += ctx; out += t.get("output", 0) + t.get("reasoning", 0); last = ctx or last
        for p in m.get("content", []):
            if p.get("type") != "tool":
                continue
            st = p.get("state") or {}
            inp = st.get("input") or {}
            txt = "".join(x.get("text", "") for x in (p.get("content") or st.get("content") or []) if isinstance(x, dict))
            if not txt and isinstance(st.get("output"), str):
                txt = st["output"]
            k = kind(p.get("name", "?"), inp)
            w = len(txt) / 4 * (n - i)
            calls[k] += 1; chars[k] += len(txt); weight[k] += w
            head = (inp.get("command") or inp.get("path") or inp.get("filePath") or inp.get("pattern") or json.dumps(inp))
            if not (k in ("read", "sh:read") and PACK.search(head)):
                big.append((w, len(txt), k, head[:90].replace("\n", " ")))
            cmds.append(inp.get("command") or "")
    low = [x.lower() for x in cmds]
    waits = sum(bool(WAIT.search(x)) for x in low)
    maps = sum("map.py" in x for x in low)
    print(f"\n== {name} turns={n} ctxSum={tot/1e6:.2f}M out={out/1e3:.0f}k firstCtx={first/1e3:.0f}k "
          f"lastCtx={last/1e3:.0f}k waits={waits} mapPy={maps}")
    for k, v in weight.most_common(9):
        print(f"  {k:12} calls={calls[k]:4} outChars={chars[k]/1e3:7.1f}k reRead~{v/1e6:5.2f}M")
    big.sort(reverse=True)
    for w, l, k, h in big[:5]:
        print(f"   big {w/1e6:4.2f}M {l/1e3:5.1f}k {k}: {h}")
    sm = [f"polling x{waits}"] if waits >= POLLS else []
    greps = calls["grep"] + calls["sh:grep"] + sum(bool(GIT_GREP.search(x)) for x in low)
    wide = sum(bool(WIDE_GREP.search(x)) for x in low)
    sm += [f"grep x{greps} ({greps / n:.2f}/turn)"] if n and greps / n > GREP_PER_TURN else []
    sm += [f"grep -A20+ x{wide} (map.py?)"] if wide >= WIDE_GREPS else []
    sm += [f"big {l/1e3:.0f}k {k}: {h[:50]}" for w, l, k, h in big if l >= BIG_CHARS][:4]
    sm += [f"same cmd x{c}: {x[:50]}" for x, c in collections.Counter(x.strip() for x in cmds if x.strip()).items()
           if c >= SAME_CMD][:3]
    sm += [f"lastCtx {last/1e3:.0f}k"] if last >= LAST_CTX else []
    sm += [f"avgCtx {tot/n/1e3:.0f}k"] if n and tot / n > AVG_CTX else []
    print("   SMELLS: " + ("; ".join(sm) or "none"))
    return n, tot, waits * tot / n if n else 0


def peek(name, o):
    msgs = [m for m in o.get("messages", []) if m.get("type") == "assistant"]
    if not msgs:
        print(f"{name:8} no assistant turn"); return
    m = msgs[-1]
    t = m.get("tokens") or {}
    c = t.get("cache") or {}
    tm = m.get("time") or {}
    now = time.time() * 1000
    age = (now - (tm.get("completed") or tm.get("created") or now)) / 60000
    text = next((p.get("text", "") for mm in reversed(msgs[-3:]) for p in reversed(mm.get("content", []))
                 if p.get("type") == "text" and p.get("text", "").strip()), "")
    busy = "" if tm.get("completed") else " RUNNING"
    ctx = t.get("input", 0) + c.get("read", 0) + c.get("write", 0)
    print(f"{name:8} age={age:5.1f}m turns={len(msgs):3} ctx={ctx/1e3:4.0f}k finish={m.get('finish')}{busy} | "
          f"{' '.join(text.split())[:260]}")


def main(args):
    quick = args[:1] == ["--peek"]
    args = args[1:] if quick else args
    if not args or any("=" not in a or a.startswith("-") for a in args):
        print("usage: session-audit.py [--peek] <name>=<session id or export .json> ...", file=sys.stderr)
        return 2
    turns = total = polls = 0
    for a in args:
        name, src = a.split("=", 1)
        o = load(src)
        if not o:
            print(f"{name}: no export"); continue
        if quick:
            peek(name, o); continue
        n, t, p = audit(name, o)
        turns += n; total += t; polls += p
    if total:
        print(f"\n== ALL turns={turns} ctxSum={total/1e6:.2f}M polling~{polls/1e6:.2f}M ({polls/total*100:.1f}%: "
              "wait turns x each session's mean context)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
