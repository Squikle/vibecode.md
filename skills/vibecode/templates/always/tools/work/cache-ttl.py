"""After an idle gap, was the prompt cache cold (a big write) or warm (a big read)? Per gap bucket, over sessions.

Usage: python -I -X utf8 tools/work/cache-ttl.py <session id> [...]

Counts each assistant turn with over 30k of context that follows a gap; cold = the cache write is over half of it.
This is how the keep-alive interval was chosen (OPTIMIZATIONS.md): measure your own provider before changing it.
Reads `opencode session export`, writes nothing.
"""
import json
import subprocess
import sys

BUCKETS = ["<5m", "5-15m", "15-60m", ">60m"]


def bucket(gap_min: float) -> str:
    return "<5m" if gap_min < 5 else "5-15m" if gap_min < 15 else "15-60m" if gap_min < 60 else ">60m"


def main(sids: list[str]) -> int:
    if not sids:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    counts = {}
    for sid in sids:
        out = subprocess.run(["opencode", "session", "export", sid], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", shell=True).stdout
        if "{" not in out:
            print(f"{sid}: no export")
            continue
        prev = None
        for m in json.loads(out[out.find("{"):]).get("messages", []):
            if m.get("type") != "assistant":
                continue
            t = m.get("time") or {}
            c = (m.get("tokens") or {}).get("cache") or {}
            ctx = (m.get("tokens") or {}).get("input", 0) + c.get("read", 0) + c.get("write", 0)
            if prev and ctx > 30000:
                b = bucket((t.get("created", 0) - prev) / 60000)
                cold, n = counts.get(b, (0, 0))
                counts[b] = (cold + (c.get("write", 0) > 0.5 * ctx), n + 1)
            prev = t.get("completed") or t.get("created")
    for b in BUCKETS:
        if b in counts:
            cold, n = counts[b]
            print(f"gap {b:7} turns {n:5}  cold {cold:4} ({100 * cold // n}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
