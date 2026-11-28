"""`tools/kmap/`'s recall and found links: the recall meter at a merge's first parent (and its `Recall:` bullet in a
MERGES entry), the miss candidates (mechanical, self-reports as weak evidence), the `found` promotion, the architect's
review (accept, reject as a snapshot that comes back) and the heartbeat's nudge. On `test_kmap.py`'s tiny repository;
every cache is the test's own.

python -I -X utf8 -m unittest discover -s tools/kmap/tests      (stdlib only; needs git on PATH)"""
import datetime
import importlib
import importlib.util
import json
import re
import subprocess
import sys
import unittest

import kit
from test_kmap import FILES, build_repo, run

kmap_found = importlib.import_module("kmap_found")
kmap_store = importlib.import_module("kmap_store")
MERGE_LOG = kit.TOOLS / "work" / "merge-log.py"
# the coordinator's heartbeat: the team deploy's, or the team template's beside this one
BEAT = next((p for p in (kit.TOOLS / "work" / "coord-beat.py",
                         kit.TOOLS.parents[1] / "team" / "tools" / "work" / "coord-beat.py") if p.is_file()), None)
T0 = datetime.datetime(2026, 11, 1, 12, 0).timestamp()


def at(t):
    return datetime.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def merge_of(root, subject):
    return subprocess.run(["git", "-C", str(root), "log", "--merges", "--format=%h", f"--grep={subject}"],
                          capture_output=True, text=True).stdout.split()[0]


