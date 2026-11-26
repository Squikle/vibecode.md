"""The vault's `Now` note (`tools/vault/vault_now.py`): the coordinator's job tables in plain words, read tolerantly, in
order (building, reviewing, gates, idle, waiting, done), linked to the batch notes, with no session id or code name; each
job's last trace (Seen) from fixture `.work` files and gate runs, `idle` after IDLE_MIN; a skip when nothing changed; one
log line per run; exit 0 always. And vaultify keeps its hands off `Now.md` and embeds it in the hub.

python -I -X utf8 -m unittest discover -s tools/vault/tests      (stdlib only)"""
import io, json, os, re, shutil, sys, tempfile, time, unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vault_now as now  # noqa: E402
import vaultify  # noqa: E402

CHECKPOINT = """# Coordinator checkpoint

## Models

| Role | Model |
|---|---|
| builder | `top-lane#high` |

## State

| Role (to the owner) | My tag | Session | Batch | wt | Head | Next |
|---|---|---|---|---|---|---|
| builder | RONE | `ses_fake01` | `rename-one` | rone | `1e3fbb26` | full gate `--base 64e116ee` → READY |
| lane | GWAIT | `ses_fake02` top-lane | `gate-wait` | gwait | `5538f634` | spawned 21:59; then `gate-gaps` same lane |
| plugin builder | NEWS | `ses_fake03` | `todo-click-check` | `.work\\news` | live | the owner tried it 21:55: buttons overflow → `READY todo-panel-fix` 22:00 → waits on their 5-step click check; then a review |
| reviewer | REVLP | `ses_fake04` | `label-pass` lane B | lpass | x | reviewing `--base d25dff69` from 22:05; RONE waits behind it |
| job | HOME | `ses_fake05` | (none) | `.work\\home-sync` | - | the owner 22:10: sync habits → READY 22:18 → HOME done, retire |
| CTO | (23:50) | automation `aut_fake06` | `cto-heavy` | cto5 | local main | the owner said yes 22:03: tech gate of four specs; no push |
| lane | broken |
| bisect | INTBIS | `ses_fake07` | main's split reds | main | - | run A3 `dc96d554` (stack 1) → `CULPRIT <test> <sha> <batch>` |
| lane | CTRLD | `ses_fake08` | `ctrl-d-bookmark` | ctrld | `5538f634` | spawned 22:01; failing browser case first | a drifted extra cell |
| fixer | FIXR | `ses_fake07` | main reds ses_fake07 | fixr | - | fixing since 22:30 |
| helper | - | - | `gate-wait` after RONE | gw2 | - | waits on its merge and SPLIT |

| lane | SPLIT | `ses_fake09` | `split-row` | sp | - | spawned 22:40; then the same REV checks it |

**Next moves:** none.

| Batch | Role | State |
|---|---|---|
| `docs-sync` | docs sync | syncing the docs, since 21:52 |
"""
TAGS = ("RONE", "GWAIT", "NEWS", "REVLP", "HOME", "INTBIS", "CTRLD", "FIXR", "SPLIT")
MTIME = 1764019680

T = time.mktime((2026, 11, 25, 22, 0, 0, 0, 0, -1))                     # the live fixture's 22:00, local
LIVE = """| Role (to the owner) | Tag | Session | Batch | wt | Head | Next |
|---|---|---|---|---|---|---|
| builder | AONE | `ses_aaa1` | `alpha` | alpha | `aaaa1111` | spawned 22:00 → building the parser |
| builder | BTWO | `ses_bbb2` top-lane | `beta` | beta | `bbbb2222` base `99999999` | spawned 22:00 → tests first |
| reviewer | CREV | `ses_ccc3` | `gamma` | gamma | `cccc3333` | reviewing from 22:00 |
| lane | DFOUR | `ses_ddd4` | `delta` | delta | base 99999999 | spawned 22:00 → fixing the parser |
| lane | EFIVE | `ses_eee5` | `eps` | eps | - | spawned 22:49 → idle, no word yet |
| lane | FSIX | `ses_fff6` | `phi` | phi | - | spawned 22:0x → waits on the owner |
| lane | GSEV | `ses_ggg7` | `chi` | chi | - | merged 22:00 → done |
"""


