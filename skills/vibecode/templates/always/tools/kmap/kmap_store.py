"""The knowledge map's cache: one SQLite file per HEAD and worktree state (the size and time of every source the
build reads, and the tool's own code), in the main checkout's `.work/kmap/` unless `--cache` or `KMAP_CACHE` names
another folder (a lane that may not write the main tree, a test). The 8 most recently used are kept; a build writes a
temp file and renames it, so two lanes building one state keep whichever landed."""
import collections
import hashlib
import io
import json
import os
import posixpath
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path

from kmap_build import Builder
from kmap_found import found_cached, found_edges
from kmap_parse import BACK, CODE, KEEP_DBS, REVERSE, SCHEMA, SKIPPED, git_top, run_git, walk

CANDIDATES, RECALL_LOG = "candidates.jsonl", "recall.jsonl"   # in the cache folder (the main tree's `.work/kmap/`)

HERE = Path(__file__).resolve().parent
OWN_CODE = sorted(HERE.glob("*.py"))


def main_tree(root):
    """The main checkout (the folder holding `.git`) of a worktree; the root itself outside git."""
    common = run_git(root, "rev-parse", "--git-common-dir") if git_top(root) else None
    if not common:
        return root
    p = Path(common.strip())
    p = p if p.is_absolute() else (root / p)
    return p.resolve().parent if p.name == ".git" else root


def cache_dir(root, cache=None):
    return Path(cache or os.environ.get("KMAP_CACHE") or main_tree(root) / ".work" / "kmap")


def own_hash():
    h = hashlib.sha1(f"{SCHEMA}".encode())
    for f in OWN_CODE:
        h.update(f.read_bytes() if f.is_file() else b"-")
    return h.hexdigest()


def state_key(root, git=True):
    """HEAD plus the size and time of every source the build reads plus the tool's own bytes."""
    h = hashlib.sha1(f"{own_hash()} {git}".encode())
    listing = run_git(root, "ls-files", "-co", "--exclude-standard") if git else None
    paths = listing.splitlines() if listing is not None else [p.relative_to(root).as_posix() for p in walk(root)]
    for p in sorted(paths):
        if p.startswith("docs/") or posixpath.splitext(p)[1] in CODE:
            try:
                st = (root / p).stat()
            except OSError:
                continue
            h.update(f"{p}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    head = ((run_git(root, "rev-parse", "HEAD") if git else None) or "nogit").strip()[:12]
    return f"{head}-{h.hexdigest()[:10]}"


def write_db(path, b, seconds):
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    db = sqlite3.connect(tmp)
    db.executescript("""
        create table node(id text primary key, kind text, l0 text, path text, status text, extra text);
        create table edge(src text, type text, dst text, weight real, conf text, evidence text,
                          primary key(src, type, dst));
        create table alias(alias text primary key, id text);
        create table issue(level text, text text);
        create table meta(k text primary key, v text);""")
    db.executemany("insert into node values (?,?,?,?,?,?)",
                   [(k, n["kind"], n["l0"], n["path"], n["status"], json.dumps(n["extra"])) for k, n in b.nodes.items()])
    db.executemany("insert into edge values (?,?,?,?,?,?)", [(s, t, d, *v) for (s, t, d), v in b.edges.items()])
    db.executemany("insert into alias values (?,?)", list(b.alias.items()))
    db.executemany("insert into issue values (?,?)", b.issues)
    db.executemany("insert into meta values (?,?)", [("seconds", f"{seconds:.2f}"), ("built", str(int(time.time()))),
                                                     ("git", "yes" if b.git else "no"),
                                                     ("reviewed", json.dumps(getattr(b, "reviewed", {})))])
    db.commit()
    db.close()
    try:
        os.replace(tmp, path)
    except OSError:  # another lane built the same state and has it open (Windows): theirs is as good
        tmp.unlink(missing_ok=True)
        if not path.is_file():
            raise


def prune(folder):
    dbs = sorted(folder.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dbs[KEEP_DBS:]:
        try:
            old.unlink()
        except OSError:
            pass  # another process holds it; the next prune gets it


class Map:
    def __init__(self, path):
        db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        self.nodes = {r[0]: {"kind": r[1], "l0": r[2] or "", "path": r[3], "status": r[4] or "",
                             "extra": json.loads(r[5] or "{}")} for r in db.execute("select * from node")}
        self.edges = list(db.execute("select src, type, dst, weight, conf, evidence from edge"))
        self.alias = dict(db.execute("select alias, id from alias"))
        self.issues = list(db.execute("select level, text from issue"))
        self.meta = dict(db.execute("select k, v from meta"))
        db.close()
        self.adj = collections.defaultdict(list)
        for s, t, d, w, conf, ev in self.edges:
            self.adj[s].append((d, t, t, w, conf))
            self.adj[d].append((s, t, REVERSE.get(t, t), w * BACK.get(t, 1.0), conf))
        self.deg = {n: len(v) for n, v in self.adj.items()}
        self.root = None                      # the working tree a level 3 answer reads its line ranges from

    def add_found(self, edges):
        """The `found` links of the candidates file, added when the map is opened (never stored in the cache)."""
        for s, t, d, w, conf, ev in edges:
            self.edges.append((s, t, d, w, conf, ev))
            self.adj[s].append((d, t, t, w, conf))
            self.adj[d].append((s, t, t, w, conf))
        self.deg = {n: len(v) for n, v in self.adj.items()}


def open_map(root, force=False, cache=None, git=True):
    git = git and git_top(root)
    folder = cache_dir(root, cache)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{state_key(root, git)}.db"
    if force or not path.is_file():
        b = Builder(root, git)
        seconds = b.build()
        write_db(path, b, seconds)
        prune(folder)
    else:
        os.utime(path)  # most recently used
    m = Map(path)
    m.root, m.meta["key"] = root, path.name
    m.add_found(found_cached(m, folder))                  # the merge hook writes it; a query never pairs the log
    return m


def unpack(repo, sha):
    """{path: bytes} of the docs and code of `sha` (`git archive`, ~50 MB in memory; a piped stream hung once on a
    stderr nobody drained): never a checkout of a worktree."""
    out = {}
    p = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", sha], capture_output=True, timeout=60)
    if p.returncode:
        raise RuntimeError(f"git archive {sha} failed in {repo}: {p.stderr.decode('utf-8', 'replace').strip()[-200:]}")
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tf:
        for t in tf:
            name = t.name
            if not t.isfile() or name.startswith("/") or ".." in name.split("/") or SKIPPED & set(name.split("/")[:-1]):
                continue
            if name.startswith("docs/") or posixpath.splitext(name)[1] in CODE:
                out[name] = tf.extractfile(t).read()
    return out


def open_map_at(repo, rev, cache=None, found=False):
    """The map of commit `rev` as it was (its files read from git in memory, its first-parent history), cached by the
    sha and the tool's code. `found`: with the found links of the candidates written before that commit."""
    sha = (run_git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}") or "").strip()
    if not sha:
        raise SystemExit(f"kmap: no commit {rev} in {repo}")
    folder = cache_dir(repo, cache)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"at-{sha[:12]}-{own_hash()[:10]}.db"
    if not path.is_file():
        t0 = time.time()
        b = Builder(Path(repo), git=False, history=(repo, sha), blobs=unpack(repo, sha))
        b.build()
        write_db(path, b, time.time() - t0)
        prune(folder)
    else:
        os.utime(path)
    m = Map(path)
    m.meta["sha"] = sha
    if found:
        m.add_found(found_edges(m, folder / CANDIDATES, int(run_git(repo, "show", "-s", "--format=%ct", sha) or 0)))
    return m
