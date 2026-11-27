"""The vault's logs (`tools/vault/vaultify.py` `parse_log`, `--logs`): one note per event line with its fields as
properties, an index note per log, Bases views, a committed log's bad line a miss (exit 1), a local one's a `log_misses`
count (exit 0), the cap, and the gate's history lines (`tools/ci/gate.py` `history_lines`) in the shape the vault reads.

python -I -X utf8 -m unittest discover -s tools/vault/tests      (stdlib only)"""
import importlib.util, io, json, shutil, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import vaultify  # noqa: E402

DOCS = {
    "AGENTS.md": "# Agents\n",
    "docs/BACKLOG.md": "# Backlog\n\n| # | Batch | Status | What |\n|---|---|---|---|\n| 1 | `alpha` The first | open | a thing |\n",
}
LOG = """# The coordinator's log

Prose about the log is not an event.

- 2026-11-26 18:49 GATE verdict=GREEN sha=00c389b5 seconds=241 parts="unit 4774/0, docs 51/1" #ci
- 2026-11-26 18:50 SPAWN: `alpha` builder model=p/m minutes=12.5 cached=true
  the first line of its body
  and the second
2026-11-26 19:02 a line with no type and a [[docs/HANDOVER]] link
- 2026-11-26 19:03 DONE `alpha` type=wrong date=wrong
```
2026-11-26 19:04 IN-FENCE not an event
```
"""


