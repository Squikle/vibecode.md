"""metrics.py: python -I -X utf8 -m unittest discover -s tests/metrics (from the kit folder)."""
import json, sqlite3, sys, tempfile, unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "metrics"))
import metrics as m  # noqa: E402

HOUR = 3600 * 1000


def make_db(path, msgs):
    con = sqlite3.connect(path)
    con.execute("create table session_v2 (id text, parent_id text)")
    con.execute("create table session_message (id text, session_id text, type text, time_created int, time_updated int, data text)")
    con.executemany("insert into session_v2 values (?, ?)", [("top", None), ("kid", "top")])
    for mid, sid, t, prov, done in msgs:
        data = {"model": {"id": "big", "providerID": prov}, "tokens": {"input": 10, "output": 2, "cache": {"read": 100, "write": 4}},
                "time": {"completed": t if done else None}}
        con.execute("insert into session_message values (?, ?, 'assistant', ?, ?, ?)", (mid, sid, t, t, json.dumps(data)))
    con.commit()
    con.close()


class Metrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.data = self.dir / "data"
        self.data.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_weighted_units(self):
        self.assertEqual(m.wu(10, 2, 100, 4), 10 + 10 + 10 + 5)

    def test_week_start_follows_the_reset(self):
        anchor = datetime(2026, 11, 23, 17).timestamp()
        for t in (datetime(2026, 11, 25, 12).timestamp(), anchor, datetime(2026, 12, 7, 16).timestamp()):
            ws = m.week_start(t, anchor)
            self.assertLessEqual(ws, t)
            self.assertLess(t - ws, m.WEEK)
            self.assertEqual(datetime.fromtimestamp(ws).strftime("%a %H:%M"), "Mon 17:00")
        self.assertEqual(m.week_start(anchor - 1, anchor), anchor - m.WEEK)

    def test_units_between_counts_the_overlap(self):
        h = int(datetime(2026, 11, 25, 10).timestamp() * 1000)
        t0 = h / 1000 + 900
        self.assertAlmostEqual(m.units_between({h: 400.0}, t0, t0 + 1800), 200.0)

    def test_per_day_left(self):
        self.assertAlmostEqual(m.per_day_left(60, 4 * 86400, 0), 10.0)

    def test_chart_shape(self):
        s = m.chart("bar", ["a b", "c"], [("x", [1, None])], stacked=True)
        self.assertIn('labels: ["a b", "c"]', s)
        self.assertIn("data: [1, null]", s)
        self.assertIn("stacked: true", s)

    def test_usage_deltas_roles_providers_and_no_double_count(self):
        db = self.dir / "o.db"
        now = int(datetime(2026, 11, 25, 12).timestamp() * 1000)
        make_db(db, [("a", "top", now - HOUR, "p1", True), ("b", "kid", now - HOUR, "p1", True),
                     ("c", "kid", now - HOUR, "p2", True), ("d", "top", now - HOUR, "p1", False)])
        st = {}
        self.assertEqual(m.collect_usage(st, self.data, db, {"p1"}, now), 2)
        rows = m.read_all(self.data, "usage")
        self.assertEqual(sorted(r["r"] for r in rows), ["child", "top"])
        self.assertTrue(all(r["n"] == 1 and r["i"] == 10 for r in rows))
        self.assertEqual(len(m.read_all(self.data, "lanes")), 1)
        self.assertEqual(m.collect_usage(st, self.data, db, {"p1"}, now), 0)  # the same messages again: nothing new
        self.assertEqual(len(m.read_all(self.data, "usage")), 2)

    def test_limits_take_the_newer_sample_once(self):
        pacer = self.dir / "pacer"
        pacer.mkdir()
        u = lambda p: {"h5": {"pct": p, "reset": 1.0}, "d7": {"pct": p + 1, "reset": 2.0}, "scoped": {"Top": 7}}
        (pacer / "plugin-state.json").write_text(json.dumps({"pace": {"at": 100.0, "usage": u(10), "mode": "GO"}}))
        (pacer / "state.json").write_text(json.dumps({"at": 200.0, "usage": u(20), "mode": "EASE"}))
        st = {}
        self.assertEqual(m.collect_limits(st, self.data, pacer), 1)
        self.assertEqual(m.collect_limits(st, self.data, pacer), 0)
        row = m.read_all(self.data, "limits")[0]
        self.assertEqual((row["h5"], row["d7"], row["mode"], row["sc"]), (20, 21, "EASE", {"Top": 7}))

    def test_gates_once_per_verdict_file(self):
        ci = self.dir / "work" / "ci"
        ci.mkdir(parents=True)
        (ci / "abcd1234.json").write_text(json.dumps({"verdict": "GREEN", "seconds": 5}))
        (ci / "other.json").write_text("[]")
        st = {}
        self.assertEqual(m.collect_gates(st, self.data, self.dir / "work"), 1)
        self.assertEqual(m.collect_gates(st, self.data, self.dir / "work"), 0)

    def test_render_flags_a_short_week(self):
        now = datetime(2026, 11, 25, 12).timestamp()
        reset = datetime(2026, 11, 30, 17).timestamp()
        m.append(self.data, "limits", [{"t": now - 600, "h5": 30, "d7": 80, "d7r": reset, "sc": {"Top": 40}, "mode": "SLOW"}])
        m.append(self.data, "usage", [{"h": int(now * 1000) // HOUR * HOUR - HOUR, "m": "big", "r": "top", "i": 1, "o": 1, "cr": 1, "cw": 1, "n": 1}])
        page = m.render(self.data, None, 14, now=now)
        for part in ("## Limits", "## Week", "## Tokens", "## Quota per token", "## Work", "| Top (week) | **40%** |"):
            self.assertIn(part, page)
        self.assertIn("⚠️ under one a day", page)  # 20 % left over ~5.2 days: under 14 % a day
        self.assertIn("✅", m.render(self.data, None, 2, now=now))

    def test_main_always_exits_0_and_logs(self):
        argv = ["--data", str(self.data), "--pacer", str(self.dir / "none"), "--db", str(self.dir / "none.db"),
                "--work", str(self.dir / "none"), "--page", str(self.dir / "Metrics.md")]
        self.assertEqual(m.main(argv), 0)
        self.assertEqual(m.main(["--bogus"]), 0)
        log = (self.data / "metrics.log").read_text(encoding="utf-8")
        self.assertIn("limits+0", log)
        self.assertIn("usage+-", log)
        self.assertTrue((self.dir / "Metrics.md").is_file())


if __name__ == "__main__":
    unittest.main()
