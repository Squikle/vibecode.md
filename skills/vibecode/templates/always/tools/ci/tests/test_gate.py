"""gate.py's locks and CPU admission, with no git and no real CPU: `python -I -X utf8 -m unittest discover -s tools/ci/tests`."""
import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("gate", Path(__file__).resolve().parents[1] / "gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.main = Path(self.tmp.name)
        self.config = {"main": str(self.main), "lock_minutes": 50, "wait_minutes": 0.001, "cpu_max": 90}
        self.hooks = (gate.CPU, gate.ALIVE, gate.STARTED, gate.WAIT_S)
        gate.CPU, gate.ALIVE, gate.STARTED, gate.WAIT_S = (lambda: 10.0), (lambda pid: True), (lambda pid: None), 0.01
        gate.SAY_LOG[:] = []

    def tearDown(self):
        gate.CPU, gate.ALIVE, gate.STARTED, gate.WAIT_S = self.hooks
        gate.SAY_LOG[:] = []
        self.tmp.cleanup()

    def slot_file(self, room: str, k: int, text: str) -> Path:
        path = self.main / ".work" / "ci" / room / f"slot{k}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class Slots(Base):
    def test_dead_pid_and_reused_pid(self):
        path = self.slot_file("cpu", 1, "gate-aaaa 2026-01-01T00:00:00\npid 4242")
        self.assertTrue(gate.live(path, gate.hold_text(path)))
        gate.STARTED = lambda pid: time.time() + 60  # started after the slot was written: another process now
        self.assertFalse(gate.live(path, gate.hold_text(path)))
        gate.STARTED, gate.ALIVE = (lambda pid: None), (lambda pid: False)
        self.assertFalse(gate.live(path, gate.hold_text(path)))

    def test_full_slots_wait_then_waited(self):
        self.slot_file("cpu", 1, f"gate-aaaa 2026-01-01T00:00:00\npid {os.getpid()}")
        slot = gate.Slot(self.config, "cpu", 1, "gate-bbbb", "unit", top=90)
        waited = slot.take(time.monotonic() + 0.05)
        self.assertIn("1 of 1 cpu slots", waited)
        self.assertIsNone(slot.path)

    def test_dead_slot_taken_over(self):
        self.slot_file("cpu", 1, "gate-aaaa 2026-01-01T00:00:00\npid 4242")
        gate.ALIVE = lambda pid: False
        slot = gate.Slot(self.config, "cpu", 1, "gate-bbbb", "unit")
        self.assertIsNone(slot.take(time.monotonic() + 1))
        self.assertTrue(gate.hold_text(slot.path).startswith("gate-bbbb"))
        slot.release()
        self.assertFalse(slot.path and slot.path.exists())

    def test_cpu_holds_back_only_beside_another_run(self):
        gate.CPU = lambda: 97.0
        first = gate.Slot(self.config, "cpu", 3, "gate-aaaa", "unit", top=90)
        self.assertIsNone(first.take(time.monotonic() + 1))  # the first run is never held back
        second = gate.Slot(self.config, "cpu", 3, "gate-bbbb", "unit", top=90)
        self.assertIn("the CPU under 90%", second.take(time.monotonic() + 0.05))
        first.release()

    def test_release_frees_only_its_own(self):
        slot = gate.Slot(self.config, "cpu", 1, "gate-aaaa", "unit")
        self.assertIsNone(slot.take(time.monotonic() + 1))
        path = slot.path
        path.write_text("gate-cccc later\npid 1", encoding="utf-8")
        slot.release()
        self.assertEqual(gate.hold_text(path), "gate-cccc later\npid 1")

    def test_who_takes_a_slot(self):
        self.assertTrue(gate.loads({"name": "unit"}))
        self.assertFalse(gate.loads({"name": "types", "light": True}))
        self.assertFalse(gate.loads({"name": "mac", "remote": True}))  # another machine's run is not this one's load
        self.assertTrue(gate.software({"name": "e2e", "renderer": "software"}))
        self.assertFalse(gate.software({"name": "e2e", "renderer": "gpu"}))
        self.assertFalse(gate.software({"name": "mac", "renderer": "software", "remote": True}))

    def test_waits_go_to_the_gate_log(self):
        log = self.main / "gate.log"
        gate.SAY_LOG[:] = [log]
        self.slot_file("software", 1, f"gate-aaaa 2026-01-01T00:00:00\npid {os.getpid()}")
        gate.Slot(self.config, "software", 1, "gate-bbbb", "e2e").take(time.monotonic() + 0.03)
        gate.say("$ e2e", console=False)
        text = log.read_text(encoding="utf-8")
        self.assertIn("e2e: waiting for 1 of 1 software slots, held by gate-aaaa since 2026-01-01T00:00:00", text)
        self.assertTrue(text.endswith("$ e2e\n"))


class Locks(Base):
    def test_drop_if_keeps_a_hold_taken_since(self):
        path = self.main / "x.lock"
        path.write_text("new 2", encoding="utf-8")
        self.assertFalse(gate.drop_if(path, "old 1"))
        self.assertEqual(gate.hold_text(path), "new 2")
        self.assertTrue(gate.drop_if(path, "new 2"))
        self.assertFalse(path.exists())
        self.assertEqual([p.name for p in self.main.iterdir()], [])

    def test_stale_lock_taken_over_and_release_frees_only_its_own(self):
        lock = gate.Lock(self.config, ["e2e"], "gate-aaaa", tag="e2e")
        path = lock.paths[0]
        if gate.WINDOWS:
            path.write_text("old 1", encoding="utf-8")
        else:
            path.mkdir()
            (path / "who").write_text("old 1\n", encoding="utf-8")
        os.utime(path, (time.time() - 3600 * 2,) * 2)
        self.assertIsNone(lock.take(time.monotonic() + 1))
        self.assertTrue(gate.hold_text(path).startswith("gate-aaaa"))
        lock.stop.set()
        (path / "who" if path.is_dir() else path).write_text("other 3", encoding="utf-8")
        lock.release()
        self.assertEqual(gate.hold_text(path), "other 3")

    def test_full_gate_takes_any_free_stack(self):
        self.config.update({"parts": [{"name": "e2e", "lock": ["e2e", "e2e2"], "paths": ["*"]}]})
        keep = gate.changed_files
        gate.changed_files = lambda sha, base: ["a.py"]
        try:
            full = gate.build_plan(self.config, "f" * 40, "origin/main", None, None)
            pinned = gate.build_plan(self.config, "f" * 40, "origin/main", None, None, stack=2)
        finally:
            gate.changed_files = keep
        self.assertEqual(full["parts"][0]["try_locks"], ["e2e", "e2e2"])
        self.assertEqual(pinned["parts"][0]["try_locks"], ["e2e2"])


if __name__ == "__main__":
    unittest.main()
