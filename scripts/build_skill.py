#!/usr/bin/env python3
"""Build a KJV2222-family bundle prompt into one global skill for every agent platform.

A bundle prompt such as prompt/1.prompt.kjv.83.md carries a SKILL.md, its workflow
files, and a gate file as fenced blocks. This script turns it into one Agent Skills
folder, checks it, and with --install puts identical copies where each platform looks:

  ~/.claude/skills/<name>/          Claude Code, PAI / LifeOS
  ~/.gemini/config/skills/<name>/   Antigravity
  ~/.gemini/skills/<name>/          Gemini CLI
  ~/Downloads/<name>.zip            upload to the Claude app

Usage:
  python3 scripts/build_skill.py prompt/1.prompt.kjv.83.md             # build and check only
  python3 scripts/build_skill.py prompt/1.prompt.kjv.83.md --install   # build, check, install

The skill name comes from the file name (1.prompt.kjv.83.md -> kjv83) unless --name
is given. Every text edit asserts how many times it matches: when a new source
changes a passage an edit relies on, the build stops and names that edit, instead of
shipping a half-converted skill. Nothing is installed unless every check passes, and
an existing install is replaced only when it is the same skill.

Standard library only; runs on macOS's built-in Python 3.9.
"""
import argparse
import filecmp
import http.server
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile

HOME = pathlib.Path.home()
SKILL_ROOTS = [
    ("Claude Code · PAI / LifeOS", HOME / ".claude" / "skills"),
    ("Antigravity", HOME / ".gemini" / "config" / "skills"),
    ("Gemini CLI", HOME / ".gemini" / "skills"),
]
ZIP_DIR = HOME / "Downloads"
REPO = pathlib.Path(__file__).resolve().parent.parent

DESCRIPTION = (
    "{bundle} v{version} Berean Council workbook builder (KJV only) with Laya-style typed gates. "
    "USE WHEN the user invokes {name} (or {aliases}) or asks to build, draft, verify, gate, expand, "
    "or adapt a KJV Bible study workbook or lesson with Reformed exegesis. Every lesson teaches how to "
    "bring life (John 10:10) and how to bring light (John 8:12), summarizes in great details the "
    "doctrines for the topic or scripture reference across the Ten Loci, and passes typed System 1 "
    "gates before it ships."
)


def fail(message):
    sys.exit(f"build_skill: {message}")


def sub(text, old, new, count, label):
    found = text.count(old)
    if found != count:
        fail(f"edit '{label}' expected {count} match(es) of {old[:70]!r}, found {found}; "
             "update that edit for this source")
    return text.replace(old, new)


def resub(text, pattern, replacement, count, label, expand=False):
    """Regex edit; `expand=True` lets the replacement use backreferences such as \\1."""
    out, found = re.subn(pattern, (lambda m: m.expand(replacement)) if expand else (lambda m: replacement),
                         text, flags=re.M)
    if found != count:
        fail(f"edit '{label}' expected {count} match(es) of /{pattern[:70]}/, found {found}; "
             "update that edit for this source")
    return out


# ------------------------------------------------------------------------------------ parse


def parse_bundle(source):
    """Return ({relative path: content}, the bundle's own skill id)."""
    lines = source.split("\n")
    header = re.compile(r"^## File (\d+) of (\d+) — `~/\.claude/skills/([^/`]+)/(.+)`$")
    files, ids, total, i = {}, set(), 0, 0
    try:
        while i < len(lines):
            m = header.match(lines[i])
            if not m:
                i += 1
                continue
            ids.add(m.group(3))
            total = int(m.group(2))
            j = i + 1
            while not lines[j].startswith("````"):
                if lines[j].strip():
                    fail(f"text between {lines[i]!r} and its fence")
                j += 1
            k, body = j + 1, []
            while lines[k] != "````":
                if lines[k].startswith("````"):
                    fail(f"nested four-backtick fence in {m.group(4)}")
                body.append(lines[k])
                k += 1
            files[m.group(4)] = "\n".join(body) + "\n"
            i = k + 1
    except IndexError:
        fail("a file fence is never closed")
    if not files or len(files) != total:
        fail(f"expected {total} bundle files, found {len(files)}")
    if len(ids) != 1:
        fail(f"bundle files name more than one skill folder: {sorted(ids)}")
    if "SKILL.md" not in files:
        fail("the bundle has no SKILL.md")
    return files, ids.pop()