def touch(p, minutes, text="x\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))
    os.utime(p, (T + minutes * 60, T + minutes * 60))


class Now(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        v = self.vault = self.tmp / "vault"
        for bid, title in (("gate-wait", "The gate: a lock's wait is not the run's time"), ("label-pass", "A block's label pass"),
                           ("rename-one", "A rename is one block"), ("ctrl-d-bookmark", "Ctrl+D bookmarks"), ("cto-heavy", "")):
            p = v / "batches" / f"{bid}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f'---\nid: "{bid}"\ntitle: "{title}"\n---\nbody\n', encoding="utf-8")
        (v / ".vaultify").mkdir()
        (v / ".vaultify/build.json").write_text('{"rev": "aaaa1111", "tool": "t"}\n', encoding="utf-8")
        self.src, self.log = self.tmp / "coordinator-now.md", self.tmp / "vault-now.log"
        hooks = (now.NOW, now.IDLE_MIN, now.ALIVE, now.STARTED)
        self.addCleanup(lambda: setattr(now, "NOW", hooks[0]) or setattr(now, "IDLE_MIN", hooks[1])
                        or setattr(now, "ALIVE", hooks[2]) or setattr(now, "STARTED", hooks[3]))
        now.NOW, now.IDLE_MIN = (lambda: MTIME + 60), 10 ** 6   # the fixture's clocks span an hour: nothing goes idle

    def run_now(self, *extra, text=CHECKPOINT):
        if text is not None and (not self.src.is_file() or self.src.read_text(encoding="utf-8") != text):
            self.src.write_text(text, encoding="utf-8")
            os.utime(self.src, (MTIME, MTIME))
        with redirect_stderr(io.StringIO()):
            return now.main(["--src", str(self.src), "--vault", str(self.vault), "--trees", str(self.tmp / "wt"), *extra])

    def text(self):
        return (self.vault / "Now.md").read_text(encoding="utf-8")

    def rows(self):
        """The job rows of Now.md: (state, the whole row)."""
        return [(r.split("|")[1].strip(), r) for r in self.text().splitlines()
                if r.startswith("| ") and not r.startswith(("| State", "|---"))]

    def one(self, needle):
        return [r for _, r in self.rows() if needle in r][0]

    def test_every_job_in_plain_words_in_order(self):
        self.assertEqual(self.run_now(), 0)
        text, got = self.text(), self.rows()
        states, order = [s for s, _ in got], ["building", "reviewing", "gate", "idle", "waiting", "done"]
        self.assertEqual(len(got), 12)
        self.assertEqual(states, sorted(states, key=order.index))
        split = self.one("split-row")                                     # rows after a blank line inside the table
        self.assertTrue("the reviewer checks it" in split and "the the" not in split)
        gw = self.one("[[batches/gate-wait\\|gate-wait]]")                # `\|`: a link's alias inside a table
        self.assertTrue(gw.startswith("| building |") and "lock's wait" in gw and "21:59" in gw)
        nobr = self.one("docs-sync")                                      # no batch note: said so, not linked
        self.assertTrue("[[" not in nobr and "no backlog row" in nobr and "21:52" in nobr)
        todo = self.one("todo-click-check")
        self.assertTrue(todo.startswith("| waiting |") and "their 5-step click check" in todo and "22:00" in todo)
        rone = self.one("[[batches/rename-one")
        self.assertTrue(rone.startswith("| gate |") and "READY" in rone.split("|")[-2])   # what comes next
        cto = self.one("cto-heavy")                                       # an automation's job waits on its time
        self.assertTrue(cto.startswith("| waiting |") and "23:50" in cto and "| 22:03 |" in cto)
        self.assertTrue(self.one("main's split reds").startswith("| gate |"))
        self.assertTrue(self.one("label-pass").startswith("| reviewing |") and "lane B" in self.one("label-pass"))
        self.assertTrue(self.one("retire").startswith("| done |"))
        self.assertIn("a drifted extra cell", text)                       # an extra cell joins the state, never lost
        self.assertEqual(re.findall(r"couldn't read row \d+", text), ["couldn't read row 19"])   # its line
        self.assertTrue("Model" not in text and "top-lane" not in text)   # a table that is no job table is skipped
        built = time.strftime("%Y-%m-%d %H:%M", time.localtime(MTIME))
        self.assertIn(f"built {built}", text.splitlines()[5])

    def test_no_session_ids_and_no_code_names(self):
        self.run_now()
        text = self.text()
        self.assertIsNone(re.search(r"ses_|aut_", text))
        self.assertEqual([t for t in TAGS if re.search(rf"\b{t}\b", text)], [])
        self.assertIn("the builder (rename-one) waits behind it", text)   # a code name in the text reads as the role
        self.assertIn("main reds, no backlog row", text)                  # the Batch cell too
        self.assertIn("after the builder (rename-one)", text)

    def test_a_table_with_no_role_column_lists_an_unread_row(self):
        text = CHECKPOINT + "\n| Batch | State |\n|---|---|\n|  | stuck since 22:40 |\n"
        self.assertEqual(self.run_now(text=text), 0)
        out = self.text()
        self.assertIn(f"couldn't read row {len(CHECKPOINT.splitlines()) + 4}", out)
        self.assertIn("docs-sync", out)

    def test_if_changed_skips_and_every_run_logs_one_line(self):
        self.assertEqual(self.run_now("--if-changed"), 0)
        first = (self.vault / "Now.md").read_bytes()
        t0 = time.perf_counter()
        self.assertEqual(self.run_now("--if-changed"), 0)
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertEqual((self.vault / "Now.md").read_bytes(), first)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(len(lines) == 2 and " built " in lines[0] and " skip" in lines[1])
        (self.vault / "Now.md").unlink()                                  # Now.md gone: built again
        self.run_now("--if-changed")
        self.assertEqual((self.vault / "Now.md").read_bytes(), first)
        (self.vault / ".vaultify/build.json").write_text('{"rev": "bbbb2222"}\n', encoding="utf-8")   # the vault moved
        self.run_now("--if-changed", text=CHECKPOINT.replace("spawned 21:59", "spawned 21:58"))
        self.assertIn("21:58", self.text())
        self.assertEqual([l.split()[2] for l in self.log.read_text(encoding="utf-8").splitlines()],
                         ["built", "skip", "built", "built"])

    def test_a_missing_source_logs_and_exits_0(self):
        self.assertEqual(self.run_now("--if-changed", text=None), 0)
        self.assertFalse((self.vault / "Now.md").exists())
        self.assertIn(" error ", self.log.read_text(encoding="utf-8"))

    def live_traces(self):
        """Seven jobs at 22:50 with traces in the fixture's `.work`, a worktree and gate run folders (pid 4242 lives)."""
        now.NOW, now.IDLE_MIN = (lambda: T + 50 * 60), 30
        now.ALIVE, now.STARTED = (lambda pid: pid == 4242), (lambda pid: None)
        touch(self.tmp / "alpha" / "progress.md", 40)                       # the main tree's .work
        self.gate("aaaa1111.only", "ses_other", 999, 45)                    # by Head: a dead gate, no verdict
        self.gate("a0000000", "ses_aaa1", 4242, -7 * 60)                    # older than 6 h: not a live gate
        touch(self.tmp / "wt" / "beta" / ".work" / "beta" / "progress.md", 45)   # the lane's worktree
        self.gate("bbbb2222.only", "ses_other", 4242, 10, end=30)           # by Head, ended
        self.gate("beee0000.only", "ses_bbb2", 4242, 20, end=48)            # by session, ended last
        touch(self.tmp / "gamma" / "review-2.md", 5)                        # a reviewer's trace, 45 min old
        touch(self.tmp / "delta" / "progress.md", 1)
        self.gate("dddd4444.only", "ses_ddd4", 4242, 2, crlf=True)          # by session, running (a Windows `\r\n`)
        touch(self.tmp / "chi" / "report.md", 49)

    def gate(self, name, session, pid, start, end=None, crlf=False):
        """A gate's run folder `ci/<name>/gate.pid` written at `start` (minutes after T), its verdict at `end`."""
        eol = "\r\n" if crlf else "\n"
        touch(self.tmp / "ci" / name / "gate.pid", start, f"pid {pid}{eol}session {session}{eol}")
        os.utime(self.tmp / "ci" / name, (T + start * 60, T + start * 60))
        if end is not None:
            touch(self.tmp / "ci" / f"{name}.json", end, '{"verdict": "GREEN"}\n')

    def test_seen_shows_each_jobs_last_trace_and_idle_jobs(self):
        self.live_traces()
        self.run_now(text=LIVE)
        text = self.text()
        self.assertIn("| State | Job | Batch | Now | Since | Seen | Next |", text)
        got = {r.split("|")[3].strip(): (s, r.split("|")[6].strip()) for s, r in self.rows()}
        self.assertEqual(got, {"alpha, no backlog row": ("building", "22:45 gate aaaa1111 lost"),
                               "beta, no backlog row": ("building", "22:48 gate beee0000 ended"),
                               "gamma, no backlog row": ("idle", "22:05 review"),
                               "delta, no backlog row": ("gate", "22:02 gate dddd4444 running"),
                               "eps, no backlog row": ("idle", "-"),
                               "phi, no backlog row": ("waiting", "-"),
                               "chi, no backlog row": ("done", "22:49 report")})
        self.assertEqual([s for s, _ in self.rows()], ["building", "building", "gate", "idle", "idle", "waiting", "done"])
        self.assertIn("| 22:0x |", text)                                     # a `hh:mx` clock is a clock
        self.assertNotIn("a0000000", text)

    def test_a_running_gate_shows_beside_a_newer_trace(self):
        self.live_traces()
        touch(self.tmp / "delta" / "progress.md", 47)
        self.run_now(text=LIVE)
        delta = self.one("delta")
        self.assertTrue(delta.startswith("| gate |") and "| 22:47 progress · gate dddd4444 running since 22:02 |" in delta)

    def test_the_skip_holds_until_a_trace_moves_or_a_job_goes_idle(self):
        self.live_traces()
        self.run_now("--if-changed", text=LIVE)
        alpha = lambda: self.one("alpha")
        now.NOW = lambda: T + 75 * 60                                       # 30 min after alpha's 22:45: not idle yet
        self.run_now("--if-changed", text=LIVE)                             # only the clock moved: a skip
        self.assertTrue("| 22:45 gate aaaa1111 lost |" in alpha() and alpha().startswith("| building |"))
        touch(self.tmp / "alpha" / "progress.md", 50)                       # a new trace
        self.run_now("--if-changed", text=LIVE)
        self.assertTrue("| 22:50 progress |" in alpha() and alpha().startswith("| building |"))
        self.run_now("--if-changed", text=LIVE)
        now.NOW = lambda: T + 81 * 60                                       # 31 min on: it went idle
        self.run_now("--if-changed", text=LIVE)
        self.assertTrue(alpha().startswith("| idle |"))
        self.assertEqual([l.split()[2] for l in self.log.read_text(encoding="utf-8").splitlines()],
                         ["built", "skip", "built", "skip", "built"])

    def test_vaultify_keeps_now_and_embeds_it_in_the_hub(self):
        src, vault = self.tmp / "src", self.tmp / "vault2"
        for rel, text in {"AGENTS.md": "# Agents\n", "docs/BACKLOG.md": "# backlog\n"}.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_bytes(text.encode("utf-8"))

        def build():
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                vaultify.main(["--src", str(src), "--vault", str(vault)])
            return json.loads(out.getvalue().strip().splitlines()[-1])

        build()
        hub = (vault / "index.md").read_text(encoding="utf-8")
        self.assertTrue((vault / "Now.md").is_file())                    # a stub until vault_now writes it
        self.assertTrue("![[Now]]" in hub and hub.index("![[Now]]") < hub.index("\n## "))
        (vault / "Now.md").write_text("the jobs\n", encoding="utf-8")
        (src / "docs/BACKLOG.md").write_bytes(b"# backlog, changed\n")
        out = build()
        self.assertEqual((vault / "Now.md").read_text(encoding="utf-8"), "the jobs\n")
        self.assertEqual(out["conflicts"], 0)
        self.assertNotIn("Now.md", json.loads((vault / ".vaultify/manifest.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
