"""Deploy the vibecode templates into a project folder.

Usage: python -I -X utf8 deploy.py <target> <answers.json>

- Copies templates/<group>/** into <target> for each group whose flag is on:
  always, team (team or fleet), solo, reviewers (all or risky), e2e (e2e or perf), remote.
- Fills {{KEY}} from the answers (missing keys stay visible as {{KEY}}).
- Keeps <!-- if:flag --> ... <!-- endif --> blocks only when the flag is on; `if:!flag` negates; blocks may nest.
- Never overwrites a file; templates named `gitignore` / `gitattributes` are appended to the dotfiles line by line.
- Prints what it created and skipped, then docs/PROMPTS.md's "Start here" part.
"""
import json, pathlib, re, sys

base = pathlib.Path(__file__).resolve().parent.parent / "templates"
target = pathlib.Path(sys.argv[1]).resolve()
a = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))

scale, rev, tests = a.get("SCALE", "team"), a.get("REVIEWERS", "all"), a.get("TESTS", "e2e")
flags = {
    "always": True,
    "team": scale in ("team", "fleet"),
    "fleet": scale == "fleet",
    "solo": scale == "solo",
    "reviewers": rev in ("all", "risky"),
    "reviewers_all": rev == "all",
    "reviewers_risky": rev == "risky",
    "e2e": tests in ("e2e", "perf") or bool(a.get("REMOTE")),  # remote runs go through lock.sh
    "perf": tests == "perf",
    "remote": bool(a.get("REMOTE")),
    "cap": a.get("USAGE") == "cap",
    "local": a.get("USAGE") == "local",
    "windows": a.get("LOCAL_OS") == "windows",
}
a.setdefault("REVIEW_STEP", {"all": "every batch", "risky": "batches marked `Review: yes` in their spec", "none": "no batch"}[rev])


def on(flag: str) -> bool:
    return not flags.get(flag[1:], False) if flag.startswith("!") else flags.get(flag, False)


BLOCK = re.compile(r"<!-- if:(!?\w+) -->\r?\n?((?:(?!<!-- if:).)*?)<!-- endif -->\r?\n?", re.S)  # innermost first


def fill(text: str) -> str:
    while True:
        new = BLOCK.sub(lambda m: m.group(2) if on(m.group(1)) else "", text)
        if new == text:
            break
        text = new
    return re.sub(r"\{\{([A-Z_0-9]+)\}\}", lambda m: str(a.get(m.group(1), m.group(0))), text)


created, skipped = [], []
for group in ["always", "team", "solo", "reviewers", "e2e", "remote"]:
    root = base / group
    if not flags[group] or not root.exists():
        continue
    for src in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = src.relative_to(root)
        text = fill(src.read_text(encoding="utf-8"))
        if rel.name in ("gitignore", "gitattributes"):
            dst = target / rel.parent / ("." + rel.name)
            have = dst.read_text(encoding="utf-8").splitlines() if dst.exists() else []
            new = [l for l in text.splitlines() if l and l not in have]
            if new:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text("\n".join(have + new) + "\n", encoding="utf-8", newline="\n")
                created.append(str(dst.relative_to(target)) + " (lines added)")
            continue
        dst = target / rel
        if dst.exists():
            skipped.append(str(rel))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8", newline="\n")
        created.append(str(rel))

for d in ["docs/specs/done", ".work"]:
    (target / d).mkdir(parents=True, exist_ok=True)

print(f"created {len(created)}:")
for c in created:
    print("  " + c)
if skipped:
    print(f"skipped (already there) {len(skipped)}:")
    for s in skipped:
        print("  " + s)
left = sorted({m for c in created if not c.endswith(")") for m in re.findall(r"\{\{([A-Z_0-9]+)\}\}", (target / c).read_text(encoding="utf-8"))})
if left:
    print("UNFILLED placeholders:", ", ".join(left))
prompts = (target / "docs/PROMPTS.md")
if prompts.exists():
    t = prompts.read_text(encoding="utf-8")
    i, j = t.find("## Start here"), t.find("\n## ", t.find("## Start here") + 5)
    print("\n" + (t[i:j] if i >= 0 else t[:3000]))
