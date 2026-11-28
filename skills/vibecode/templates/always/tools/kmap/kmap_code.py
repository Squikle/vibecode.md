"""The knowledge map's build from the code and git: TS/JS relative imports, Python imports by `ast` (relative, and
absolute by a unique dotted suffix: `app.graph.order`), tests by import and by name; the first-parent history
(a merge that moved a spec into `docs/specs/done/`, or a `docs/MERGES.md` entry's commit, implemented it in the files
it changed; files changed together in 3+ small commits co-change; renames are aliases); DECISIONS' binds."""
import ast
import collections
import hashlib
import importlib.util
import posixpath
import re
from pathlib import Path

from kmap_parse import AMENDS, CO_CONF, CO_MAX, CO_SUPPORT, EDGE, FILE_MERGES, HISTORY, TS_IMPORT, is_test, run_git

IMPL_FULL = 15   # a merge of more files says less about each: implemented-in weighs sqrt(15 / files) of its base

MAP_PY = Path(__file__).resolve().parents[1] / "work" / "map.py"
_mapmod = []


def map_py():
    """`tools/work/map.py` gives the TypeScript outlines; without it a file has none."""
    if not _mapmod:
        mod = None
        try:
            spec = importlib.util.spec_from_file_location("kmap_map_py", MAP_PY)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:  # an outline is a nicety; never fail the map for it
            mod = None
        _mapmod.append(mod)
    return _mapmod[0]


def read_code(b, code):
    py = [p for p in code if p.endswith(".py")]
    dotted = collections.defaultdict(set)
    for p in py:
        parts = p[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for k in range(len(parts)):
            dotted[".".join(parts[k:])].add(p)
    modules = {k: next(iter(v)) for k, v in dotted.items() if len(v) == 1}
    mapmod = map_py()
    for p in code:
        ext = posixpath.splitext(p)[1]
        text = b.text(p)
        b.nodes[f"file:{p}"]["extra"]["fp"] = blob_sha(text)    # git's blob sha of an LF UTF-8 file: a rejection's end
        if not text or ext not in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"):
            continue
        if ext == ".py":
            outline, targets = py_scan(b, p, text, modules)
        else:
            found = mapmod.ts_outline(text) if mapmod else []
            outline = [[line, what] for line, depth, what, _ in found if depth == 0][:60]
            targets = ts_imports(b, p, text)
        b.nodes[f"file:{p}"]["extra"].update(lines=text.count("\n") + 1, outline=outline)
        for dst in sorted(set(targets)):
            if dst != p:
                etype = "tested-by" if is_test(p) and not is_test(dst) else "imports"
                src_, dst_ = (dst, p) if etype == "tested-by" else (p, dst)
                b.edge(f"file:{src_}", etype, f"file:{dst_}", evidence=p)
    for p in code:
        if not is_test(p):
            continue
        name = p.rsplit("/", 1)[-1]
        stem = re.sub(r"\.(test|spec|perf\.test)\.", ".", name)
        stem = stem[5:] if stem.startswith("test_") else stem.replace("_test.py", ".py")
        same_dir = posixpath.join(posixpath.dirname(p), stem)
        hits = [same_dir] if same_dir in b.files else [h for h in b.by_base.get(stem, []) if not is_test(h)]
        if len(hits) == 1 and hits[0] != p:
            b.edge(f"file:{hits[0]}", "tested-by", f"file:{p}", evidence="by name")


def blob_sha(text):
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:12]


def ts_imports(b, src, text):
    out = []
    for spec in TS_IMPORT.findall(text):
        if not spec.startswith("."):
            continue
        base = posixpath.normpath(posixpath.join(posixpath.dirname(src), spec))
        stem = re.sub(r"\.(js|jsx|mjs|cjs)$", "", base)
        for cand in [base] + [stem + e for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".d.ts")] + \
                    [base + "/index" + e for e in (".ts", ".tsx", ".js", ".jsx")]:
            if cand in b.files:
                out.append(cand)
                break
    return out


