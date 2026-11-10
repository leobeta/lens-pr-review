#!/usr/bin/env python3
"""Validate a review draft (see references/draft-template.md) and extract a summary for the review map.

Errors (exit 2) are structural or rule breaks: missing sections or fields, an unknown lens, a blocking comment without
evidence or on a lens that may not block, a comment that is too long or contains rule ids, AI mentions or headings, a
verdict of APPROVE, a diagram without its source line, or an anchor to a file or line that does not exist at --head.
Warnings never fail the run: a harsh or imperative tone, a backticked symbol that does not exist at --head (ignore it
when you are proposing a new name), a verdict that disagrees with the weights.

The draft is a suggestion for a human. This script never talks to GitHub.

Usage:
  check_draft.py draft.md [--head <ref>] [--threads threads.json] [--summary-out summary.json]

With --threads (written by threads.py) the "Earlier discussion" section must have one table row per earlier review
thread that needs a status: the thread id, a valid status, and evidence that cites the fix.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lenses as lens_loader  # noqa: E402

WEIGHTS = ("blocking-suggested", "discuss", "minor", "praise")
VERDICTS = ("COMMENT", "REQUEST_CHANGES")
REQUIRED_SECTIONS = ("In plain words", "Where to look", "Suggested comments", "Dismissed scanner hits", "Not verified")
MAX_WORDS = 90
MAX_PRAISE = 2

HEADING = re.compile(r"^### (C\d+) · `([^`]+)` · ([\w:.-]+) · ([\w-]+)\s*$")
RULE_ID = re.compile(r"\b[A-Z]{2,}-\d{3}\b")
AI_MENTION = re.compile(r"\b(claude|anthropic|chatgpt|copilot|as an ai|language model|llm)\b", re.I)
MD_STRUCTURE = re.compile(r"^\s*(#{1,6}\s|[-*+]\s|\d+\.\s|\|)")
IMPERATIVE = re.compile(r"^(remove|change|use|replace|fix|add|rename|delete|move|make|ensure|avoid|don't|do not|please)\b", re.I)
HARSH = re.compile(r"\b(wrong|bad|terrible|obviously|clearly|must|always|never|violates?|unacceptable|sloppy)\b", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
SYMBOL = re.compile(r"`([A-Za-z_][\w.#]*(?:\(\))?)`")
STATUSES = ("Addressed", "Partially addressed", "Not addressed", "No longer applicable", "Deferred", "Unclear")
CITES_FIX = re.compile(r"`[^`]+`|\b[0-9a-f]{7,40}\b")
COMMON = {"null", "true", "false", "int", "long", "String", "List", "Map", "Set", "Optional", "BigDecimal", "this"}


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], capture_output=True).returncode == 0


def line_count(head: str, path: str) -> int | None:
    result = subprocess.run(["git", "show", f"{head}:{path}"], capture_output=True, text=True, errors="replace")
    return len(result.stdout.splitlines()) if result.returncode == 0 else None


def symbol_exists(head: str, token: str) -> bool:
    """True when the last segment of `Class#method`, `Class.method` or `name()` appears anywhere at `head`."""
    last = re.split(r"[#.]", token.removesuffix("()"))[-1]
    return bool(last) and git_ok("grep", "-q", "-w", "-F", last, head)


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        match = re.match(r"^## (.+?)\s*$", line)
        if match:
            current = match.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def parse_comments(lines: list[str], errors: list[str]) -> list[dict]:
    comments: list[dict] = []
    current: dict | None = None
    in_quote_block = False
    for line in lines:
        heading = HEADING.match(line)
        if line.startswith("### "):
            if not heading:
                errors.append(f"Malformed comment heading: {line!r}. Expected: ### C1 · `path:line` · lens-id · weight")
                current = None
                continue
            current = {"id": heading.group(1), "anchor": heading.group(2), "lens": heading.group(3), "weight": heading.group(4),
                       "evidence": "", "verified": "", "quote": [], "checkbox": False}
            comments.append(current)
            in_quote_block = False
            continue
        if current is None:
            continue
        field = re.match(r"^- \*\*(Evidence|Verified by|Link|Comment):\*\*\s*(.*)$", line)
        if field:
            name, value = field.groups()
            in_quote_block = name == "Comment"
            if name == "Evidence":
                current["evidence"] = value.strip()
            elif name == "Verified by":
                current["verified"] = value.strip()
            continue
        if re.match(r"^- \[[ xX]\] Post this comment\s*$", line):
            current["checkbox"] = True
            in_quote_block = False
            continue
        if in_quote_block and line.startswith(">"):
            current["quote"].append(line[1:].strip())
    return comments


def check_earlier_discussion(lines: list[str] | None, threads: dict, errors: list[str], warnings: list[str]) -> None:
    needing = {t["id"]: t for t in threads["threads"] if t["needs_status"]}
    if not needing:
        return
    if lines is None:
        errors.append(f"Missing section '## Earlier discussion': {len(needing)} earlier review thread(s) need a status (see threads.json)")
        return
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.strip().startswith("|") else []
        if len(cells) >= 3 and not set("".join(cells)) <= set("-: "):
            rows.append(cells)
    for thread_id, thread in sorted(needing.items()):
        row = next((cells for cells in rows if re.search(rf"\b{thread_id}\b", cells[0])), None)
        where = f"{thread['path']}:{thread['line'] or thread['original_line']}"
        if row is None:
            errors.append(f"Earlier discussion: no row for thread {thread_id} ({where}). Start the row with the thread id")
            continue
        status = next((c for c in row if c.lower() in [x.lower() for x in STATUSES]), None)
        if status is None:
            errors.append(f"Earlier discussion: thread {thread_id} has no valid status ({', '.join(STATUSES)})")
            continue
        evidence = row[-1]
        if len(evidence.split()) < 3:
            errors.append(f"Earlier discussion: thread {thread_id} needs evidence, not just a status")
        elif status.lower() in ("addressed", "partially addressed") and not CITES_FIX.search(evidence):
            errors.append(f"Earlier discussion: thread {thread_id} is '{status}' but the evidence cites no fix (a `symbol` or commit sha)")
        delta = thread["delta"]
        if status.lower() == "addressed" and delta["available"] and delta["touched"] is False:
            warnings.append(f"Earlier discussion: thread {thread_id} is Addressed but {thread['path']} did not change since the comment. Is the fix elsewhere?")
        if status.lower() == "not addressed" and delta["available"] and delta["touched"]:
            warnings.append(f"Earlier discussion: thread {thread_id} is Not addressed but {thread['path']} changed since the comment. Did you read the delta?")


def check_mermaid(text: str, errors: list[str]) -> None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "```mermaid":
            preceding = " ".join(lines[max(0, index - 3):index])
            if "Derived from:" not in preceding:
                errors.append(f"Mermaid block at line {index + 1} has no 'Derived from:' line before it (see references/mermaid.md)")
            end = next((i for i in range(index + 1, len(lines)) if lines[i].strip() == "```"), len(lines))
            if end - index - 1 > 40:
                errors.append(f"Mermaid block at line {index + 1} has more than 40 lines; split or cut it")


def check_comment(comment: dict, lenses: dict, errors: list[str], warnings: list[str], head: str | None) -> None:
    cid = comment["id"]
    if comment["weight"] not in WEIGHTS:
        errors.append(f"{cid}: unknown weight '{comment['weight']}' (use {', '.join(WEIGHTS)})")
    lens = comment["lens"]
    scanner = lens.startswith("scanner:")
    if not scanner and lens not in lenses:
        errors.append(f"{cid}: unknown lens '{lens}'. Enabled lenses: {', '.join(sorted(lenses))}, or scanner:<RULE-ID>")
    if not comment["evidence"]:
        errors.append(f"{cid}: missing Evidence")
    if comment["weight"] == "blocking-suggested":
        if not comment["verified"]:
            errors.append(f"{cid}: blocking-suggested needs 'Verified by' (what you ran, traced or read)")
        if not scanner and lens in lenses and lenses[lens] != "if-verified":
            errors.append(f"{cid}: lens '{lens}' may not block (may_block: never). Use discuss")
    if not comment["checkbox"]:
        errors.append(f"{cid}: missing '- [ ] Post this comment'")
    body = " ".join(comment["quote"]).strip()
    if not body:
        errors.append(f"{cid}: missing comment text (a '> ' quote after '- **Comment:**')")
        return
    words = len(body.split())
    if words > MAX_WORDS:
        errors.append(f"{cid}: comment has {words} words (max {MAX_WORDS}). Cut it, comments are written by humans")
    if len(comment["quote"]) > 8:
        errors.append(f"{cid}: comment spans {len(comment['quote'])} lines; keep it short")
    in_code = False
    for line in comment["quote"]:
        if line.startswith("```"):
            in_code = not in_code
            continue
        if not in_code and MD_STRUCTURE.match(line):
            errors.append(f"{cid}: no headings, bullets or tables inside a comment: {line[:40]!r}")
            break
    if RULE_ID.search(body):
        errors.append(f"{cid}: do not put rule ids in the comment text ({RULE_ID.search(body).group(0)})")
    if AI_MENTION.search(body):
        errors.append(f"{cid}: do not mention AI tools in the comment text")
    if EMOJI.search(body.replace("👍", "")):
        errors.append(f"{cid}: no emoji in comments (except an occasional 👍 on praise)")
    if comment["weight"] != "praise":
        if IMPERATIVE.match(body):
            warnings.append(f"{cid}: starts as an order. Consider a question or a proposal ('Would it be simpler to...')")
        if HARSH.search(body):
            warnings.append(f"{cid}: harsh wording ('{HARSH.search(body).group(0)}'). Say the concrete consequence instead")
    anchor_path, _, anchor_line = comment["anchor"].partition(":")
    if head:
        total = line_count(head, anchor_path)
        if total is None:
            errors.append(f"{cid}: file {anchor_path} does not exist at {head}")
        elif anchor_line:
            if not anchor_line.isdigit() or not 1 <= int(anchor_line) <= total:
                errors.append(f"{cid}: line {anchor_line!r} is outside {anchor_path} ({total} lines at {head})")
        for token in sorted(set(SYMBOL.findall(body)) - COMMON):
            if not symbol_exists(head, token):
                warnings.append(f"{cid}: `{token}` was not found at {head}. Fine if you are proposing a new name; otherwise check it")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("draft", type=Path)
    parser.add_argument("--head", help="Ref to check anchors and symbols against (the reviewed head)")
    parser.add_argument("--profile", default="auto")
    parser.add_argument("--threads", type=Path, help="threads.json from threads.py: require a status row per earlier thread")
    parser.add_argument("--summary-out", type=Path, help="Write a summary.json that build_review_map.py --summary understands")
    args = parser.parse_args()

    text = args.draft.read_text(encoding="utf-8")
    errors: list[str] = []
    warnings: list[str] = []

    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or ".")
    import profiles as profile_loader
    try:
        profile = profile_loader.load_profile(args.profile, root)
        lenses = {lens_id: lens_loader.load_lens(lens_id)["may_block"] for lens_id in profile["lenses"]}
    except profile_loader.ProfileError as error:
        print(str(error), file=sys.stderr)
        return 2

    sections = split_sections(text)
    for name in REQUIRED_SECTIONS:
        if name not in sections:
            errors.append(f"Missing section '## {name}'")
    for label in ("Head", "Base", "Profile", "Suggested verdict"):
        if not re.search(rf"^- \*\*{label}:\*\*\s*\S", text, re.M):
            errors.append(f"Missing header field '- **{label}:**'")
    verdict_match = re.search(r"^- \*\*Suggested verdict:\*\*\s*`?([A-Z_]+)`?", text, re.M)
    verdict = verdict_match.group(1) if verdict_match else ""
    if verdict and verdict not in VERDICTS:
        errors.append(f"Suggested verdict must be {' or '.join(VERDICTS)} (never APPROVE, approval is the human's decision), found '{verdict}'")

    comments = parse_comments(sections.get("Suggested comments", []), errors)
    ids = [c["id"] for c in comments]
    if ids != [f"C{i}" for i in range(1, len(ids) + 1)]:
        errors.append(f"Comment ids must be C1, C2, ... in order, found {', '.join(ids) or 'none'}")
    for comment in comments:
        check_comment(comment, lenses, errors, warnings, args.head)
    if sum(c["weight"] == "praise" for c in comments) > MAX_PRAISE:
        errors.append(f"At most {MAX_PRAISE} praise comments")
    blocking = [c for c in comments if c["weight"] == "blocking-suggested"]
    if verdict in VERDICTS and (verdict == "REQUEST_CHANGES") != bool(blocking):
        warnings.append(f"Verdict {verdict} does not match {len(blocking)} blocking-suggested comment(s)")
    if args.threads:
        check_earlier_discussion(sections.get("Earlier discussion"), json.loads(args.threads.read_text(encoding="utf-8")), errors, warnings)
    check_mermaid(text, errors)

    if args.summary_out:
        findings = [{"rule": c["lens"], "severity": {"blocking-suggested": "major", "discuss": "minor", "minor": "nit"}[c["weight"]],
                     "path": c["anchor"].partition(":")[0], "line": int(c["anchor"].partition(":")[2] or 0) or None, "title": c["id"]}
                    for c in comments if c["weight"] in ("blocking-suggested", "discuss", "minor")]
        args.summary_out.write_text(json.dumps({"schemaVersion": 1, "head": args.head or "unknown", "findings": findings}, indent=2) + "\n")

    counts = {w: sum(c["weight"] == w for c in comments) for w in WEIGHTS}
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}")
    print(f"{len(comments)} comments {counts}; {len(errors)} errors, {len(warnings)} warnings")
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
