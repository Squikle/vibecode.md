# python -I tests/pacer/pacer_cases.py   (pacer.py's decide(): the modes, week on and off)
import json, pathlib, sys, tempfile, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "plugins" / "pacer"))
import pacer

tmp = pathlib.Path(tempfile.mkdtemp(prefix="pacer-cases-"))
pacer.TOGGLES = tmp / "toggles.json"

now = time.time()
def u(p5, left5_min, p7, left7_days):
    return {"h5": {"pct": p5, "reset": now + left5_min * 60}, "d7": {"pct": p7, "reset": now + left7_days * 86400}, "scoped": {}}

def week(on):
    pacer.TOGGLES.write_text(json.dumps({"week": on}))

# week on: the original 8 cases keep their meaning
week_on = [
    ("fresh window, week fine", u(10, 280, 20, 5), "GO"),
    ("5h hot early", u(60, 240, 20, 5), "SLOW"),
    ("5h 92%, 2h left", u(92, 120, 20, 5), "HOLD"),
    ("5h 92%, 40 min left, week fine", u(92, 40, 20, 5), "BURST"),
    ("40 min left but week far ahead", u(70, 40, 60, 5), "SLOW"),
    ("limit hit", u(100, 90, 20, 5), "LIMITED"),
    ("week hit", u(10, 200, 100, 3), "LIMITED"),
    ("today's real-ish", u(37, 173, 30, 6.2), "EASE"),
]
# week off (the default): the week is ignored, except a hit week limit (the API refuses then anyway)
week_off = [
    ("week off: 40 min left, week far ahead -> BURST", u(70, 40, 60, 5), "BURST"),
    ("week off: 5h on pace, week far ahead -> GO", u(10, 280, 60, 5), "GO"),
    ("week off: week hit still LIMITED", u(10, 200, 100, 3), "LIMITED"),
    ("week off: 5h hot early still SLOW", u(60, 240, 20, 5), "SLOW"),
]
bad = 0
def run(cases, on):
    global bad
    week(on)
    for name, x, want in cases:
        d = pacer.decide(x, now)
        ok = d["mode"] == want and d["week"] is on
        bad += not ok
        print("ok " if ok else "BAD", f"{name}: {d['mode']} lanes<={d['lanes']} ({d['why']}) want {want}")

run(week_on, True)
run(week_off, False)
pacer.TOGGLES.unlink(missing_ok=True)
d = pacer.decide(u(70, 40, 60, 5), now)  # no toggles.json at all = week off
ok = d["mode"] == "BURST"; bad += not ok
print("ok " if ok else "BAD", f"no toggles.json -> week off: {d['mode']}")
# explicit param beats the file
d = pacer.decide(u(70, 40, 60, 5), now, week=True)
ok = d["mode"] == "SLOW"; bad += not ok
print("ok " if ok else "BAD", f"week=True param: {d['mode']}")
tmp.rmdir()
print("bad", bad)
sys.exit(1 if bad else 0)