def put(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(text.encode("utf-8"))
    return root


def call(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = vaultify.main(list(args))
    return code, json.loads(out.getvalue().strip().splitlines()[-1]), err.getvalue()


def front(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    head = text.split("---\n")[1]
    return {k: (v if k == "rev" else json.loads(v)) for k, v in (ln.split(": ", 1) for ln in head.splitlines())}


class Parse(unittest.TestCase):
    def test_an_event_line_and_its_fields(self):
        events, misses = vaultify.parse_log(LOG)
        self.assertEqual(misses, [])
        self.assertEqual([e["type"] for e in events], ["gate", "spawn", "note", "done"])
        gate, spawn, plain, done = events
        self.assertEqual(gate["props"], {"verdict": "GREEN", "sha": "00c389b5", "seconds": 241, "parts": "unit 4774/0, docs 51/1"})
        self.assertEqual(gate["tags"], ["ci"])
        self.assertEqual((spawn["day"], spawn["time"], spawn["ids"]), ("2026-11-26", "18:50", ["alpha"]))
        self.assertEqual(spawn["props"], {"model": "p/m", "minutes": 12.5, "cached": True})
        self.assertEqual(spawn["more"], ["the first line of its body", "and the second"])
        self.assertTrue(plain["text"].startswith("a line with no type"))
        self.assertEqual(done["props"], {}, "the build's own keys (type, date) stay text")

    def test_a_dated_line_off_the_shape_is_a_miss(self):
        _, misses = vaultify.parse_log("- 2026-11-26: colon after the date\n2026-13-01 10:00 X month 13\n"
                                       "2026-11-26 25:00 HOUR no\n2026-11-26\n# 2026-11-26 a heading is not one\n")
        self.assertEqual([n for n, _ in misses], [1, 2, 3, 4])

    def test_the_readme_examples_read_as_it_says(self):
        text = (HERE / "README.md").read_text(encoding="utf-8")
        events, misses = vaultify.parse_log(text.split("## Logs", 1)[1].split("```", 2)[1])
        self.assertEqual((misses, [e["type"] for e in events]), ([], ["gate", "spawn"]))
        self.assertEqual(events[1]["more"], ["an indented line right under an event is its body"])
        block = text.split("## A project whose events are not merges", 1)[1].split("```", 2)[1]
        events, misses = vaultify.parse_log(block)
        self.assertEqual((misses, [e["type"] for e in events]), ([], ["source", "claim", "finding", "deploy", "incident"]))
        self.assertEqual((events[1]["props"]["confidence"], events[3]["props"]["version"], events[4]["tags"]), (0.7, "2.4.1", ["rollback"]))

    def test_an_all_digit_sha_stays_text_and_a_leading_zero_too(self):
        (e,), _ = vaultify.parse_log("2026-11-26 GATE sha=12345678 n=007 x=1.5.\n")
        self.assertEqual(e["props"], {"sha": "12345678", "n": "007", "x": 1.5})
        self.assertEqual(e["time"], "")


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src, self.vault = put(self.tmp / "src", DOCS), self.tmp / "vault"
        self.log = put(self.tmp / "work", {"coordinator-log.md": LOG}) / "coordinator-log.md"

    def build(self, *more):
        return call("--src", str(self.src), "--vault", str(self.vault), *more)

    def test_one_note_per_event_an_index_and_the_views(self):
        code, res, _ = self.build("--logs", str(self.log))
        self.assertEqual((code, res["log_events"], res["log_misses"]), (0, 4, 0))
        notes = sorted((self.vault / "log/coordinator-log").glob("*.md"))
        self.assertEqual(len(notes), 4)
        spawn = front(next(p for p in notes if "-spawn-" in p.name))
        self.assertEqual((spawn["type"], spawn["date"], spawn["day"], spawn["log"]), ("spawn", "2026-11-26 18:50", "2026-11-26", "coordinator-log"))
        self.assertEqual((spawn["batches"], spawn["minutes"], spawn["cached"]), (["alpha"], 12.5, True))
        self.assertIn("[[batches/alpha]]", next(p for p in notes if "-spawn-" in p.name).read_text(encoding="utf-8"))
        index = (self.vault / "log/coordinator-log.md").read_text(encoding="utf-8")
        self.assertIn("4 events, 2026-11-26 18:49 to 2026-11-26 19:03", index)
        self.assertIn("| gate | 1 | 2026-11-26 18:49 |", index)
        self.assertIn("[[log/coordinator-log]] (4 events)", (self.vault / "index.md").read_text(encoding="utf-8"))
        logs = (self.vault / "Logs.base").read_text(encoding="utf-8")
        self.assertIn('file.inFolder("log")', logs)
        self.assertIn("'note.type == \"red\"'", logs)
        self.assertTrue((self.vault / "Batches.base").is_file())

    def test_no_logs_with_src_unless_given(self):
        code, res, _ = self.build()
        self.assertEqual((code, res["log_events"]), (0, 0))
        self.assertFalse((self.vault / "log").exists())

    def test_a_local_bad_line_is_counted_not_failed(self):
        self.log.write_text(LOG + "2026-11-26: off the shape\n", encoding="utf-8")
        code, res, err = self.build("--logs", str(self.log))
        self.assertEqual((code, res["misses"], res["log_misses"]), (0, 0, 1))
        self.assertIn("log-miss:", err)
        self.assertIn("## Not events (1)", (self.vault / "log/coordinator-log.md").read_text(encoding="utf-8"))

    def test_a_committed_bad_line_is_a_miss(self):
        put(self.src, {"docs/log/research/sources.md": "- 2026-11-26 SOURCE a paper url=https://x.org\n2026-11-31 bad day\n"})
        code, res, err = self.build()
        self.assertEqual((code, res["misses"], res["log_events"]), (1, 1, 1))
        self.assertIn("docs/log/research/sources.md:2", err)
        self.assertTrue((self.vault / "log/research-sources.md").is_file())

    def test_an_appended_line_writes_its_note_and_the_index_only(self):
        self.build("--logs", str(self.log))
        with self.log.open("a", encoding="utf-8") as f:
            f.write("- 2026-11-27 09:00 DEPLOY v2 version=2.10\n")
        _, res, _ = self.build("--logs", str(self.log))
        self.assertEqual(res["written"], 3, "the new note, the log's index, the hub")
        (new,) = (self.vault / "log/coordinator-log").glob("2026-11-27-0900-deploy-*.md")
        self.assertEqual(front(new)["version"], "2.10")

    def test_the_cap_keeps_the_newest(self):
        lines = "".join(f"- 2026-11-{1 + i // 1440:02d} {i // 60 % 24:02d}:{i % 60:02d} TICK n={i}\n" for i in range(30))
        self.log.write_text(lines, encoding="utf-8")
        old, vaultify.LOG_KEEP = vaultify.LOG_KEEP, 10
        self.addCleanup(setattr, vaultify, "LOG_KEEP", old)
        _, res, _ = self.build("--logs", str(self.log))
        notes = sorted((self.vault / "log/coordinator-log").glob("*.md"))
        self.assertEqual((res["log_events"], len(notes)), (30, 10))
        self.assertEqual(front(notes[0])["n"], 20)

    def test_a_missing_logs_path_is_a_bad_argument(self):
        with self.assertRaises(SystemExit) as e, redirect_stderr(io.StringIO()):
            vaultify.main(["--src", str(self.src), "--vault", str(self.vault), "--logs", str(self.tmp / "nope.md")])
        self.assertEqual(e.exception.code, 2)


GATE = HERE.parent / "ci" / "gate.py"


@unittest.skipUnless(GATE.is_file(), "no tools/ci/gate.py")
class GateHistory(unittest.TestCase):
    def test_the_gate_writes_the_shape_the_vault_reads(self):
        spec = importlib.util.spec_from_file_location("gate_for_vault", GATE)
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        red = {"id": "a.spec.ts::opens", "name": 'opens the "file" menu', "file": "e2e/a.spec.ts"}
        flaky = {"id": "b::saves", "name": "saves", "file": ""}
        r = {"sha": "c389b513deadbeef", "verdict": "NEW-RED", "mode": "full", "seconds": 241, "new_flaky": [{"part": "e2e", "id": "b::saves"}],
             "parts": [{"name": "unit", "machine": "pc", "passed": 4774, "failed": 0},
                       {"name": "e2e", "machine": "mac", "passed": 40, "failed": 2, "new": [red], "new_flaky": ["b::saves"],
                        "reds": [red, flaky]}]}
        events, misses = vaultify.parse_log("\n".join(gate.history_lines(r, "2026-11-26 18:49")))
        self.assertEqual(misses, [])
        self.assertEqual([e["type"] for e in events], ["gate", "red", "flaky"])
        self.assertEqual(events[0]["props"], {"verdict": "NEW-RED", "sha": "c389b513", "mode": "full", "seconds": 241, "new": 1,
                                              "flaky": 1, "parts": "unit 4774/0, e2e 40/2"})
        self.assertEqual(events[1]["props"], {"test": "opens the 'file' menu", "part": "e2e", "machine": "mac",
                                              "sha": "c389b513", "file": "e2e/a.spec.ts"})
        self.assertEqual(events[2]["props"]["test"], "saves")


if __name__ == "__main__":
    unittest.main()