def py_scan(b, src, text, modules):
    """One parse: (top-level outline like map.py's, imported project files). Statements only, never expressions:
    an import sits in a module, class, function, if or try body."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], []
    outline = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            outline.append([node.lineno, f"{'class' if isinstance(node, ast.ClassDef) else 'def'} {node.name}"])
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            outline += [[node.lineno, f"const {t.id}"] for t in targets if isinstance(t, ast.Name) and t.id.isupper()]

    def rel(path):
        for cand in (path + ".py", path + "/__init__.py"):
            if cand in b.files:
                return cand
        return None

    def statements(body):
        for node in body:
            yield node
            for field in ("body", "orelse", "finalbody", "handlers"):
                yield from statements(getattr(node, field, None) or [])

    out = []
    for node in statements(tree.body):
        if isinstance(node, ast.Import):
            out += [modules.get(a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level:
            base = posixpath.dirname(src)
            for _ in range(node.level - 1):
                base = posixpath.dirname(base)
            root = posixpath.join(base, *(node.module or "").split(".")) if node.module else base
            out += [rel(root)] + [rel(posixpath.join(root, a.name)) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out += [modules.get(node.module)] + [modules.get(f"{node.module}.{a.name}") for a in node.names]
    return outline[:60], [x for x in out if x]


def read_git(b):
    """Also each file's last FILE_MERGES merge times (`extra.merged`, newest first): a found link, a collecting pair
    and a rejection count the merges that touched their own ends, never main's merges at large (137 commits a day)."""
    repo, rev = b.history or (b.root, "HEAD")
    out = run_git(repo, "log", "--first-parent", f"-n{HISTORY}", "--diff-merges=first-parent",
                  "--name-status", "-M", "--format=@@%H %ct %P", rev)
    if out is None:
        out = run_git(repo, "log", "--first-parent", f"-n{HISTORY}", "-m", "--name-status", "-M",
                      "--format=@@%H %ct %P", rev) or ""
    commits, cur = [], None
    for line in out.splitlines():
        if line.startswith("@@"):
            sha, ct, *parents = line[2:].split()
            cur = {"sha": sha, "done": [], "files": [], "ct": int(ct), "merge": len(parents) > 1}
            commits.append(cur)
        elif line.strip() and cur is not None:
            parts = line.split("\t")
            status, path = parts[0], parts[-1]
            if status.startswith("R") and len(parts) == 3 and path in b.files:
                b.alias[f"file:{parts[1]}"] = f"file:{path}"
            if path.startswith("docs/specs/done/") and path.endswith(".md") and status[:1] in "RA":
                cur["done"].append("spec:" + path.rsplit("/", 1)[-1][:-3])
            if path in b.files and status[:1] != "D":
                cur["files"].append(path)
    seen, pairs = collections.Counter(), collections.Counter()
    for at, c in enumerate(commits):
        merged = b.merge_shas.get(c["sha"][:7], [])
        impl = sorted(set(c["files"]))
        if c["done"] and not c["merge"] and at + 1 < len(commits) and commits[at + 1]["merge"]:
            impl = sorted(set(impl + commits[at + 1]["files"]))   # integrate.py moves the spec in a commit past its merge
        weight = EDGE["implemented-in"] * min(1.0, (IMPL_FULL / max(1, len(impl))) ** 0.5)
        for sid in c["done"] + merged:
            if sid in b.nodes:
                for f in impl:
                    b.edge(sid, "implemented-in", f"file:{f}", evidence=f"commit {c['sha'][:8]}", weight=weight)
        fs = sorted(set(c["files"]))
        for f in fs if c["merge"] else ():
            times = b.nodes[f"file:{f}"]["extra"].setdefault("merged", [])
            if len(times) < FILE_MERGES:
                times.append(c["ct"])
        if 2 <= len(fs) <= CO_MAX:
            seen.update(fs)
            pairs.update((fs[i], fs[j]) for i in range(len(fs)) for j in range(i + 1, len(fs)))
    for (x, y), n in pairs.items():
        if n >= CO_SUPPORT and n / min(seen[x], seen[y]) >= CO_CONF:
            b.edge(f"file:{x}", "co-changes", f"file:{y}", "STAT", f"{n} commits")


def read_binds(b):
    """DECISIONS' last column binds files and specs; "amends 12" amends a decision. After the git read: a renamed
    file's old path resolves through its alias."""
    for did, toks, number, text in b.decision_binds:
        for tok in toks:
            dst = b.ref(tok)
            if dst:
                b.edge(did, "binds", dst, evidence=f"docs/DECISIONS.md:{number}")
            elif "/" in tok or "." in tok.rsplit("/", 1)[-1]:
                b.issues.append(("warn", f"binds a missing file: docs/DECISIONS.md:{number} `{tok}`"))
        for m in AMENDS.finditer(text):
            b.edge(did, "amends", f"dec:{m.group(1)}", evidence=f"docs/DECISIONS.md:{number}")