def test_recall_at_the_merge_s_first_parent(repo, tmp_path):
    root, _ = repo
    cache = tmp_path / "r"
    rc, out = run(root, "recall", merge_of(root, "merge levels"), "--batch", "minimap-levels", cache=cache)
    assert rc == 0, out
    assert re.search(r"^RECALL batch=minimap-levels sha=[0-9a-f]{8} k=20 recall=1\.0 precision=0\.05 new=0 missed=-$",
                     out, re.M), out
    assert re.search(r"^MERGES: 1\.00 \(1 of 1 code files in the map's top 20 at `[0-9a-f]{8}`; precision 0\.05; "
                     r"0 new\)$", out, re.M), out
    rc, out = run(root, "recall", merge_of(root, "merge routes"), "--batch", "link-routes-a", "--log", cache=cache)
    assert rc == 0 and "missed=src/ui/islands.ts" in out, out          # a row with no file of its own
    logged = json.loads((cache / "recall.jsonl").read_text(encoding="utf-8"))
    assert (logged["batch"], logged["topic"], logged["recall"], logged["missed"]) == (
        "link-routes-a", "spec:link-routes-a", 0.0, ["src/ui/islands.ts"])
    cands = [json.loads(x) for x in (cache / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(c["a"], c["b"], c["kind"]) for c in cands] == [("spec:link-routes-a", "src/ui/islands.ts", "recall-miss")]
    # the heartbeat's count is HEAD's, as --review's: the merge's own implemented-in link holds this pair already
    beat = json.loads((cache / "review.json").read_text(encoding="utf-8"))["unreviewed"]     # as the merge left it
    rc, review = run(root, "candidates", "--review", cache=cache)
    rows = [ln for ln in review.splitlines() if re.match(r"^\| [0-9a-f]{6} \|", ln)]
    assert beat == len(rows) == 0, review
    rc, out = run(root, "recall", "--replay", "5", "--log", cache=cache)   # the one merge that moved a spec
    assert rc == 0 and "baseline over 1 merges with a spec" in out and "recall@20  1.000 / 1.000" in out, out
    assert re.search(r"\d+ new candidates in .*, \d+ stale collecting lines dropped along the way", out), out


def test_merge_log_adds_a_recall_bullet(tmp_path):
    r = kit.Repo(tmp_path / "proj")
    r.write("docs/MERGES.md", "# Merges\n\n<!-- merge-log.py adds each new entry below this line, newest first -->\n\n")
    r.write("docs/specs/x.md", "# X (`x`)\n\nStatus: ready\n**Try:** x\n\n## What must be true\n\n1. **A.** `src/a.ts`\n")
    r.write("src/a.ts", "export const a = 1;\n")
    r.write("src/b.ts", "export const b = 1;\n")
    r.commit("one")
    r.branch("x")
    r.write("src/a.ts", "export const a = 2;\n")
    r.write("src/b.ts", "export const b = 2;\n")
    r.write("src/c.ts", "export const c = 1;\n")
    r.commit("x")
    r.git("checkout", "-q", "main")
    r.git("merge", "-q", "--no-ff", "x", "-m", "Merge branch 'x'")
    sha = r.git("rev-parse", "--short=8", "HEAD")
    got = subprocess.run([sys.executable, str(MERGE_LOG), "--file", str(r.path / "docs/MERGES.md"), "--batch", "x",
                          "--sha", sha, "--what", "w", "--up", "no, docs only", "--try", "t", "--from", "`x`", "--when",
                          "2026-11-28 10:00"], capture_output=True, text=True, encoding="utf-8")
    assert got.returncode == 0 and "RECALL batch=x" in got.stdout, got.stdout + got.stderr
    text = (r.path / "docs/MERGES.md").read_text(encoding="utf-8")
    assert f"- **Recall:** 0.33 (1 of 3 code files in the map's top 20 at `" in text and "; 1 new; missed `src/b.ts`)" in text, text
    assert (r.path / ".work/kmap/recall.jsonl").is_file()
    (r.path / "docs/specs/done").mkdir()
    r.git("mv", "docs/specs/x.md", "docs/specs/done/x.md")      # the integrator: the spec moves in a commit past the merge
    r.commit("Merge x: the branch's work")
    b = importlib.import_module("kmap_build").Builder(r.path)
    b.build()
    assert ("spec:x", "implemented-in", "file:src/b.ts") in b.edges     # the merge's files, not the docs commit's


def write(cache, lines):
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "candidates.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def cand(a, b, kind, ev, t=T0):
    return {"a": a, "b": b, "kind": kind, "evidence": ev, "at": at(t)}


def test_promotion_found_links_and_the_review_table(repo, tmp_path):
    root, _ = repo
    cache = tmp_path / "c"
    split, levels, mini = "src/ui/split.ts", "src/model/levels.ts", "src/ui/Minimap.tsx"
    write(cache, [cand("spec:island-drag", split, "recall-miss", "aaaa1111"),     # three mechanical: a link
                  cand("spec:island-drag", split, "co-change", "bbbb2222"),
                  cand("spec:island-drag", split, "off-map-read", "sess-x"),
                  cand("spec:theme-a", levels, "self-report", "sess-1"),            # self-reports alone: never
                  cand("spec:theme-a", levels, "self-report", "sess-2"),
                  cand("spec:theme-a", levels, "self-report", "sess-3"),
                  cand("spec:scope-a", mini, "self-report", "sess-4"),              # one self-report and one mechanical
                  cand("spec:scope-a", mini, "off-map-read", "sess-5"),
                  cand("spec:view-later", levels, "recall-miss", "cccc3333"),      # two mechanical: waits
                  cand("spec:view-later", levels, "recall-miss", "dddd4444"),
                  {**cand("spec:view-later", levels, "recall-miss", "dddd4444"), "at": at(T0 + 9)}])   # a repeat
    m = kmap_store.open_map(root, cache=cache)
    got = kmap_found.pairs(m, kmap_found.read(cache / "candidates.jsonl"))
    status = {k: p["status"] for k, p in got.items()}
    assert status == {("file:" + split, "spec:island-drag"): "found", ("file:" + levels, "spec:theme-a"): "waiting",
                      ("file:" + mini, "spec:scope-a"): "found", ("file:" + levels, "spec:view-later"): "waiting"}
    rc, out = run(root, "island-drag", "--types", "found", "--depth", "1", cache=cache)
    assert rc == 0 and re.search(r"file src/ui/split.ts .*via found~", out), out
    rc, out = run(root, "candidates", "--review", cache=cache)
    rows = [ln for ln in out.splitlines() if re.match(r"^\| [0-9a-f]{6} \|", ln)]
    assert rc == 0 and len(rows) == 2, out
    view, theme = ("file:" + levels, "spec:view-later"), ("file:" + levels, "spec:theme-a")
    assert rows[0].startswith(f"| {pid(view)} | `{view[0]}` | `{view[1]}` | recall-miss x2: cccc3333 dddd4444")
    assert rows[1].startswith(f"| {pid(theme)} | `{theme[0]}` | `{theme[1]}` | self-report x3: sess-1 sess-2 sess-3")


def pid(pair):
    return importlib.import_module("kmap_review").pair_hash(pair)[:6]


def tree_of(tmp_path):
    root = tmp_path / "proj"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    return root


def test_accept_is_a_curated_link_and_reject_a_snapshot(tmp_path):
    root, cache = tree_of(tmp_path), tmp_path / "c"
    theme_a, theme_b = ("file:src/model/levels.ts", "spec:theme-a"), ("file:src/ui/split.ts", "spec:theme-b")
    write(cache, [cand("spec:theme-a", "src/model/levels.ts", "self-report", "sess-1"),
                  cand("spec:theme-b", "src/ui/split.ts", "self-report", "sess-2")])
    rc, out = run(root, "candidates", "--accept", pid(theme_a), "--reject", pid(theme_b), "--reason",
                  "only a word alike", cache=cache)
    assert rc == 0 and f"accepted: {pid(theme_a)} file:src/model/levels.ts ~ spec:theme-a" in out, out
    assert json.loads((cache / "review.json").read_text(encoding="utf-8"))["unreviewed"] == 0   # counted after it
    doc = (root / "docs/kmap/reviewed.md").read_text(encoding="utf-8")
    assert re.search(r"\| `file:src/ui/split.ts` \| `spec:theme-b` \| \d{4}-\d\d-\d\dT\d\d:\d\d \| architect \| "
                     r"only a word alike \| [0-9a-f]{12} [0-9a-f]{12} \| self-report: sess-2 \|", doc), doc
    rc, out = run(root, "candidates", cache=cache)
    assert "accepted 1, rejected 1" in out, out
    rc, out = run(root, "theme-a", "--types", "accepted", "--depth", "1", cache=cache)
    assert re.search(r"file src/model/levels.ts .*via accepted!", out), out


@kit.cases(
    (None, ""),
    ("end", "changed since: `file:src/ui/split.ts`"),
    ("two mechanical", "new evidence since: 2 mechanical, 0 self-report"),
    ("one mechanical", ""),                                                  # one new event is not enough
    ("mechanical and self-report", "new evidence since: 1 mechanical, 1 self-report"),
    ("30 days", "the rejection expired (31 days, 0 merges on its ends)"),
    ("20 merges on an end", "the rejection expired (2 days, 20 merges on its ends)"),
    ("25 unrelated merges", ""),                         # main's merges at large never expire it
)
def test_a_rejection_comes_back_when_its_snapshot_no_longer_holds(repo, tmp_path, change, back):
    """A rejection is a snapshot, not a ban; back in review it quotes the old reason and still
    waits for a reviewer, whatever its evidence."""
    m = kmap_store.open_map(repo[0], cache=tmp_path)
    a, b = "file:src/ui/split.ts", "spec:theme-b"
    row = {"ends": [a, b], "fps": [kmap_found.fp_of(m, a), kmap_found.fp_of(m, b)], "date": at(T0)[:16],
           "reviewer": "architect", "reason": "only a word alike"}
    if change == "end":
        row["fps"][0] = "0123456789ab"
    m.meta["reviewed"] = json.dumps({"accepted": [], "rejected": [row]})
    if change == "20 merges on an end":                  # 20 merges that changed split.ts after the rejection
        m.nodes[a]["extra"]["merged"] = [T0 + 3600 * (i + 1) for i in range(20)]
    if change == "25 unrelated merges":                  # main moved 25 times, none of them on split.ts
        m.nodes["file:src/ui/islands.ts"]["extra"]["merged"] = [T0 + 3600 * (i + 1) for i in range(25)]
    old = [cand(b, "src/ui/split.ts", k, e, T0 - 86400) for k, e in
           (("recall-miss", "s1"), ("co-change", "s2"), ("off-map-read", "sess-a"))]   # enough for a link, if not rejected
    new = {"two mechanical": [("recall-miss", "s3"), ("co-change", "s4")], "one mechanical": [("recall-miss", "s3")],
           "mechanical and self-report": [("off-map-read", "sess-b"), ("self-report", "sess-c")]}.get(change, [])
    cands = old + [cand(b, "src/ui/split.ts", k, e, T0 + 3600) for k, e in new]
    now = T0 + 86400 * (31 if change == "30 days" else 2)
    p = kmap_found.pairs(m, cands, now)[(a, b)]
    assert (p["status"], p["back"]) == (("waiting", back) if back else ("rejected", "")), p
    assert p["was"]["reason"] == "only a word alike"
    if back:
        assert importlib.import_module("kmap_review").before(p).startswith('rejected 2026-11-01 by architect: "only a '
                                                                           'word alike"; ')


def test_co_change_is_one_evidence_per_merge_and_three_merges_promote(repo, tmp_path):
    """Co-change >= 3 means 3 distinct merges, each changing both ends; one detection is one
    evidence, never its history's merges."""
    m = kmap_store.open_map(repo[0], cache=tmp_path)
    files = {"src/ui/split.ts", "src/model/levels.ts", "src/model/minimap.ts", "src/ui/Minimap.tsx"}
    one = kmap_found.co_change(m, files, "s1111111", T0)
    pairs = {(c["a"], c["b"]) for c in one}
    assert ("file:src/model/levels.ts", "src/ui/split.ts") in pairs and {c["evidence"] for c in one} == {"s1111111"}
    assert ("file:src/model/minimap.ts", "src/ui/Minimap.tsx") not in pairs      # linked (imports): left out
    key = ("file:src/model/levels.ts", "file:src/ui/split.ts")
    status = lambda cands: kmap_found.pairs(m, cands)[key]["status"]  # noqa: E731
    assert status(one) == "collecting"                                                  # 1 merge: a candidate only
    two = one + kmap_found.co_change(m, files, "s2222222", T0 + 1)
    assert status(two + kmap_found.co_change(m, files, "s2222222", T0 + 1)) == "collecting"   # the same merge again
    assert status(two + kmap_found.co_change(m, files, "s3333333", T0 + 2)) == "found"         # 3 distinct merges
    class Big:                                                  # a merge of more than CO_MAX code files says nothing
        nodes, adj = {f"file:f{i}.ts": {} for i in range(26)}, {}
    assert kmap_found.co_change(Big, {f"f{i}.ts" for i in range(26)}, "s4", T0) == []
    assert len(kmap_found.co_change(Big, {f"f{i}.ts" for i in range(25)}, "s4", T0)) == 25 * 24 // 2


class Ends:
    """A map of two files whose merge times a test writes (`extra.merged`, newest first)."""
    def __init__(self):
        self.nodes = {f"file:{x}": {"kind": "file", "extra": {"merged": []}} for x in ("a.ts", "b.ts")}
        self.adj, self.alias, self.meta = {}, {}, {}

    def merge(self, t, *files):
        for f in files:
            self.nodes[f"file:{f}"]["extra"]["merged"].insert(0, t)


@kit.cases((0, True), (19, True), (20, False))
def test_a_collecting_pair_waits_for_its_own_ends_not_main(tmp_path, one_sided, promotes):
    """Measured on a real pair: it co-changed 30, 68, 9 and 98 merges of main apart with nothing
    one-sided between: compacting after every merge, as live, it still reaches 3. 20 merges that change one end
    without the other drop it, and it starts again."""
    m, path, t, k = Ends(), tmp_path / "candidates.jsonl", T0, ("file:a.ts", "file:b.ts")
    status = None
    for n, gap in enumerate((30, 68, 9, 98)):
        if n == 1:                                              # before the 2nd co-change, a.ts moves on alone,
            for i in range(one_sided):                          # each merge compacting as the hook does
                m.merge(t + 60 * (i + 1), "a.ts")
                kmap_found.compact(m, path, t + 60 * (i + 1) + 1)
        t += gap * 630                                          # main's merges between: none touch a.ts or b.ts
        m.merge(t, "a.ts", "b.ts")
        append = kmap_found.append(path, kmap_found.co_change(m, {"a.ts", "b.ts"}, f"s{n}000000", t))
        kmap_found.compact(m, path, t + 1)
        assert append == 1
        if n == 2:
            status = kmap_found.pairs(m, kmap_found.read(path), t + 1)[k]["status"]
    assert status == ("found" if promotes else "collecting"), status


@kit.cases((0,), (20,))         # 20: the line was half written when compact read the file
def test_compact_keeps_a_line_appended_while_it_works(tmp_path, monkeypatch, cut):
    m, path = Ends(), tmp_path / "candidates.jsonl"
    m.merge(T0, "a.ts", "b.ts")
    kmap_found.append(path, kmap_found.co_change(m, {"a.ts", "b.ts"}, "s1000000", T0))
    late = (json.dumps(cand("spec:x", "a.ts", "off-map-read", "sess-late", T0 + 5)) + "\n").encode()
    path.write_bytes(path.read_bytes() + late[:cut])
    monkeypatch.setattr(kmap_found, "before_swap", lambda: path.write_bytes(path.read_bytes() + late[cut:]))
    assert kmap_found.compact(m, path, T0 + 31 * 86400) == 1                 # the stale pair goes
    assert [c["evidence"] for c in kmap_found.read(path)] == ["sess-late"]       # the append made meanwhile stays
    assert len(path.read_bytes().splitlines()) == 1


def test_expiry_by_rule():
    class M:
        nodes = {f"file:{x}": {"extra": {"merged": list(range(100, 90, -1)) if x == "a" else []}} for x in "ab"}
    assert kmap_found.expired(M, ("file:a", "spec:x"), 90) and not kmap_found.expired(M, ("file:a",), 91)


def test_a_module_s_recall_miss_promotes_after_three_merges(tmp_path):
    """A spec ~ file miss is linked by its merge at once; the spec's module ~ file miss is what can promote: three
    specs of one module, each merge missing the same file, make a found link (two keep it waiting, and the
    heartbeat's count then equals the review's rows)."""
    r = kit.Repo(tmp_path / "proj")
    r.write("docs/kmap/modules.md", "# Modules\n\n| Module | What | Seeds |\n|---|---|---|\n| `m1` | one part | `spec:s-*` |\n")
    for s in "abc":
        r.write(f"docs/specs/s-{s}.md", f"# S {s} (`s-{s}`)\n\nStatus: ready\n\n## What must be true\n\n1. **A.** `src/a.ts`\n")
    r.write("src/a.ts", "export const a = 1;\n")
    r.commit("one")
    r.write("src/z.ts", "export const z = 1;\n")
    r.commit("two")
    (r.path / "docs/specs/done").mkdir()
    cache, mod = tmp_path / "c", ("file:src/z.ts", "mod:m1")
    for n, s in enumerate("abc", 1):
        r.branch(f"s-{s}")
        r.write("src/a.ts", f"export const a = {n + 1};\n")
        r.write("src/z.ts", f"export const z = {n + 1};\n")
        r.git("mv", f"docs/specs/s-{s}.md", f"docs/specs/done/s-{s}.md")        # as a lane does: built
        r.commit(s)
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", f"s-{s}", "-m", f"Merge branch 's-{s}'")
        rc, out = run(r.path, "recall", r.git("rev-parse", "HEAD"), "--batch", f"s-{s}", "--log", cache=cache)
        assert rc == 0 and "missed=src/z.ts" in out, out
        got = kmap_found.pairs(kmap_store.open_map(r.path, cache=cache), kmap_found.read(cache / "candidates.jsonl"))
        assert got[mod]["status"] == ("found" if n == 3 else "waiting"), (n, got[mod])
        beat = json.loads((cache / "review.json").read_text(encoding="utf-8"))["unreviewed"]
        _, review = run(r.path, "candidates", "--review", cache=cache)
        assert beat == len([ln for ln in review.splitlines() if re.match(r"^\| [0-9a-f]{6} \|", ln)]), review
    assert got[("file:src/z.ts", "spec:s-a")]["status"] == "linked"                  # the spec's own pair: inert


def test_review_ids_are_stable_across_a_merge_and_a_stale_id_refuses(tmp_path):
    root, cache = tree_of(tmp_path), tmp_path / "c"
    a, b = ("file:src/model/levels.ts", "spec:theme-a"), ("file:src/ui/split.ts", "spec:theme-b")
    write(cache, [cand("spec:theme-a", "src/model/levels.ts", "recall-miss", "m1"),
                  cand("spec:theme-a", "src/model/levels.ts", "recall-miss", "m2"),
                  cand("spec:theme-b", "src/ui/split.ts", "recall-miss", "m3")])
    _, out = run(root, "candidates", "--review", cache=cache)
    assert out.index(f"| {pid(a)} |") < out.index(f"| {pid(b)} |"), out          # a first: what "1" meant
    def add(*c):
        with (cache / "candidates.jsonl").open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(x) + "\n" for x in c)
    add(cand("spec:theme-b", "src/ui/split.ts", "off-map-read", "sess-9"))      # a merge lands: b ranks first now
    _, out = run(root, "candidates", "--review", cache=cache)
    assert out.index(f"| {pid(b)} |") < out.index(f"| {pid(a)} |"), out
    rc, out = run(root, "candidates", "--accept", pid(a), cache=cache)              # the id still names a
    assert rc == 0 and f"accepted: {pid(a)} {a[0]} ~ {a[1]}" in out and b[1] not in out, out
    add(cand("spec:theme-b", "src/ui/split.ts", "recall-miss", "m4"))          # b: 3 mechanical, a found link
    rc, out = run(root, "candidates", "--reject", pid(b), "--reason", "x", cache=cache)
    assert rc == 2 and f"id {pid(b)}" in out and "is found now" in out and "nothing written" in out, out
    rc, out = run(root, "candidates", "--accept", "abcdef", cache=cache)
    assert rc == 2 and "id abcdef: no such pair" in out, out
    assert "theme-b" not in (root / "docs/kmap/reviewed.md").read_text(encoding="utf-8")


