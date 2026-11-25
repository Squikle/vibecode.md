# python -I tests/pacer/pacer_tick_cases.py   (pacer.py's tick: claims, wakes, the coordinator switch)
import json, pathlib, sys, tempfile, time, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "plugins" / "pacer"))
import pacer

tmp = pathlib.Path(tempfile.mkdtemp(prefix="pacer-tick-"))
tmp.mkdir(exist_ok=True)
pacer.STATE, pacer.CLAIM, pacer.LOG, pacer.SAMPLES = (tmp / n for n in ("state.json", "claim.json", "log", "samples"))
pacer.TOGGLES = tmp / "toggles-tick.json"  # legacy tick cases were written with the week counted
pacer.TOGGLES.write_text(json.dumps({"week": True}))
for p in (pacer.STATE, pacer.CLAIM):
    p.unlink(missing_ok=True)

T = [time.time()]
pacer.time.time = lambda: T[0]
sent = []
pacer.send = lambda c, m: sent.append((c["sid"], m)) or 1
SESS = []
pacer.sessions = lambda d: SESS
U = {}
pacer.usage = lambda: (U["u"], None)

def setu(p5, left5_min, p7=20, left7_days=5):
    U["u"] = {"h5": {"pct": p5, "reset": T[0] + left5_min * 60}, "d7": {"pct": p7, "reset": T[0] + left7_days * 86400}, "scoped": {}}

def tick():
    n = len(sent)
    pacer.cmd_tick(types.SimpleNamespace(send=True))
    return sent[n][1].split(" ")[2].rstrip(":") if len(sent) > n else None

def claim(sid, created):
    pacer.CLAIM.write_text(json.dumps({"sid": sid, "dir": str(tmp), "at": T[0]}))

bad = 0
def check(name, got, want):
    global bad
    ok = got == want
    bad += not ok
    print("ok " if ok else "BAD", f"{name}: got {got!r} want {want!r}")

# coordinator A claims, works, then the 5 h limit hits 2 h before reset and lasts 2 h
SESS[:] = [{"id": "A", "title": "Project coordinator", "created": 1, "updated": T[0] * 1000}]
claim("A", 1); setu(50, 200); check("fresh, busy coordinator", tick(), None)
T[0] += 1800; setu(100, 120); check("limit hit", tick(), None)
T[0] += 7200; setu(3, 300); SESS[0]["updated"] = (T[0] - 7300) * 1000
check("reset after 2 h: claim 2.5 h old but live at the stop", tick(), "RESET")
T[0] += 3600; check("1 h later, still idle, claim stale -> quiet", tick(), None)

# The user switches: A claimed, B (new coordinator) claims -> B gets wakes, A never
claim("A", 1); SESS[:] = [{"id": "A", "title": "Project coordinator", "created": 1, "updated": (T[0] - 3600) * 1000}]
T[0] += 60; setu(10, 280); check("A idle 61 min, claim fresh -> HEARTBEAT to A", tick(), "HEARTBEAT")
SESS.append({"id": "B", "title": "coordinator resume", "created": 2, "updated": T[0] * 1000})
T[0] += 3000; check("new coordinator B active, unclaimed -> quiet", tick(), None)
claim("B", 2); T[0] += 60; SESS[1]["updated"] = T[0] * 1000; check("B claimed, busy -> quiet", tick(), None)
T[0] += 3600; SESS[1]["updated"] = (T[0] - 3100) * 1000; setu(10, 200)
check("B idle 52 min -> HEARTBEAT to B", tick(), "HEARTBEAT")
check("target is B", sent[-1][0], "B")

# HOLD message
T[0] += 3600; claim("B", 2); SESS[1]["updated"] = (T[0] - 3100) * 1000; setu(92, 150)
w = tick(); check("HOLD heartbeat", w, "HEARTBEAT"); check("HOLD wording", "warm-only" in sent[-1][1], True)
print("bad", bad)
sys.exit(1 if bad else 0)