# ------------------------------------------------------------------------------------ build


def build(source_path, name, out):
    source = source_path.read_text()
    files, src_id = parse_bundle(source)

    title = re.search(r"^# (\S+) v([\d.]+) — (.+?) \(Skill Bundle\)$", source, re.M)
    created = re.search(r"^\*\*Created by:\*\* (.+)$", source, re.M)
    if not title or not created:
        fail("could not read the bundle title or Created by line")
    bundle, version, product = title.groups()
    router_row = re.search(r"^\| BuildWorkbook \| (.+?) \|", files["SKILL.md"], re.M)
    triggers = re.findall(r'"(kjv\w+)"', router_row.group(1)) if router_row else []
    aliases = [t for t in triggers if t != name]
    if not aliases:
        fail("could not read the kjv trigger aliases from the BuildWorkbook router row")
    try:
        source_rel = source_path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        source_rel = source_path.name

    # SKILL.md
    s = files["SKILL.md"]
    description = DESCRIPTION.format(bundle=bundle, version=version, name=name, aliases=", ".join(aliases))
    s = resub(s, rf"\A---\nname: {re.escape(src_id)}\ndescription: .*\n---\n# {re.escape(src_id)}\n",
              f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n", 1, "frontmatter and title")
    s = sub(s, "\nDEFINITION OF DONE:",
            f"\n**Bundle:** {bundle} v{version} — {product} · **Created by:** {created.group(1)} · "
            "**Translation:** KJV exclusively · **Lineage:** gate architecture adapted from "
            "[Laya](https://github.com/NandhaKishorM/laya) (Convai Innovations, Apache-2.0) and "
            "[laya-ultrafast](https://github.com/ipenywis/laya-ultrafast) (a port of Browser Use's "
            "jev-ultrafast, MIT) — their ideas and Laya's question format, no code.\n\nDEFINITION OF DONE:",
            1, "bundle line")
    s = sub(s, "## The Anchors\n", f"""## Running this skill

- **Invoke:** `{name}: book=Ephesians, lesson_count=5` — or any Book / Passage / Topic, with any variable below. In Claude Code and PAI / LifeOS, `/{name}` also works; Antigravity and Gemini CLI activate the skill from its description, so naming `{name}` in the request is enough.
- **Skill folder:** every path in this skill (`Workflows/…`, `Gates/…`, `references/…`) is relative to the folder that holds this `SKILL.md`. A shell command that reads a skill file uses `$SKILL_DIR`, set to that folder:

  | Platform | Skill folder (`$SKILL_DIR`) |
  |---|---|
  | Claude Code · PAI / LifeOS | `~/.claude/skills/{name}` |
  | Antigravity | `~/.gemini/config/skills/{name}` |
  | Gemini CLI | `~/.gemini/skills/{name}` |
  | Claude app | uploaded from `{name}.zip`; the app places the folder |

- **No shell or no local server:** where the agent cannot run shell commands or reach `localhost` (the Claude app, a sandbox), DECISION_BACKEND resolves to `inline` and every gate still runs; leave KJV_SOURCE and WORKBOOK_FILE unset.
- **Background for people, not rules:** what v{version} adds, the Laya lineage, setup for an optional local Laya server, the quick reference, and the changelog live in [references/design-notes.md](references/design-notes.md). Nothing there changes how the skill runs.

## The Anchors
""", 1, "running section")
    old_voice = f"""**When executing a workflow, do BOTH:**

1. **Send voice notification**:
   ```bash
   curl -s -X POST http://localhost:31337/notify \\
     -H "Content-Type: application/json" \\
     -d '{{"message": "Running the WORKFLOWNAME workflow in the {src_id} skill to ACTION"}}' \\
     > /dev/null 2>&1 &
   ```
2. **Output text notification**:
   ```
   Running the **WorkflowName** workflow in the **{src_id}** skill to ACTION...
   ```

Full documentation: `~/.claude/PAI/DOCUMENTATION/Notifications/NotificationSystem.md`
"""
    new_voice = f"""**In a PAI / LifeOS session** (PAI is loaded in the session, or `~/.claude/PAI/` exists), do BOTH when executing a workflow:

1. **Send voice notification**:
   ```bash
   curl -s -X POST http://localhost:31337/notify \\
     -H "Content-Type: application/json" \\
     -d '{{"message": "Running the WORKFLOWNAME workflow in the {name} skill to ACTION"}}' \\
     > /dev/null 2>&1 &
   ```
2. **Output text notification**:
   ```
   Running the **WorkflowName** workflow in the **{name}** skill to ACTION...
   ```

Full documentation: `~/.claude/PAI/DOCUMENTATION/Notifications/NotificationSystem.md`

**On every other platform** — Antigravity, Gemini CLI, Claude Code without PAI, the Claude app — skip the curl (there is no voice server to reach) and print the text notification alone.
"""
    s = sub(s, old_voice, new_voice, 1, "SKILL.md voice section")
    workflow_count = sum(1 for p in files if p.startswith("Workflows/"))
    s = resub(s, r"\| Workflows/(\w+)\.md \|$", r"| [Workflows/\1.md](Workflows/\1.md) |", workflow_count,
              "router links", expand=True)
    s = sub(s, "live in `Gates/kjv-gates.json` — the same questions",
            "live in [Gates/kjv-gates.json](Gates/kjv-gates.json) — the same questions", 1, "gates link")
    s = sub(s, f'**User:** "{src_id.lower()}: book=Ephesians, lesson_count=5"',
            f'**User:** "{name}: book=Ephesians, lesson_count=5"', 1, "example 1")
    files["SKILL.md"] = s

    # workflows
    pai_only = ("PAI / LifeOS only (see SKILL.md § Voice Notification); on every other platform, "
                "print the text line below alone.\n\n")
    for path in sorted(p for p in files if p.startswith("Workflows/")):
        w = files[path]
        w = sub(w, f"in the {src_id} skill", f"in the {name} skill", 1, f"{path} voice message")
        w = sub(w, f"in the **{src_id}** skill", f"in the **{name}** skill", 1, f"{path} text message")
        if path == "Workflows/RunGates.md":
            w = sub(w, "## Voice Notification\n\nOnly when routed directly. As an `on_end` hook or inside "
                       "VerifyWorkbook, RunGates runs without notifications.\n",
                    "## Voice Notification\n\nOnly when routed directly — as an `on_end` hook or inside "
                    "VerifyWorkbook, RunGates runs without notifications. The curl is PAI / LifeOS only (see "
                    "SKILL.md § Voice Notification); on every other platform, print the text line below alone.\n",
                    1, "RunGates voice note")
            w = sub(w, f"to `~/.claude/skills/{src_id}/Gates/ledger.jsonl`, in the dataset",
                    "to `Gates/ledger.jsonl` in this skill's folder (`$SKILL_DIR/Gates/ledger.jsonl`), in the "
                    "dataset", 1, "ledger path")
            w = sub(w, "Write the window to a file with a quoted heredoc, so the shell expands nothing in it:",
                    "Set `SKILL_DIR` to this skill's folder (SKILL.md § Running this skill), and write the window "
                    "to a file with a quoted heredoc, so the shell expands nothing in it:", 1, "SKILL_DIR note")
            w = sub(w, '```bash\nW="${TMPDIR:-/tmp}/kjv-window.txt"\n',
                    f'```bash\nSKILL_DIR="$HOME/.claude/skills/{name}"   # Antigravity: '
                    f'~/.gemini/config/skills/{name} · Gemini CLI: ~/.gemini/skills/{name}\n'
                    'W="${TMPDIR:-/tmp}/kjv-window.txt"\n', 1, "SKILL_DIR line")
            w = sub(w, f"--slurpfile g ~/.claude/skills/{src_id}/Gates/kjv-gates.json \\",
                    '--slurpfile g "$SKILL_DIR/Gates/kjv-gates.json" \\', 1, "slurpfile path")
        else:
            w = sub(w, "## Voice Notification\n\n```bash", "## Voice Notification\n\n" + pai_only + "```bash",
                    1, f"{path} voice note")
        files[path] = w

    # gate file
    files["Gates/kjv-gates.json"] = sub(files["Gates/kjv-gates.json"], f'"name": "{src_id} Laya Gates"',
                                        f'"name": "{name} Laya Gates"', 1, "gate file name")

    # references/design-notes.md: the bundle's preamble and closing sections, for people
    try:
        pre = source[source.index("**Reformed KJV Workbook Generator"):source.index("## THE FILES")]
        tail = source[source.index("## QUICK REFERENCE"):]
    except ValueError:
        fail("could not find the preamble, THE FILES, or QUICK REFERENCE sections")
    pre = pre.rstrip().rstrip("-").rstrip() + "\n"
    pre = resub(pre, r"This document is the complete, installable reference bundle for the \*\*\w+\*\* PAI "
                     r"skill: (one `SKILL\.md` routing file, [^.]+)\.",
                f"The **{name}** skill is the {bundle} v{version} bundle installed as one global skill for "
                "Antigravity, Gemini CLI, Claude, and PAI / LifeOS: \\1.", 1, "notes intro", expand=True)
    pre = resub(pre, r"^1\. \*\*Install or update the skill:\*\*.*$",
                f"1. **Where it lives:** `~/.claude/skills/{name}/` (Claude Code, PAI / LifeOS) · "
                f"`~/.gemini/config/skills/{name}/` (Antigravity) · `~/.gemini/skills/{name}/` (Gemini CLI) · "
                f"`~/Downloads/{name}.zip` (upload to the Claude app's Skills settings ⚠VERIFY). All four are "
                f"built from `{source_rel}` in the kjv.prompt repository by `scripts/build_skill.py`; after "
                f"editing the source, run `python3 scripts/build_skill.py {source_rel} --install` so all four "
                "stay identical.", 1, "notes install")
    pre = resub(pre, r"^2\. \*\*Run it:\*\* in a PAI session say `[^`]+` \(or `[^`]+`, or any Book/Passage/Topic\)\.",
                f"2. **Run it:** say `{name}: book=Ephesians, lesson_count=5` (or `/{name}` in Claude Code and "
                "PAI / LifeOS, or any Book/Passage/Topic).", 1, "notes run")
    pre = sub(pre, "declared once, in `SKILL.md` § Variables below — the single source of truth.",
              "declared once, in `../SKILL.md` § Variables — the single source of truth.", 1, "notes variables")
    tail = resub(tail, r"^\| Skill name \| .*\|$", f"| Skill name | `{name}` (bundle: {bundle} v{version}) |",
                 1, "quick reference name")
    tail = resub(tail, r"^\| Invoke \| .*\|$",
                 f"| Invoke | `{name}: book=..., lesson_count=...` in Antigravity, Gemini CLI, Claude, or "
                 f"PAI / LifeOS (`/{name}` in Claude Code); aliases {', '.join(f'`{a}`' for a in aliases)} |",
                 1, "quick reference invoke")
    files["references/design-notes.md"] = (
        f"# {name} — Background: What v{version} Adds, Design Notes & Changelog\n\n"
        f"> Background for human readers of the `{name}` skill. This file adds no rules, and an agent "
        "running the skill takes no action from it. `../SKILL.md` and the files it names are the single "
        "source of truth.\n\n" + pre + "\n---\n\n" + tail)

    for rel, content in sorted(files.items()):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return src_id, version


# ------------------------------------------------------------------------------------ checks

NEGATION = re.compile(r"\b(not|no|never|none|nothing|without|n't)\b", re.I)


def check_gates(g):
    errors, gate_ids = [], set()
    presets = g.get("presets", {})
    for pname, questions in presets.items():
        if pname not in g.get("window", {}):
            errors.append(f"gates: preset {pname} has no window entry")
        for qid, q in questions.items():
            gid = f"{pname}.{qid}"
            gate_ids.add(gid)
            ins = q.get("instructions", "")
            if q.get("type") not in {"choice", "score", "noul"}:
                errors.append(f"gates: {gid} has an unknown type")
            if not ins.strip() or NEGATION.search(ins):
                errors.append(f"gates: {gid} instructions are empty or negated")
            if q.get("type") != "choice":
                continue
            criteria = q.get("criteria") or {}
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 6:
                errors.append(f"gates: {gid} needs 2-6 options")
                continue
            for label, text in criteria.items():
                if label.lower() in {"yes", "no", "true", "false"} or not re.fullmatch(r"[a-z][a-z_]*", label):
                    errors.append(f"gates: {gid} label {label!r} is not a semantic snake_case label")
                if re.search(r"(^|_)(not|no|never|without|none)(_|$)", label):
                    errors.append(f"gates: {gid} label {label!r} carries a negation")
                if not isinstance(text, str) or not text.strip() or len(text.split()) > 16 or NEGATION.search(text):
                    errors.append(f"gates: {gid}.{label} description is empty, over 16 words, or negated")
    for gid, rule in g.get("pass", {}).items():
        if gid not in gate_ids:
            errors.append(f"gates: pass entry for unknown gate {gid}")
        elif isinstance(rule, list):
            pname, qid = gid.split(".")
            errors += [f"gates: pass[{gid}] names unknown label {lab!r}" for lab in rule
                       if lab not in presets[pname][qid]["criteria"]]
    errors += [f"gates: {gid} has no pass entry" for gid in sorted(gate_ids - set(g.get("pass", {})))]
    points = g.get("points", {})
    if sorted(points, key=lambda p: int(p[1:])) != [f"P{n}" for n in range(1, 20)]:
        errors.append("gates: points must be exactly P1-P19")
    for point, refs in points.items():
        errors += [f"gates: points[{point}] names unknown {ref!r}" for ref in refs
                   if ref not in presets and not re.fullmatch(r"R(1[01]|[1-9])", ref)]
    errors += [f"gates: promotable names unknown gate {gid}" for gid in g.get("promotable", []) if gid not in gate_ids]
    return errors


def check_tables(path, text):
    errors, in_code, lines, i = [], False, text.split("\n"), 0
    cells = lambda line: re.sub(r"\\\|", "", line.strip()).count("|") - 1  # noqa: E731
    while i < len(lines):
        if re.match(r"^\s*```", lines[i]):
            in_code = not in_code
        if (not in_code and lines[i].strip().startswith("|") and i + 1 < len(lines)
                and re.match(r"^\s*\|[-\s|:]+\|\s*$", lines[i + 1])):
            width, j = cells(lines[i]), i
            while j < len(lines) and lines[j].strip().startswith("|"):
                if cells(lines[j]) != width:
                    errors.append(f"{path}:{j + 1}: table row has {cells(lines[j])} columns, header has {width}")
                j += 1
            i = j
            continue
        i += 1
    return errors


def pipeline_selftest(skill):
    """Run RunGates' request block against a stand-in Laya server, with hostile window text."""
    if not all(shutil.which(tool) for tool in ("bash", "jq", "curl")):
        return "skipped (needs bash, jq, and curl)"
    blocks = re.findall(r"^```bash\n(.*?)^```$", (skill / "Workflows/RunGates.md").read_text(), re.M | re.S)
    block = next((b for b in blocks if "kjv-window.txt" in b), None)
    if block is None:
        return "FAILED: no request block in RunGates.md"
    hostile = "It's \"dead in trespasses\" — $HOME `id` $(echo PWNED) back\\slash"
    received = {}

    class StandIn(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.update(body)
            answers = {qid: {"type": "choice", "choice": next(iter(q["criteria"])), "answer_confidence": 0.9}
                       for qid, q in body["questions"].items()}
            payload = json.dumps({"answers": answers, "usage": {"input_tokens": 1, "output_tokens": 0}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.HTTPServer(("127.0.0.1", 0), StandIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        script, pointed = re.subn(r'^SKILL_DIR="[^"]*"', lambda m: f'SKILL_DIR="{skill}"', block, count=1,
                                  flags=re.M)
        if not pointed or "The window text, verbatim." not in script:
            return "FAILED: the request block no longer has its SKILL_DIR line or window placeholder"
        script = script.replace("The window text, verbatim.", hostile)
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, LAYA_URL=f"http://127.0.0.1:{server.server_address[1]}", TMPDIR=tmp)
            run = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    finally:
        server.shutdown()
    expected = list(json.loads((skill / "Gates/kjv-gates.json").read_text())["presets"]["life"])
    answered = [line.split(" = ")[0] for line in run.stdout.strip().split("\n") if " = " in line]
    if run.returncode != 0 or answered != expected:
        return f"FAILED: exit {run.returncode}, answered {answered}, stderr {run.stderr.strip()[:200]!r}"
    if received.get("state", {}).get("text") != hostile + "\n":
        return "FAILED: window text was altered on the way to the server"
    return f"passed ({len(expected)} answers; window text arrived byte-for-byte)"


def check(skill, name, src_id, version):
    errors = []
    s = (skill / "SKILL.md").read_text()
    front = re.match(r"\A---\nname: (.*)\ndescription: (.*)\n---\n", s)
    if not front:
        errors.append("SKILL.md frontmatter must be exactly name and description")
    else:
        fm_name, desc = front.groups()
        if fm_name != name or fm_name != skill.name:
            errors.append("frontmatter name must match the skill folder")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fm_name) or len(fm_name) > 64:
            errors.append("name must be lowercase letters, digits, and hyphens, at most 64 characters")
        if not 1 <= len(desc) <= 1024:
            errors.append(f"description is {len(desc)} characters; the limit is 1024")
        if ": " in desc or " #" in desc or desc[:1] in set("*&!|>'\"%@`[{") or re.search(r"<[a-zA-Z/]", desc):
            errors.append("description must stay a plain YAML scalar with no tags")
    for path in sorted(skill.rglob("*")):
        if not path.is_file():
            continue
        rel, text = path.relative_to(skill).as_posix(), path.read_text()
        for leftover in (src_id, f"~/.claude/skills/{src_id}", "in a PAI session"):
            if leftover in text:
                errors.append(f"{rel} still contains {leftover!r}")
        if rel.endswith(".md"):
            errors += check_tables(rel, text)
            for ref in re.findall(r"`((?:Workflows|Gates)/[A-Za-z-]+\.(?:md|json))`", text):
                if not (skill / ref).is_file():
                    errors.append(f"{rel} names missing file {ref}")
        if rel.startswith("Workflows/") and "31337" in text and "PAI / LifeOS only" not in text:
            errors.append(f"{rel} calls the PAI voice server without the PAI / LifeOS only note")
    for ref in re.findall(r"\]\(((?:Workflows|Gates|references)/[^)]+)\)", s):
        if not (skill / ref).is_file():
            errors.append(f"SKILL.md links to missing file {ref}")
    notes = (skill / "references/design-notes.md").read_text()
    for heading in (f"## WHAT v{version} ADDS", "## HOW TO USE", "## QUICK REFERENCE", "## CHANGELOG", "## CREDITS"):
        if heading not in notes:
            errors.append(f"design notes are missing {heading!r}")
    if "## THE FILES" in notes or "````" in notes:
        errors.append("design notes still carry the bundle's file blocks")
    try:
        errors += check_gates(json.loads((skill / "Gates/kjv-gates.json").read_text()))
    except json.JSONDecodeError as err:
        errors.append(f"Gates/kjv-gates.json is not valid JSON: {err}")
    return errors


# ------------------------------------------------------------------------------------ install


def same_tree(a, b):
    files_a = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
    return files_a == files_b and all(filecmp.cmp(a / f, b / f, shallow=False) for f in files_a)


def is_this_skill(folder, name):
    skill_md = folder / "SKILL.md"
    return skill_md.is_file() and re.search(rf"^name: {re.escape(name)}$", skill_md.read_text(), re.M)


def install(skill, name):
    for platform, root in SKILL_ROOTS:
        dest = root / name
        if dest.exists() and not is_this_skill(dest, name):
            fail(f"{dest} exists but is not the '{name}' skill; refusing to replace it")
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".{name}.installing"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(skill, staging)
        if dest.exists():
            shutil.rmtree(dest)
        staging.rename(dest)
        if not same_tree(skill, dest):
            fail(f"{dest} does not match the build")
        print(f"  identical  {dest}  ({platform})")
    ZIP_DIR.mkdir(exist_ok=True)
    archive, staging = ZIP_DIR / f"{name}.zip", ZIP_DIR / f".{name}.zip.installing"
    with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(skill, f"{name}/")
        for path in sorted(skill.rglob("*")):
            if path.name != ".DS_Store":
                zf.write(path, f"{name}/{path.relative_to(skill).as_posix()}" + ("/" if path.is_dir() else ""))
    os.replace(staging, archive)
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        mismatched = [p for p in skill.rglob("*") if p.is_file()
                      and zf.read(f"{name}/{p.relative_to(skill).as_posix()}") != p.read_bytes()]
    if bad or mismatched:
        fail(f"{archive} failed verification")
    print(f"  identical  {archive}  (Claude app upload)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", type=pathlib.Path, help="bundle prompt, e.g. prompt/1.prompt.kjv.83.md")
    parser.add_argument("--name", help="skill name (default: kjv<version> from the file name)")
    parser.add_argument("--install", action="store_true", help="install to all four platforms after the checks pass")
    parser.add_argument("--out", type=pathlib.Path, help="also keep a copy of the built skill folder here")
    args = parser.parse_args()

    if not args.source.is_file():
        fail(f"no such file: {args.source}")
    derived = re.search(r"kjv\.(\d+)\.md$", args.source.name)
    name = args.name or (f"kjv{derived.group(1)}" if derived else None)
    if not name:
        fail("cannot derive a skill name from the file name; pass --name")

    with tempfile.TemporaryDirectory() as tmp:
        skill = pathlib.Path(tmp) / name
        src_id, version = build(args.source, name, skill)
        count = sum(1 for p in skill.rglob("*") if p.is_file())
        print(f"built {name} (bundle {src_id} v{version}): {count} files")
        errors = check(skill, name, src_id, version)
        if errors:
            fail("checks failed:\n  - " + "\n  - ".join(errors))
        print("checks passed: frontmatter, leftovers, links, voice gating, design notes, gates, tables")
        selftest = pipeline_selftest(skill)
        print(f"gate pipeline self-test {selftest}")
        if selftest.startswith("FAILED"):
            fail("gate pipeline self-test failed")
        if args.out:
            dest = args.out / name
            if dest.exists():
                fail(f"{dest} already exists")
            shutil.copytree(skill, dest)
            print(f"copy kept at {dest}")
        if args.install:
            install(skill, name)
        else:
            print("not installed (add --install)")


if __name__ == "__main__":
    main()
