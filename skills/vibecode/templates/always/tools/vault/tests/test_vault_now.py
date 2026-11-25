"""The vault's `Now` note (`tools/vault/vault_now.py`): the coordinator's job tables in plain words, read tolerantly, in
order (building, reviewing, gates, waiting, done), linked to the batch notes, with no session id or code name; a skip when
nothing changed; one log line per run; exit 0 always. And vaultify keeps its hands off `Now.md` and embeds it in the hub.

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

    def run_now(self, *extra, text=CHECKPOINT):
        if text is not None and (not self.src.is_file() or self.src.read_text(encoding="utf-8") != text):
            self.src.write_text(text, encoding="utf-8")
            os.utime(self.src, (MTIME, MTIME))
        with redirect_stderr(io.StringIO()):
            return now.main(["--src", str(self.src), "--vault", str(self.vault), *extra])

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
        states, order = [s for s, _ in got], ["building", "reviewing", "gate", "waiting", "done"]
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
