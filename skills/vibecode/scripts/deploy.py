"""Deploy the vibecode templates into a project folder.

Usage: python -I -X utf8 deploy.py <target> <answers.json> [--no-vault]

- Copies templates/<group>/** into <target> for each group whose flag is on:
  always, team (team or fleet), solo, reviewers (all or risky), e2e (e2e or perf), remote.
- The docs vault (tools/vault/) is on: in a repository whose top is <target> it installs its git hooks
  (tools/vault/hook.py install); else it prints the command for after `git init`. `--no-vault` or `"VAULT": false`
  in the answers leaves tools/vault/ out (the gate and integrate skip it).
- Fills {{KEY}} from the answers (missing keys stay visible as {{KEY}}).
- Keeps <!-- if:flag --> ... <!-- endif --> blocks only when the flag is on; `if:!flag` negates; blocks may nest.
- Never overwrites a file; templates named `gitignore` / `gitattributes` are appended to the dotfiles line by line.
- Prints what it created and skipped, then docs/PROMPTS.md's "Start here" part.
- templates/opencode-global is never copied into a project.

Usage: python -I -X utf8 deploy.py --global [--force]
- Copies templates/opencode-global's plugins/*, tui-plugins/* and scripts/* into your OpenCode config folder
  ($XDG_CONFIG_HOME/opencode, else ~/.config/opencode) only where missing (--force: overwrite the kit's own files).
- Never edits opencode.jsonc or cli.json: prints the cli.json lines and the config snippet instead.
"""
import json, os, pathlib, re, shutil, sys

base = pathlib.Path(__file__).resolve().parent.parent / "templates"


def install_global(args: list) -> int:
    if any(x != "--force" for x in args) or len(args) > 1:
        print("usage: deploy.py --global [--force]", file=sys.stderr)
        return 2
    force, kit = bool(args), base / "opencode-global"
    home = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config") / "opencode"
    done, kept, tui = [], [], []
    for sub in ("plugins", "tui-plugins", "scripts"):
        for src in sorted((kit / sub).iterdir()) if (kit / sub).is_dir() else []:
            dst = home / sub / src.name
            if sub == "tui-plugins":
                tui.append(dst.as_posix())
            if dst.exists() and not force:
                kept.append(f"{sub}/{src.name}")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(src, dst)
            done.append(f"{sub}/{src.name}")
    print(f"OpenCode config folder: {home}")
    print(f"copied {len(done)}: {', '.join(done) or '-'}")
    if kept:
        print(f"kept (already there; --force overwrites) {len(kept)}: {', '.join(kept)}")
    if tui:
        print("\nTUI plugins: add to \"plugins\" in " + (home / "cli.json").as_posix() + " (deploy never edits it):")
        print("  \"plugins\": [" + ", ".join(json.dumps(t) for t in tui) + "]")
        print("then restart the TUI. ctxpick's CLI (bin/) needs node >= 23.6.")
    print("\nprompt-polish: set POLISH_MODEL and ONLY_PROVIDERS at the top of plugins/prompt-polish/index.js.")
    print("rtk (not vendored): install rtk, then `rtk init -g --opencode`.")
    if (kit / "plugins" / "pacer").is_dir():
        print("pacer: no cli.json line (its TUI files load from beside index.js); restart OpenCode, then the TUI.")
        print("  Everything is off until `/pacer`. Needs python (python3 off Windows; $PACER_PYTHON overrides).")
        print("  How it works: " + (kit / "pacer.md").as_posix())
    if (kit / "plugins" / "news").is_dir():
        print("todo (news): no cli.json line; restart OpenCode, then the TUI. `/todo` opens the list of 📌 lines.")
        print("  How it works: " + (kit / "README.md").as_posix() + " (section: todo)")
    if (kit / "scripts" / "metrics").is_dir():
        print("metrics: a no-model shell automation every 10 min runs scripts/metrics/metrics.py (0 tokens).")
        print("  How it works: " + (kit / "README.md").as_posix() + " (section: metrics)")
    print("\nMerge by hand into " + (home / "opencode.jsonc").as_posix() + " (deploy never edits it):\n")
    print((kit / "opencode.snippet.jsonc").read_text(encoding="utf-8"))
    return 0


if sys.argv[1:2] == ["--global"]:
    sys.exit(install_global(sys.argv[2:]))
args = [x for x in sys.argv[1:] if x != "--no-vault"]
if len(args) != 2 or args[0].startswith("-"):
    print("usage: deploy.py <target> <answers.json> [--no-vault] | deploy.py --global [--force]", file=sys.stderr)
    sys.exit(2)
target = pathlib.Path(args[0]).resolve()
a = json.loads(pathlib.Path(args[1]).read_text(encoding="utf-8"))
vault = "--no-vault" not in sys.argv and a.get("VAULT", True) is not False  # the docs vault: on unless opted out

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
    "vault": vault,
}
# Older answer files name two roles by their old names (planner -> product lead, CTO -> architect) or lack the newer
# roles: fall back to the old key, then to the reviewer's model (top, high) or the integrator's.
for new, old in (("M_PRODUCT", "M_PLANNER"), ("M_ARCH", "M_CTO")):
    if not a.get(new) and a.get(old):
        a[new] = a[old]
for k, fallback in (("M_PRODUCT", "M_REVIEW"), ("M_ARCH", "M_REVIEW"), ("M_ARCH_QUICK", "M_REVIEW"),
                    ("M_PLANREV", "M_REVIEW"), ("M_PREGROOM", "M_REVIEW"), ("M_DOCS", "M_INTEG")):
    if not a.get(k):
        a[k] = a.get(fallback) or a.get("M_ARCH") or a.get("M_BUILDER", "")
a.setdefault("M_REVIEW_DELTA", a.get("M_INTEG") or a.get("M_REVIEW", ""))  # a delta review of docs or tools only: the mid model
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
    for src in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):  # a local test run leaves .pyc
        rel = src.relative_to(root)
        if not vault and rel.parts[:2] == ("tools", "vault"):  # the gate and integrate skip a missing tools/vault/
            continue
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
        if rel.parts[0] == "dot-opencode":  # kept un-dotted here so opencode never loads the templates as config
            rel = pathlib.Path(".opencode", *rel.parts[1:])
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


def vault_hook() -> str:
    """The vault's git hooks (`tools/vault/hook.py install`): only in a repository whose top is the target."""
    import subprocess
    hook = target / "tools" / "vault" / "hook.py"
    if not vault:
        return "off (--no-vault or \"VAULT\": false): no tools/vault/, no hooks"
    if not hook.is_file():
        return "no tools/vault/hook.py: skipped"
    top = subprocess.run(["git", "-C", str(target), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode or os.path.normcase(str(pathlib.Path(top.stdout.strip()).resolve())) != os.path.normcase(str(target)):
        return "not a git repository's top yet: after `git init`, run `python -I tools/vault/hook.py install`"
    r = subprocess.run([sys.executable, "-I", str(hook), "install", "--repo", str(target)], capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [r.stderr.strip() or f"exit {r.returncode}"])[-1]
    return last + ("" if r.returncode == 0 else " (see `python -I tools/vault/hook.py print`)")


print("vault hook: " + vault_hook() + ("; how it works: tools/vault/README.md" if vault else ""))
prompts = (target / "docs/PROMPTS.md")
if prompts.exists():
    t = prompts.read_text(encoding="utf-8")
    i, j = t.find("## Start here"), t.find("\n## ", t.find("## Start here") + 5)
    print("\n" + (t[i:j] if i >= 0 else t[:3000]))
