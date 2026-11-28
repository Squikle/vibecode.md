"""`tools/jobs/jobs.py`: each job's automation runs one Python command on either OS (no pwsh), and `sync` plans the
smallest change: keep a matching job, replace a changed one, delete one the manifest dropped, never touch another's.
`python -I -X utf8 -m unittest discover -s tools/jobs/tests`."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import jobs  # noqa: E402

MAN = jobs.load()
PRE = MAN["prefix"]
TEL = PRE + "role-telemetry"


class Jobs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_commands_are_python_on_both_systems(self):
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform):
                want = jobs.desired(MAN, self.tmp, python="/usr/bin/python3", platform=platform)
                self.assertEqual(set(want), {TEL})
                cmd = want[TEL]["action"]["command"]
                self.assertTrue(cmd.startswith("/usr/bin/python3 -I -X utf8 "), cmd)
                self.assertTrue(cmd.endswith("tools/jobs/jobs.py run role-telemetry"), cmd)
                self.assertTrue("pwsh" not in cmd and "\\" not in cmd, cmd)
        for j in MAN["jobs"]:
            self.assertTrue(j["run"][0].endswith(".py") and (ROOT / j["run"][0]).is_file(), j["run"][0])

    def test_a_path_with_a_space_is_quoted_per_system(self):
        self.assertEqual(jobs.quoted("C:/Program Files/py.exe", "win32"), '& "C:/Program Files/py.exe"')
        self.assertEqual(jobs.quoted("/opt/my py/python3", "darwin"), '"/opt/my py/python3"')

    def test_sync_plans_the_smallest_change(self):
        want = jobs.desired(MAN, self.tmp, python="py")
        body = want[TEL]
        same = {"id": "aut_1", "name": body["name"], "status": "active", "trigger": body["trigger"],
                "action": body["action"]}
        self.assertEqual(jobs.plan(want, [same], PRE), ([], []))
        changed = {**same, "trigger": {"type": "every", "every": "1 hour"}}
        self.assertEqual(jobs.plan(want, [changed], PRE), ([body], ["aut_1"]))
        gone = {"id": "aut_2", "name": PRE + "old", "status": "active", "trigger": {}, "action": {}}
        other = {"id": "aut_3", "name": "stale-check", "status": "active", "trigger": {}, "action": {}}
        self.assertEqual(jobs.plan(want, [same, gone, other], PRE), ([], ["aut_2"]))

    def test_sync_creates_nothing_live_without_apply(self):
        real = jobs.api
        try:
            for apply in (False, True):
                calls = []
                jobs.api = lambda *args: calls.append(args[0]) or '{"data": []}'
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    jobs.sync(MAN, apply, self.tmp)
                self.assertEqual(calls, ["experimental.automation.list"]
                                 + (["experimental.automation.create"] if apply else []))
                self.assertNotEqual("dry run" in out.getvalue(), apply)
        finally:
            jobs.api = real

    def test_alerts_come_from_the_manifest(self):
        job = next(j for j in MAN["jobs"] if j["name"] == "role-telemetry")
        self.assertTrue(job["alert_exit"] == [1] and not hasattr(jobs, "ALERT_EXIT"))
        self.assertEqual(jobs.quoted_arg("C:/My Repo/tools/jobs/jobs.py"), '"C:/My Repo/tools/jobs/jobs.py"')

    def test_run_rejects_an_unknown_job(self):
        with self.assertRaises(SystemExit):
            jobs.run(MAN, "nope")


if __name__ == "__main__":
    unittest.main()