def test_ids_are_the_shortest_unique_prefix_and_a_shared_one_refuses(monkeypatch):
    review = importlib.import_module("kmap_review")
    fake = {("a", "b"): "abcdef0123", ("c", "d"): "abcdef1999", ("e", "f"): "9876543210"}
    monkeypatch.setattr(review, "pair_hash", lambda k: fake[k])
    got = {k: {"status": "waiting"} for k in fake}
    assert review.ids_of(got) == {("a", "b"): "abcdef0", ("c", "d"): "abcdef1", ("e", "f"): "987654"}
    assert review.picks("abcdef1", got) == ([(("c", "d"), got[("c", "d")])], "")
    assert review.picks("abcdef", got) == (None, "id abcdef: 2 pairs start so (abcdef0, abcdef1); run --review again")
    assert review.picks("abcd", got)[1].startswith("id abcd: shorter than 6 hex")
    assert review.picks("987654,abcdef", got)[0] is None                      # one bad id: nothing is taken


def test_stale_collecting_pairs_leave_and_queries_read_the_found_cache(repo, tmp_path):
    root, _ = repo
    cache = tmp_path / "c"
    split, levels = "src/ui/split.ts", "src/model/levels.ts"
    now = T0 + 40 * 86400
    write(cache, [cand("spec:island-drag", split, "recall-miss", "aaaa1111"), cand("spec:island-drag", split, "co-change",
                  "bbbb2222"), cand("spec:island-drag", split, "off-map-read", "sess-x"),          # found: stays
                  cand("file:src/ui/islands.ts", levels, "co-change", "cccc3333"),             # collecting, 40 days
                  cand("file:src/ui/islands.ts", split, "co-change", "dddd4444", now - 86400),  # collecting, fresh
                  cand("spec:theme-a", levels, "recall-miss", "eeee5555")])                        # waiting: stays
    m = kmap_store.open_map(root, cache=cache)
    assert kmap_found.compact(m, cache / "candidates.jsonl", now) == 1
    left = {(c["a"], c["b"]) for c in kmap_found.read(cache / "candidates.jsonl")}
    assert ("file:src/ui/islands.ts", levels) not in left and len(left) == 3, left
    m.nodes["file:src/ui/islands.ts"]["extra"]["merged"] = [now - 60 * i for i in range(20)]  # 20 one-sided merges
    assert kmap_found.compact(m, cache / "candidates.jsonl", now) == 1
    kmap_found.write_found(kmap_store.open_map(root, cache=cache), cache)
    found = json.loads((cache / "found.json").read_text(encoding="utf-8"))
    assert [e[:3] for e in found["edges"]] == [["file:" + split, "found", "spec:island-drag"]]
    found["edges"] = [["spec:island-drag", "found", "file:src/ui/Minimap.tsx", 0.5, "FOUND", "planted"]]
    (cache / "found.json").write_text(json.dumps(found), encoding="utf-8")
    _, out = run(root, "island-drag", "--types", "found", "--depth", "1", cache=cache)   # the cache, not the log
    assert "file src/ui/Minimap.tsx" in out and "split.ts" not in out, out
    with (cache / "candidates.jsonl").open("a", encoding="utf-8") as f:                   # the log moved on: recount
        f.write(json.dumps(cand("spec:theme-a", levels, "self-report", "sess-7")) + "\n")
    _, out = run(root, "island-drag", "--types", "found", "--depth", "1", cache=cache)
    assert "file src/ui/split.ts" in out and "Minimap.tsx" not in out, out


