"""Level 3's line ranges, resolved at query time from the working tree and never stored: a file's top-level symbols and
class methods with their first and last lines (Python by `ast`; TS/JS by a scan that skips strings, template literals
and comments and counts brackets), ranked by how their names match the topic (its id and title, the backticked names
of its spec, its modules and their seeds), a few per file."""
import ast
import re

from kmap_parse import L3_SYMBOLS, L3_WHOLE, TICK

DECL = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
                  r"(function\*?|const|let|var|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)")
MEMBER = re.compile(r"^(\s+)(?:(?:public|private|protected|static|readonly|async|override|abstract|get|set)\s+)*"
                    r"(#?[A-Za-z_$][\w$]*)\s*(?:<[^>(]*>)?\s*\(")
NOT_MEMBER = {"if", "for", "while", "switch", "return", "catch", "function", "with", "else", "do", "typeof", "new",
              "await", "super", "this", "constructor"}
GOES_ON = (")", "]", "}", ".", "|", "&", "?", ":")      # a line starting so continues the one above
STOP = {"the", "and", "for", "with", "from", "its", "into", "that", "this", "when", "what", "each", "after", "before",
        "only", "spec", "web", "src", "api", "tools", "test", "tests", "docs", "one", "two", "not", "but", "are", "has",
        "all", "any", "new", "out", "off", "own", "per", "via", "fix", "more", "less", "then", "them", "they", "than"}
CODE_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def depths(text):
    """The bracket depth at the end of each line; strings, template literals (with their `${}`) and comments skipped.
    A quote left open at a line's end closes there (JSX text like `don't`)."""
    ends, depth, mode, tpl, i, n = [], 0, None, [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            ends.append(depth)
            mode = None if mode in ("//", "'", '"') else mode
        elif mode == "//":
            pass
        elif mode == "/*":
            if text.startswith("*/", i):
                mode, i = None, i + 1
        elif mode in ("'", '"', "`"):
            if c == "\\" and text[i + 1:i + 2] != "\n":
                i += 1
            elif c == mode:
                mode = None
            elif mode == "`" and text.startswith("${", i):
                tpl.append(depth)
                depth, mode, i = depth + 1, None, i + 1
        elif text.startswith(("//", "/*"), i):
            mode, i = text[i:i + 2], i + 1
        elif c in "'\"`":
            mode = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
            if c == "}" and tpl and depth == tpl[-1]:
                tpl.pop()
                mode = "`"
        i += 1
    ends.append(depth)
    return ends


def indent(line):
    return len(line) - len(line.lstrip())


def close(lines, starts, ends, k, base, limit):
    """The last line of the declaration at line k (0-based) whose body sits above depth `base`."""
    for j in range(k, limit):
        if ends[j] > base:
            continue
        if j > k and starts[j] > base:
            return j                                   # it closed what it opened
        nxt = lines[j + 1] if j + 1 < len(lines) else ""
        if lines[j].rstrip().endswith((";", "}")) or not nxt.strip() or (
                indent(nxt) <= indent(lines[k]) and not nxt.lstrip().startswith(GOES_ON)):
            return j
    j = limit - 1
    while j > k and not lines[j].strip():
        j -= 1
    return j


def doc_top(lines, k):
    """The first line of the comment block or decorators right above line k."""
    while k > 0 and lines[k - 1].strip().startswith(("/**", "*", "//", "@")):
        k -= 1
    return k


def ts_symbols(text):
    """[(name, first, last)] (1-based) for each top-level declaration and each method of a top-level class."""
    lines = text.split("\n")
    ends = depths(text)
    starts = [0] + ends[:-1]
    tops = [k for k, ln in enumerate(lines) if starts[k] == 0 and DECL.match(ln)]
    out = []
    for at, k in enumerate(tops):
        m = DECL.match(lines[k])
        end = close(lines, starts, ends, k, 0, tops[at + 1] if at + 1 < len(tops) else len(lines))
        out.append((m.group(2), doc_top(lines, k) + 1, end + 1))
        if m.group(1) != "class":
            continue
        for j in range(k + 1, end):
            mm = MEMBER.match(lines[j])
            if starts[j] == 1 and mm and mm.group(2) not in NOT_MEMBER:
                out.append((f"{m.group(2)}.{mm.group(2)}", doc_top(lines, j) + 1, close(lines, starts, ends, j, 1, end) + 1))
    return out


def py_symbols(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def first(node):
        return min([node.lineno] + [d.lineno for d in node.decorator_list])

    out = []
    for node in tree.body:
        if isinstance(node, defs):
            out.append((node.name, first(node), node.end_lineno))
            if isinstance(node, ast.ClassDef):
                out += [(f"{node.name}.{s.name}", first(s), s.end_lineno) for s in node.body if isinstance(s, defs)]
    return out


def symbols(path, text):
    if path.endswith(".py"):
        return py_symbols(text)
    return ts_symbols(text) if path.endswith(CODE_EXT) else []


def words_of(text):
    """Lower-case name parts: `shiftLevel` -> shift, level; `minimap-shift` -> minimap, shift."""
    return [w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", text or "")]


def terms(m, seed, root):
    """(names, words) the topic asks about: names are its spec's backticked identifiers (an exact match), words the
    parts of its id, its one-liner, its modules and their seeds."""
    n = m.nodes[seed]
    names, words = set(), set(words_of(seed.split(":", 1)[-1])) | set(words_of(n["l0"]))
    for o, t, label, *_ in m.adj.get(seed, []):
        if label == "part-of" and o.startswith("mod:"):
            words |= set(words_of(o[4:])) | {w for s in m.nodes[o]["extra"].get("seeds", [])
                                             for w in words_of(s.rsplit("/", 1)[-1].split(".")[0])}
    if n["kind"] == "spec" and n["path"] and root:
        try:
            text = (root / n["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for tok in TICK.findall(text):
            if "/" not in tok and not re.search(r"\.(md|tsx?|jsx?|py|css|json)\b", tok):
                names |= {x for x in re.findall(r"[A-Za-z_$][\w$]{2,}", tok) if x.lower() not in STOP}
    return names, {w for w in words if len(w) >= 3 and w not in STOP}


def score(name, names, words):
    leaf = name.rsplit(".", 1)[-1]
    s = 3 if leaf in names or name in names else 0
    for p in words_of(name):
        if len(p) >= 3 and any(p == w or (min(len(p), len(w)) >= 4 and (p.startswith(w) or w.startswith(p)))
                               for w in words):
            s += 1
    return s


def ranges(root, path, topic):
    """`a-b name; c-d name` for the file's best-matching symbols, with its line count, or None (no match, a short file,
    no file)."""
    try:
        text = (root / path).read_text(encoding="utf-8-sig", errors="replace")
    except (OSError, TypeError):
        return None
    count = len(text.splitlines())
    if count <= L3_WHOLE:
        return None
    names, words = topic
    hits = sorted(((-score(n, names, words), a, n, b) for n, a, b in symbols(path, text)), key=lambda h: (h[0], h[1]))
    best = sorted((a, b, n) for s, a, n, b in hits[:L3_SYMBOLS] if s < 0)
    if not best:
        return None
    return "; ".join(f"{a}-{b} {n}" for a, b, n in best) + f" ({count} lines)"
