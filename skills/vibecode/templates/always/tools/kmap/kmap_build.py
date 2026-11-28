"""The knowledge map's build from the docs: specs (header lines, the H1's ids, backticked paths and ids, decisions
cited), `docs/BACKLOG.md` rows, `docs/DECISIONS.md` rows, `docs/MERGES.md` entries,
`docs/QUESTIONS.md` rows, the code map's tables and `docs/kmap/modules.md`; the code and git through `kmap_code`.
No model."""
import collections
import fnmatch
import posixpath
import re
import time

import kmap_code
import kmap_found
import kmap_review
from kmap_parse import (CITE, CODE, EDGE, H1, HEADER, HEADING, KINDS, MAX_BYTES, REPLACED, RULE, SKIPPED,
                        SPEC_PATH, TICK, TITLE_IDS, git_top, ids_in, is_test, merges, parse_links, run_git, sections,
                        spec_try, table_rows, walk)

AREA_COLS = ("path", "file", "where", "folder", "file or folder", "script", "files")
D_CITE = re.compile(r"\bD(\d{3})\b")
PROSE = re.compile(r"[a-z]+|[0-9a-f]{7,40}")   # no hyphen, or a sha: an unknown one in free text is a word


class Builder:
    def __init__(self, root, git=True, history=None, blobs=None):
        """`history` and `blobs` build a commit as it was (`kmap_store.open_map_at`): the first-parent history from
        (repo, sha), the files from {path: bytes} read out of `git archive` in memory (a file written to disk and read
        back cost ~3 ms each under a virus scanner: 4.4 of 6 s)."""
        self.root, self.git, self.history, self.blobs = root, git, history, blobs
        self.nodes, self.edges, self.alias, self.issues = {}, {}, {}, []
        self.pending, self.later, self.merge_shas = [], [], {}
        self.files, self.by_base = set(), collections.defaultdict(list)

    def node(self, nid, kind, l0="", path=None, status="", **extra):
        n = self.nodes.setdefault(nid, {"kind": kind, "l0": "", "path": None, "status": "", "extra": {}})
        n["l0"] = n["l0"] or l0[:160]
        n["path"] = n["path"] or path
        n["status"] = status or n["status"]
        n["extra"].update(extra)
        return n

    def edge(self, src, etype, dst, conf="EXTRACTED", evidence="", weight=None):
        if src == dst or src not in self.nodes or dst not in self.nodes:
            return
        key = (src, etype, dst)
        w = EDGE[etype] if weight is None else weight
        old = self.edges.get(key)
        if not old or old[0] < w or (conf == "CURATED" and old[1] != "CURATED"):
            self.edges[key] = (w, conf, evidence)

    def file_of(self, token, bases=()):
        """A code file for a backticked token: the path, `<base><token>` for each base folder of the doc, or a name
        (with its folders) that ends exactly one file."""
        t = token.strip().split()[0].split(":")[0].replace("\\", "/") if token.strip() else ""
        t = t[2:] if t.startswith("./") else t
        for cand in [b + t for b in bases] + [t]:
            if cand in self.files:
                return cand
        if not t or "." not in t.rsplit("/", 1)[-1]:
            return None
        for cand in [b + t for b in bases] + [t]:
            hits = [h for h in self.by_base.get(cand.rsplit("/", 1)[-1], []) if "/" not in cand or h.endswith("/" + cand)]
            if len(hits) == 1:
                return hits[0]
        return None

    def ref(self, token):
        """A curated token -> a node id, or None: `mod:x`, `spec:x`, `dec:3`, `feat:2`, a bare spec or module id, a path."""
        t = token.strip()
        kind, _, key = t.partition(":")
        if kind in KINDS and key:
            if kind == "file":
                f = self.file_of(key)
                return f"file:{f}" if f else None
            return t if t in self.nodes else self.alias.get(t)
        for cand in (f"spec:{t}", f"mod:{t}"):
            if cand in self.nodes:
                return cand
        f = self.file_of(t)
        if f:
            return f"file:{f}"
        return self.alias.get(f"spec:{t}")

    def build(self):
        t0 = time.time()
        if self.blobs is not None:
            self.git, paths = bool(self.history), sorted(self.blobs)
        else:
            tracked = run_git(self.root, "ls-files", "-co", "--exclude-standard") \
                if self.git and git_top(self.root) else None
            if tracked is None:
                self.git = False
                paths = [p.relative_to(self.root).as_posix() for p in walk(self.root)]
            else:
                paths = [p for p in tracked.splitlines() if p and not SKIPPED & set(p.split("/")[:-1])]
            paths = [p for p in paths if (self.root / p).is_file()]
        code = [p for p in paths if posixpath.splitext(p)[1] in CODE and not p.startswith("docs/")]
        for p in code:
            self.files.add(p)
            self.by_base[p.rsplit("/", 1)[-1]].append(p)
            self.node(f"file:{p}", "test" if is_test(p) else "file", path=p)
        specs = sorted(p for p in paths if SPEC_PATH.match(p) and not p.rsplit("/", 1)[-1].startswith(("_", "README")))
        for p in specs:  # nodes first: a spec's links name other specs
            self.node(f"spec:{SPEC_PATH.match(p).group(1)}", "spec", path=p, status="done" if "/done/" in p else "")
        self.read_modules()
        self.read_backlog()
        self.read_merges()
        self.read_decisions()
        self.read_areas(paths)
        for p in specs:
            self.read_spec(p)
        self.read_questions()
        kmap_code.read_code(self, code)
        if self.git:
            kmap_code.read_git(self)
        kmap_code.read_binds(self)
        self.read_module_seeds()
        for src, etype, token, evidence, strict in self.pending:
            dst = self.ref(token)
            if dst:
                self.edge(src, etype, dst, "CURATED", evidence)
            elif not strict and PROSE.fullmatch(token):
                continue   # free text: a lone word or a sha in backticks that names nothing is prose, not a link
            else:  # a built spec's links rot by design: a warning there, a red in open work
                self.issues.append(("warn" if "/done/" in evidence else "red",
                                    f"dangling: {evidence} {etype} `{token}` names nothing"))
        for src, etype, dst, evidence in self.later:
            self.edge(src, etype, dst, evidence=evidence)
        self.read_reviewed()
        return time.time() - t0

    def read_reviewed(self):
        """`docs/kmap/reviewed.md` (`kmap_review`): an accepted candidate pair is a curated link; the rows, their ends
        resolved, go to the map's meta (a rejection is held against its ends' fingerprints when the map is read)."""
        self.reviewed = kmap_review.parse(self.text(kmap_review.REVIEWED))
        for verdict, rows in self.reviewed.items():
            for row in rows:
                row["ends"] = [self.ref(row["a"]) or row["a"], self.ref(row["b"]) or row["b"]]
                if verdict == "accepted":
                    self.edge(row["ends"][0], "accepted", row["ends"][1], "CURATED",
                              f"{kmap_review.REVIEWED}:{row['line']} accepted {row['date']} by {row['reviewer']}: "
                              f"{row['evidence']}")

    def text(self, rel):
        if self.blobs is not None:
            data = self.blobs.get(rel, b"")
            return data.decode("utf-8-sig", "replace").replace("\r\n", "\n") if len(data) <= MAX_BYTES else ""
        try:
            p = self.root / rel
            return p.read_text(encoding="utf-8-sig", errors="replace") if p.stat().st_size <= MAX_BYTES else ""
        except OSError:
            return ""

    def read_modules(self):
        self.module_rows = []
        for number, header, cells in table_rows(self.text("docs/kmap/modules.md").splitlines()):
            ids = TICK.findall(cells[0]) if cells else []
            if not ids:
                continue
            mid = "mod:" + ids[0].removeprefix("mod:")
            seeds = TICK.findall(cells[2]) if len(cells) > 2 else []
            self.node(mid, "mod", cells[1] if len(cells) > 1 else "", path="docs/kmap/modules.md", line=number,
                      seeds=seeds)
            self.module_rows.append((mid, seeds, number))

    def read_module_seeds(self):
        specs = sorted(n for n in self.nodes if n.startswith("spec:"))
        for mid, seeds, number in self.module_rows:
            for seed in seeds:
                where = f"docs/kmap/modules.md:{number}"
                if any(c in seed for c in "*?["):
                    spec_glob = seed.startswith("spec:")
                    hit = [s for s in specs if fnmatch.fnmatchcase(s, seed)] if spec_glob else \
                        [f"file:{f}" for f in sorted(self.files) if fnmatch.fnmatchcase(f, seed)]
                    for h in hit:
                        self.edge(h, "part-of", mid, "CURATED", where)
                    if not hit:
                        self.issues.append(("red", f"module seed matches nothing: {where} `{seed}`"))
                    continue
                dst = self.ref(seed)
                if dst and dst != mid:
                    self.edge(dst, "part-of", mid, "CURATED", where)
                else:
                    self.issues.append(("red", f"module seed names nothing: {where} `{seed}`"))

    def read_backlog(self):
        self.backlog_order = []
        for number, header, cells in table_rows(self.text("docs/BACKLOG.md").splitlines()):
            if "batch" not in header or "status" not in header:
                continue
            col = {h: i for i, h in enumerate(header)}
            cell = lambda name: cells[col[name]] if name in col and col[name] < len(cells) else ""  # noqa: E731
            ids = TICK.findall(cell("batch"))
            if not ids:
                continue
            sid, where = f"spec:{ids[0]}", f"docs/BACKLOG.md:{number}"
            self.backlog_order.append(ids[0])
            title = re.sub(r"^\s*`[^`]+`\s*", "", cell("batch")).replace("`", "").replace("\\|", "|").strip() or cell("what")
            self.node(sid, "spec", title, status=cell("status"), backlog=number, area=cell("area"))
            for name, etype in (("waits for", "depends-on"), ("waits on", "depends-on"), ("same files as", "same-files-as")):
                for tok in ids_in(cell(name)):
                    self.pending.append((sid, etype, tok, where, False))
            for tok in TICK.findall(cell("spec")):
                m = SPEC_PATH.match(tok)
                if m and m.group(1) != ids[0]:
                    self.later.append((sid, "specified-in", f"spec:{m.group(1)}", where))

    def read_merges(self):
        """MERGES: a merged id is a node (its status `merged <date>` unless a row or a spec says more); its entry's sha
        is the merge commit on main, whose files `read_git` gives it as implemented-in."""
        for e in merges(self.text("docs/MERGES.md")):
            n = self.node(f"spec:{e['batch']}", "spec", e["what"])
            n["status"] = n["status"] or f"merged {e['date'][:10]}"
            self.merge_shas.setdefault(e["sha"][:7], []).append(f"spec:{e['batch']}")

    def read_decisions(self):
        self.decision_binds = []
        for number, header, cells in table_rows(self.text("docs/DECISIONS.md").splitlines()):
            if not cells or not cells[0].isdigit() or len(cells) < 2:
                continue
            did, text = f"dec:{cells[0]}", " | ".join(cells[1:])
            first = re.split(r"(?<=[.!?])\s", re.sub(r"[*`]", "", cells[1]), maxsplit=1)[0]
            self.node(did, "dec", first, path="docs/DECISIONS.md", line=number, text=text[:600])
            self.decision_binds.append((did, TICK.findall(cells[-1]) if len(cells) > 2 else [], number, text))
            if m := REPLACED.match(cells[1]):
                self.later.append((f"dec:{m.group(1)}", "supersedes", did, f"docs/DECISIONS.md:{number}"))

    def read_areas(self, paths):
        docs = ["docs/ARCHITECTURE.md"] + sorted(p for p in paths if p.startswith("docs/architecture/") and p.endswith(".md"))
        for doc in docs:
            lines = self.text(doc).splitlines()
            own = doc.rsplit("/", 1)[-1][:-3] if doc != "docs/ARCHITECTURE.md" else None
            title = H1.match(lines[0]).group(1) if lines and H1.match(lines[0]) else (own or "")
            top, bases = folders_in(title), folders_in(title)
            at = {}
            for number, line in enumerate(lines, 1):
                if (m := HEADING.match(line)) and number > 1:
                    bases = folders_in(m.group(2)) + top
                at[number] = bases
            for number, header, cells in table_rows(lines):
                self.area_row(doc, own, title, number, header, cells, at.get(number, top))

    def area_row(self, doc, own, title, number, header, cells, bases):
        col = next((i for i, h in enumerate(header) if h in AREA_COLS), 0)
        job = cells[0] if "need" in header else next((c for c in cells[col + 1:] if c), "")
        named = own or "need" in header   # ARCHITECTURE.md's other tables say where, not what a file holds
        for tok in TICK.findall(cells[col]) if col < len(cells) else []:
            tok = tok.split()[0].rstrip("/") if tok.strip() else ""
            f = self.file_of(tok, bases) if tok else None
            under = []
            if tok and not f:
                folder = next((c for c in [b + tok for b in bases] + [tok] if any(x.startswith(c + "/") for x in self.files)), None)
                under = sorted(x for x in self.files if folder and x.startswith(folder + "/"))
                tok = folder or tok
            if f and named:
                self.nodes[f"file:{f}"]["l0"] = self.nodes[f"file:{f}"]["l0"] or job[:160]
            aid = f"area:{own}" if own else (f"area:{tok}" if under else None)
            if not aid or not (f or under):
                continue
            self.node(aid, "area", title if own else (cells[0] if col else job), path=doc, line=number)
            if f:
                self.edge(aid, "maps", f"file:{f}", evidence=f"{doc}:{number}")
            for x in under[:300]:
                self.edge(aid, "maps", f"file:{x}", evidence=f"{doc}:{number}", weight=0.3)

    def read_spec(self, rel):
        sid = "spec:" + SPEC_PATH.match(rel).group(1)
        lines = self.text(rel).splitlines()
        self.node(sid, "spec", fp=kmap_found.spec_fp(lines))       # a rejection is undone when it changes
        head, rules, inside = {}, [], False
        for number, line in enumerate(lines, 1):
            if line.startswith("## "):
                break
            if m := HEADER.match(line):
                head[m.group(1)] = (m.group(2), number)
        for line in lines:
            if line.startswith("## "):
                inside = line[3:].strip().lower().startswith("what must be true")
            elif inside and (m := RULE.match(line)):
                rules.append(m.group(1).strip()[:140])
        title = next((H1.match(x).group(1) for x in lines[:3] if H1.match(x)), "")
        named = TITLE_IDS.search(title)
        for tok in ids_in(named.group(0)) if named else []:
            if f"spec:{tok}" != sid:   # one spec file, several rows: each row is specified here
                self.later.append((f"spec:{tok}", "specified-in", sid, f"{rel}:1"))
        n = self.nodes[sid]
        n["l0"] = TITLE_IDS.sub("", title)[:160] or n["l0"]
        # done/ wins; else the backlog row's status (read first); else the spec's own Status line
        n["status"] = ("done" if "/done/" in rel else n["status"] or head.get("Status", ("",))[0])[:60]
        tech = re.match(r"[\w-]*", head.get("Tech", ("",))[0]).group(0)   # `light`, `heavy`: the first word
        tried = spec_try("\n".join(lines[:40]))
        n["extra"].update(rules=rules[:12], tech=tech, sections=sections(lines), lines=len(lines), tried=tried[:160])
        for field, etype in (("Depends on", "depends-on"), ("Same files as", "same-files-as")):
            value, number = head.get(field, ("", 0))
            value = re.split(r"\.(?:\s|$)", value, maxsplit=1)[0]   # free text after the first period is prose
            for tok in ids_in(value):
                self.pending.append((sid, etype, tok, f"{rel}:{number}", False))
        self.spec_links(sid, rel, *head.get("Links", ("", 0)))
        body_start = next((i for i, x in enumerate(lines) if x.startswith("## ")), len(lines))
        for number, line in enumerate(lines[body_start:], body_start + 1):
            for tok in TICK.findall(line):
                f = self.file_of(tok) if ("/" in tok or "." in tok) else None
                if f:
                    self.edge(sid, "touches-file", f"file:{f}", evidence=f"{rel}:{number}")
                elif f"spec:{tok}" in self.nodes and f"spec:{tok}" != sid:
                    self.edge(sid, "mentions", f"spec:{tok}", evidence=f"{rel}:{number}")
            for d in cited(line):
                self.edge(sid, "cites", f"dec:{d}", evidence=f"{rel}:{number}")

    def spec_links(self, sid, rel, value, number):
        items, bad = parse_links(value)
        for part in bad:
            self.issues.append(("red", f"bad link: {rel}:{number} `{part[:60]}`"))
        for etype, tok, _ in items:
            if etype != "not":
                self.pending.append((sid, etype, tok, f"{rel}:{number}", True))
            elif dst := self.ref(tok):
                self.nodes[sid]["extra"].setdefault("not", []).append(dst)
            else:
                self.issues.append(("red", f"dangling: {rel}:{number} not `{tok}` names nothing"))

    def read_questions(self):
        """A QUESTIONS row (a bullet with its indented lines, or a table row) that names a spec or a code file."""
        lines, entries = self.text("docs/QUESTIONS.md").splitlines(), []
        section = ""
        for number, line in enumerate(lines, 1):
            if line.startswith("## "):
                section = line[3:].strip()
            elif line.startswith(("- ", "| ")) and not set(line) <= set("|-: "):
                entries.append([number, section, line])
            elif entries and line.startswith((" ", "\t")) and line.strip():
                entries[-1][2] += " " + line.strip()
        for number, section, text in entries:
            targets = {f"spec:{t}" for t in ids_in(text) if f"spec:{t}" in self.nodes}
            targets |= {f"file:{f}" for t in TICK.findall(text) if ("/" in t or "." in t) and (f := self.file_of(t))}
            if not targets:
                continue
            qid = f"q:{number}"
            words = re.sub(r"[*`|]", "", text.removeprefix("- ")).strip()
            self.node(qid, "q", words[:120], path="docs/QUESTIONS.md", status=section[:40], line=number)
            for t in sorted(targets):
                self.edge(qid, "asks-about", t, evidence=f"docs/QUESTIONS.md:{number}")


def folders_in(text):
    """The backticked folders (`src/`) of a heading, as bases a bare file name of its table is under."""
    return [t for t in TICK.findall(text) if t.endswith("/")]


def cited(line):
    """The decision numbers a line cites: "decision 12", "decisions 3, 4 and 7", "365 to 372", "D382"."""
    out = []
    for m in CITE.finditer(line):
        nums = re.findall(r"\d+|to", m.group(1))
        for i, x in enumerate(nums):
            if x == "to" and 0 < i < len(nums) - 1 and nums[i + 1] != "to":
                a, b = int(nums[i - 1]), int(nums[i + 1])
                out += [str(k) for k in range(a + 1, b)] if 0 < b - a <= 20 else []
            elif x != "to":
                out.append(x)
    return out + [str(int(d)) for d in D_CITE.findall(line)]