def test_merge_log_soft_fails_on_a_hang_or_an_error(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("merge_log_k", MERGE_LOG)
    ml = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ml)
    (tmp_path / ".git").mkdir()                                     # a checkout, as far as merge-log asks
    merges = tmp_path / "docs" / "MERGES.md"
    merges.parent.mkdir()
    merges.write_text("# M\n\n<!-- merge-log.py adds each new entry below this line, newest first -->\n\n", encoding="utf-8")
    for body, word in (("import time; time.sleep(30)", "not measured (TimeoutExpired)"),
                       ("import sys; sys.exit(3)", "not measured (kmap exit 3)")):
        tool = tmp_path / "kmap.py"
        tool.write_text(body + "\n", encoding="utf-8")
        monkeypatch.setattr(ml, "KMAP", tool)
        monkeypatch.setattr(ml, "RECALL_TIMEOUT", 1)
        assert ml.main(["--file", str(merges), "--batch", "x", "--sha", "abc12345", "--what", "w", "--up", "no",
                        "--try", "t", "--from", "`x`", "--when", "2026-11-28 10:00"]) == 0
        assert f"- **Recall:** {word}\n" in merges.read_text(encoding="utf-8")


def test_export_never_writes_outside_mesh(tmp_path):
    out = tmp_path / "v" / "mesh"
    out.mkdir(parents=True)
    assert importlib.import_module("kmap_cmds").put(out, "spec/../../escape.md", "x") == "outside"
    assert not (tmp_path / "v" / "escape.md").exists()


def test_the_heartbeat_nudges_for_a_review(tmp_path):
    if BEAT is None:
        raise unittest.SkipTest("no tools/work/coord-beat.py (a solo deploy)")
    spec = importlib.util.spec_from_file_location("coord_beat_k", BEAT)
    beat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(beat)
    (tmp_path / "kmap").mkdir()
    now = T0 * 1000
    for n, days, due in ((21, 0, True), (20, 1, False), (1, 7, True), (0, 30, False)):
        (tmp_path / "kmap" / "review.json").write_text(json.dumps(
            {"unreviewed": n, "since": T0 - days * 86400, "max": 20, "days": 7}), encoding="utf-8")
        assert bool(beat.review(tmp_path, now)) == due, (n, days)
    assert beat.review(tmp_path / "none", now) == []
    (tmp_path / "kmap" / "candidates.jsonl").write_text("{}\n", encoding="utf-8")   # lines after its count: marked
    (tmp_path / "kmap" / "review.json").write_text(json.dumps(
        {"unreviewed": 21, "since": T0, "max": 20, "days": 7, "size": 0}), encoding="utf-8")
    assert beat.review(tmp_path, now)[0].startswith("kmap: 21+ candidate links to review")


Found = kit.suite(globals(), repo=build_repo)
