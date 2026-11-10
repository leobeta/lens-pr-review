#!/usr/bin/env python3
"""Score how confident the review is in each changed file, and render the human review map with links.

Every file has two independent measures: inherent risk from named signals, and verification confidence from
review evidence. Missing review records score 0.00; completed reviews start at 0.95 and missing, skipped, failed,
stale checks or unresolved findings lower that score. These are rubrics, not calibrated probabilities.

map.json schema 2 requires a revision matching scan.json and an entry for every reviewed file:
  {"schemaVersion": 2, "revision": "<scan revision>", "files": [{"path": "a/B.java", "signals": ["business-rule"],
    "verification": {"read": "Full file and diff inspected", "context": "Callers and acceptance criteria checked",
      "checks": [{"kind": "behavior", "status": "passed", "evidence": "Test#case, assertion and report path",
                  "revision": "<scan revision>"}]}, "risk": "one line", "verify": "yes/no question"}]}

Files at or below --threshold need human review. Missing review evidence is always visible. Reviewed focus files
must include a risk and verify question. See references/review-map.md for check requirements and scoring.
Scanner false positives can be cleared with a reason; summary findings cannot be cleared.

Links (--links): `pr` links to the PR's Files changed tab, `blob` to the file at a commit, `local` to a path relative
to the output file (works in VS Code and in .pr-review/lens/ reports), `absolute` to an absolute path (for chat replies),
`none` for plain text. Standard library only.

Usage: build_review_map.py --scan scan.json --map map.json [--summary summary.json] [--threshold 0.75]
         [--links pr --pr 412 | --links blob --ref <sha> | --links local | --links absolute | --links none]
         [--repo owner/name] [--format markdown|json] [--output map.md]
Exit codes: 0 = map rendered, 2 = invalid input (unknown signal or path, missing risk/verify, bad arguments).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 2
THRESHOLD = 0.75
MUST_READ_AT_MOST = 0.75
LARGE_CHANGE_LINES = 150
TOO_MANY_ITEMS = 15
RANGE_GAP = 10
MAX_RANGES = 4
LINES_PER_MINUTE = 25
MAX_MINUTES_PER_FILE = 20
RANGE = re.compile(r"^L(\d+)(?:-L(\d+))?$")


@dataclass(frozen=True)
class Signal:
    penalty: float
    label: str


# Penalties describe inherent risk; findings and uncertainty affect verification confidence separately.
SIGNALS = {
    "open-blocker": Signal(0.50, "a confirmed blocker is still open"),
    "disputed": Signal(0.35, "a finding is disputed and needs a human decision"),
    "open-major": Signal(0.30, "a confirmed major is still open"),
    "security": Signal(0.30, "security boundary"),
    "guard-rail": Signal(0.30, "a guard-rail test was changed, disabled or removed"),
    "contract": Signal(0.25, "public contract or event schema"),
    "concurrency": Signal(0.25, "concurrency"),
    "business-rule": Signal(0.20, "new or changed business rule"),
    "messaging": Signal(0.20, "messaging or delivery semantics"),
    "persistence": Signal(0.20, "persistence or transactions"),
    "prod-deploy": Signal(0.20, "production deployment or CI"),
    "intent-unclear": Signal(0.20, "unclear whether this matches the ticket's intent"),
    "design-decision": Signal(0.15, "design decision requiring scrutiny"),
    "unverified": Signal(0.15, "behaviour the agent could not verify"),
    "dependencies": Signal(0.12, "new or upgraded dependencies"),
    "untested-change": Signal(0.08, "behaviour change without a test that asserts it"),
    "data-semantics": Signal(0.08, "dates, rounding, charsets or locales"),
    "complex-logic": Signal(0.06, "dense branching or stream logic"),
    "error-handling": Signal(0.06, "new error handling or retries"),
    "large-change": Signal(0.06, f"more than {LARGE_CHANGE_LINES} added lines"),
    "open-minor": Signal(0.06, "a confirmed minor is still open"),
}

SCANNER_SIGNALS = {
    "Domain rules (business logic)": "business-rule",
    "Public contract / event schema": "contract",
    "Production deployment / CI": "prod-deploy",
    "Build / dependencies": "dependencies",
    "Guard-rail test changed (a boundary may have been relaxed)": "guard-rail",
    "Test removed": "guard-rail",
    "Security-sensitive": "security",
    "Expression evaluation (sandbox boundary)": "security",
    "Concurrency": "concurrency",
    "Persistence / transactions": "persistence",
    "Messaging / delivery semantics": "messaging",
    "Error handling / retries": "error-handling",
    "Data transformation semantics": "data-semantics",
}
FINDING_SIGNALS = {"blocker": "open-blocker", "major": "open-major", "minor": "open-minor"}
BEHAVIOURAL_TIERS = ("HIGH", "MEDIUM")

UNCERTAINTY_SIGNALS = {"intent-unclear", "unverified", "untested-change"}
FINDING_NAMES = {"open-blocker", "open-major", "open-minor", "disputed"}
INHERENT_SIGNALS = set(SIGNALS) - UNCERTAINTY_SIGNALS - FINDING_NAMES
SIGNAL_CHECKS = {
    "security": "boundary", "guard-rail": "boundary", "contract": "contract", "concurrency": "concurrency",
    "messaging": "integration", "persistence": "integration", "prod-deploy": "configuration",
    "dependencies": "configuration", "business-rule": "behavior", "data-semantics": "behavior",
    "complex-logic": "behavior", "error-handling": "behavior", "untested-change": "behavior",
}
CHECK_KINDS = set(SIGNAL_CHECKS.values()) | {"independent-review"}
CHECK_STATUSES = {"passed", "failed", "skipped", "not-applicable"}


def nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


class InvalidInput(ValueError):
    pass


@dataclass
class ScoredFile:
    path: str
    status: str
    added: int
    removed: int
    signals: list[str]
    cleared: dict[str, str]
    lines: list[str]
    risk: str
    verify: str
    narrowed: bool = False
    confidence: float = 0.0
    minutes: int = 0
    inherent_risk: float = 0.0
    review_status: str = "unreviewed"
    verification: dict = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)

    @property
    def risk_level(self) -> str:
        return "high" if self.inherent_risk >= 0.25 else "medium" if self.inherent_risk else "low"

    @property
    def why(self) -> str:
        return ", ".join(SIGNALS[signal].label for signal in self.signals)


@dataclass
class ReviewMap:
    threshold: float
    files: list[ScoredFile]
    focus: list[ScoredFile] = field(default_factory=list)
    revision: str = ""

    def __post_init__(self) -> None:
        self.files.sort(key=lambda f: (f.confidence, -f.added, f.path))
        self.focus = [f for f in self.files if f.confidence <= self.threshold]


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidInput(f"{path}: {error}") from error


def range_bounds(text: str) -> tuple[int, int]:
    match = RANGE.match(text)
    if not match:
        raise InvalidInput(f"line range {text!r} must look like L40 or L40-L72")
    start = int(match.group(1))
    return start, int(match.group(2) or start)


def inherent_risk_of(signals: list[str]) -> float:
    return round(1 - math.prod(1 - SIGNALS[signal].penalty for signal in signals if signal in INHERENT_SIGNALS), 2)


def merged(ranges: list[str]) -> list[str]:
    """Join ranges less than RANGE_GAP lines apart, so a reader gets a few useful links instead of many tiny ones."""
    spans: list[list[int]] = []
    for start, end in sorted(map(range_bounds, ranges)):
        if spans and start - spans[-1][1] <= RANGE_GAP:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return [f"L{a}" if a == b else f"L{a}-L{b}" for a, b in spans]


def minutes_for(entry: ScoredFile) -> int:
    """Reviewer-narrowed ranges are what a human reads; otherwise they read the file's whole diff."""
    narrowed = sum(end - start + 1 for start, end in map(range_bounds, entry.lines))
    lines = narrowed if entry.narrowed else entry.added or entry.removed
    return min(MAX_MINUTES_PER_FILE, max(1, math.ceil(lines / LINES_PER_MINUTE)))


def validate_map(review_map: dict, changed: set[str]) -> dict[str, dict]:
    if not isinstance(review_map, dict) or review_map.get("schemaVersion") != SCHEMA_VERSION:
        raise InvalidInput(f"map.json schemaVersion must be {SCHEMA_VERSION}; regenerate legacy review maps")
    entries = review_map.get("files")
    if not isinstance(entries, list):
        raise InvalidInput("map.json files must be a list")
    errors, by_path = [], {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append(f"files[{index}] must be an object with a path")
            continue
        path = entry["path"]
        if path not in changed:
            errors.append(f"files[{index}].path {path!r} is not a changed file in scan.json")
            continue
        if path in by_path:
            errors.append(f"{path}: duplicate review record")
        signals, cleared, lines = entry.get("signals", []), entry.get("cleared", []), entry.get("lines", [])
        if not isinstance(signals, list) or any(not isinstance(v, str) or v not in SIGNALS for v in signals):
            errors.append(f"{path}: unknown signals or invalid signals list")
        if not isinstance(cleared, list) or any(not isinstance(c, dict) or not isinstance(c.get("signal"), str)
                                              or c["signal"] not in SIGNALS or not nonblank(c.get("reason")) for c in cleared):
            errors.append(f"{path}: cleared signals must be known signals with a reason")
        if not isinstance(lines, list) or any(not isinstance(v, str) for v in lines):
            errors.append(f"{path}: lines must be a list of ranges")
        else:
            for text in lines:
                try:
                    range_bounds(text)
                except InvalidInput as error:
                    errors.append(f"{path}: {error}")
        for name in ("risk", "verify"):
            if name in entry and not isinstance(entry[name], str):
                errors.append(f"{path}: {name} must be text")
        if "verification" in entry:
            evidence = entry["verification"]
            if not isinstance(evidence, dict):
                errors.append(f"{path}: verification must be an object")
            else:
                for name in ("read", "context"):
                    if name in evidence and not isinstance(evidence[name], str):
                        errors.append(f"{path}: verification.{name} must be text")
                checks = evidence.get("checks", [])
                if not isinstance(checks, list):
                    errors.append(f"{path}: verification.checks must be a list")
                else:
                    for check in checks:
                        if (not isinstance(check, dict) or not isinstance(check.get("kind"), str)
                                or check["kind"] not in CHECK_KINDS or not isinstance(check.get("status"), str)
                                or check["status"] not in CHECK_STATUSES or not nonblank(check.get("evidence"))
                                or not nonblank(check.get("revision"))):
                            errors.append(f"{path}: each check needs a known kind/status, evidence and revision")
        by_path[path] = entry
    if errors:
        raise InvalidInput("\n  - " + "\n  - ".join(errors))
    return by_path


def verification_confidence(evidence: dict, signals: list[str], tier: str, revision: str) -> tuple[float, str, list[str]]:
    gaps = []
    if not evidence:
        gaps.append("Missing review record")
    else:
        for name in ("read", "context"):
            if not nonblank(evidence.get(name)):
                gaps.append(f"Missing {name} evidence")
    if gaps:
        return 0.0, "unreviewed", gaps

    confidence = 0.95
    required = {SIGNAL_CHECKS[s] for s in signals if s in SIGNAL_CHECKS}
    if tier in BEHAVIOURAL_TIERS:
        required.add("behavior")
    passed = set()
    for check in evidence.get("checks", []):
        kind, status = check["kind"], check["status"]
        if check["revision"] != revision:
            confidence = min(confidence, 0.75)
            gaps.append(f"{kind}: evidence is for a different revision")
        if status == "failed":
            confidence = min(confidence, 0.50)
            gaps.append(f"{kind}: failed — {check['evidence']}")
        elif status == "skipped":
            confidence = min(confidence, 0.65)
            gaps.append(f"{kind}: skipped — {check['evidence']}")
        elif status == "passed" and check["revision"] == revision:
            passed.add(kind)
        elif (status == "not-applicable" and check["revision"] == revision and kind == "behavior"
              and not any(SIGNAL_CHECKS.get(s) == kind for s in signals)):
            # A documented non-behavior change may need inspection only. Active risk checks cannot be waived.
            passed.add(kind)
    for kind in sorted(required - passed):
        confidence = min(confidence, 0.75)
        gaps.append(f"Missing {kind} check")
    for signal in signals:
        if signal in UNCERTAINTY_SIGNALS:
            confidence = min(confidence, 0.75)
            gaps.append(f"Unresolved {signal}: {SIGNALS[signal].label}")
    for signal in signals:
        if signal in FINDING_NAMES:
            confidence *= 1 - SIGNALS[signal].penalty
            gaps.append(f"{signal}: {SIGNALS[signal].label}")
    return round(confidence, 2), "reviewed", gaps


def finding_signals(summary: dict | None) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for finding in (summary or {}).get("findings", []):
        signals = result.setdefault(finding["path"], set())
        if finding["severity"] in FINDING_SIGNALS:
            signals.add(FINDING_SIGNALS[finding["severity"]])
        if finding.get("disputed"):
            signals.add("disputed")
    return result


def score(scan: dict, review_map: dict, summary: dict | None, threshold: float) -> ReviewMap:
    triage = {entry["file"]: entry for entry in scan["triage"]}
    judged = validate_map(review_map, set(triage))
    revision = scan.get("revision")
    if not nonblank(revision):
        raise InvalidInput("scan.json has no revision; rescan with the current scanner")
    if review_map.get("revision") != revision:
        raise InvalidInput("map.json revision must match scan.json revision; review the current snapshot")
    from_findings = finding_signals(summary)
    errors, files = [], []
    for path, entry in triage.items():
        own = judged.get(path, {})
        automatic = {sig for sig in entry.get("signals", []) if sig in SIGNALS}
        automatic |= {SCANNER_SIGNALS[r] for r in entry.get("reasons", []) if r in SCANNER_SIGNALS}
        if entry["tier"] in BEHAVIOURAL_TIERS and entry["added"] > LARGE_CHANGE_LINES:
            automatic.add("large-change")
        cleared = {c["signal"]: c["reason"] for c in own.get("cleared", [])}
        not_clearable = sorted(set(cleared) - automatic)
        if not_clearable:
            errors.append(f"{path}: only scanner signals can be cleared, not {', '.join(not_clearable)}")
        active = (automatic - set(cleared)) | from_findings.get(path, set()) | set(own.get("signals", []))
        signals = sorted(active, key=lambda s: (-SIGNALS[s].penalty, s))
        focus = [text for text in entry.get("focus", []) if RANGE.match(text)]
        scored = ScoredFile(path, entry.get("status", "M"), entry["added"], entry.get("removed", 0), signals,
                            {s: r for s, r in cleared.items() if s in automatic}, merged(own.get("lines") or focus),
                            own.get("risk", "").strip(), own.get("verify", "").strip(), narrowed=bool(own.get("lines")))
        scored.inherent_risk = inherent_risk_of(signals)
        scored.verification = own.get("verification", {})
        scored.confidence, scored.review_status, scored.gaps = verification_confidence(
            scored.verification, signals, entry["tier"], revision)
        if scored.review_status == "unreviewed":
            scored.risk = scored.risk or "; ".join(scored.gaps)
            scored.verify = scored.verify or "Has the full change and its affected context been reviewed?"
        scored.minutes = minutes_for(scored)
        if scored.confidence <= threshold and not (scored.risk and scored.verify):
            errors.append(f"{path}: confidence {scored.confidence:.2f} is at or below {threshold:.2f} "
                          f"({scored.why}), so map.json needs a risk and a verify question for it")
        files.append(scored)
    if errors:
        raise InvalidInput("\n  - " + "\n  - ".join(errors))
    return ReviewMap(threshold, files, revision=revision)


# ------------------------------------------------------------------------------------------------- links


class Links:
    def file(self, entry: ScoredFile) -> str | None:
        return None

    def lines(self, entry: ScoredFile, text: str) -> str | None:
        return None


class PrLinks(Links):
    def __init__(self, repo: str, pr: int) -> None:
        self.base = f"https://github.com/{repo}/pull/{pr}/files"

    def file(self, entry: ScoredFile) -> str:
        return f"{self.base}#diff-{hashlib.sha256(entry.path.encode('utf-8')).hexdigest()}"

    def lines(self, entry: ScoredFile, text: str) -> str:
        start, end = range_bounds(text)
        return f"{self.file(entry)}R{start}" + (f"-R{end}" if end != start else "")


class FileLinks(Links):
    """Links to the file as it is on the branch. Deleted files have nothing to link to."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def file(self, entry: ScoredFile) -> str | None:
        return None if entry.status == "D" else self.prefix + entry.path.replace(" ", "%20")

    def lines(self, entry: ScoredFile, text: str) -> str | None:
        target = self.file(entry)
        start, end = range_bounds(text)
        return target and f"{target}#L{start}" + (f"-L{end}" if end != start else "")


def repo_root() -> Path:
    return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip())


def origin_repo() -> str:
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not match:
        raise InvalidInput("can't work out the GitHub repository from origin: pass --repo owner/name")
    return match.group(1)


def make_links(args: argparse.Namespace) -> Links:
    if args.links == "pr":
        if not args.pr:
            raise InvalidInput("--links pr needs --pr <number>")
        return PrLinks(args.repo or origin_repo(), args.pr)
    if args.links == "blob":
        if not args.ref:
            raise InvalidInput("--links blob needs --ref <commit sha>")
        return FileLinks(f"https://github.com/{args.repo or origin_repo()}/blob/{args.ref}/")
    if args.links == "local":
        start = (args.output.resolve().parent if args.output else Path.cwd())
        return FileLinks(Path(os.path.relpath(repo_root(), start)).as_posix().rstrip("/") + "/")
    if args.links == "absolute":
        return FileLinks(repo_root().as_posix() + "/")
    return Links()


# ------------------------------------------------------------------------------------------------ render


def link(text: str, target: str | None) -> str:
    return f"[{text}]({target})" if target else text


def item(number: int, entry: ScoredFile, links: Links) -> list[str]:
    shown, hidden = entry.lines[:MAX_RANGES], len(entry.lines) - MAX_RANGES
    ranges = ", ".join(link(text, links.lines(entry, text)) for text in shown) + (f" (+{hidden} more)" if hidden > 0 else "")
    head = f"{number}. {link(f'`{entry.path}`', links.file(entry))}" + (f" {ranges}" if ranges else "")
    return [
        f"{head} · confidence **{entry.confidence:.2f}** · inherent risk **{entry.risk_level} ({entry.inherent_risk:.2f})** · {entry.review_status} · ~{entry.minutes} min",
        f"   Risk: {entry.risk}",
        f"   Why: {entry.why}",
        f"   Verify: {entry.verify}",
    ] + evidence_lines(entry)


def evidence_lines(entry: ScoredFile) -> list[str]:
    evidence = entry.verification
    out = [f"   Read: {evidence['read']}"] if nonblank(evidence.get("read")) else []
    if nonblank(evidence.get("context")):
        out.append(f"   Context: {evidence['context']}")
    out += [f"   Check — {c['kind']}: {c['status']} at `{c['revision']}` — {c['evidence']}"
            for c in evidence.get("checks", [])]
    if entry.gaps:
        out.append("   Gaps: " + "; ".join(entry.gaps))
    return out


def render_markdown(result: ReviewMap, links: Links) -> str:
    focus, skip = result.focus, [f for f in result.files if f.confidence > result.threshold]
    lines_total = sum(f.added for f in result.files) or 1
    focus_lines = sum(f.added for f in focus)
    out = [
        f"Human focus: {len(focus)} of {len(result.files)} files score at or below {result.threshold:.2f} confidence "
        f"({focus_lines} of {lines_total} added lines, {round(100 * focus_lines / lines_total)}%), "
        f"est. {sum(f.minutes for f in focus)} min. Safe to skip: {len(skip)} files.",
    ]
    out += ["", f"Reviewed snapshot: `{result.revision}`. Scores are evidence rubrics, not probabilities."]
    must = [f for f in focus if f.confidence <= MUST_READ_AT_MOST]
    should = [f for f in focus if f.confidence > MUST_READ_AT_MOST]
    number = 0
    for title, group in ((f"**Must read** (confidence at or below {min(MUST_READ_AT_MOST, result.threshold):.2f})", must),
                         (f"**Should read** (confidence above {MUST_READ_AT_MOST:.2f} and at or below {result.threshold:.2f})", should)):
        if group:
            out += ["", title, ""]
            for entry in group:
                number += 1
                out += item(number, entry, links)
    if not focus:
        out += ["", "No file scored at or below the threshold: the agent is confident in every change."]
    if len(focus) > TOO_MANY_ITEMS:
        out += ["", f"> More than {TOO_MANY_ITEMS} files need a human. This PR mixes concerns: consider reviewing it "
                    "commit by commit, or splitting it."]
    if skip:
        out += ["", f"<details><summary>Safe to skip ({len(skip)} files, confidence above {result.threshold:.2f})</summary>", ""]
        for entry in skip:
            out += [f"- {link(f'`{entry.path}`', links.file(entry))}: {entry.confidence:.2f} · "
                    f"inherent risk {entry.risk_level} ({entry.inherent_risk:.2f}) · {entry.review_status}"]
            out += evidence_lines(entry)
        out += ["", "</details>"]
    cleared = [(f, signal, reason) for f in result.files for signal, reason in f.cleared.items()]
    if cleared:
        out += ["", f"<details><summary>Scanner signals cleared by the reviewer ({len(cleared)})</summary>", ""]
        out += [f"- {link(f'`{f.path}`', links.file(f))}: {SIGNALS[signal].label}: {reason}" for f, signal, reason in cleared]
        out += ["", "</details>"]
    return "\n".join(out) + "\n"


def render_json(result: ReviewMap, links: Links) -> str:
    files = [{
        "path": f.path, "inherentRisk": f.inherent_risk, "reviewStatus": f.review_status,
        "verification": f.verification, "gaps": f.gaps, "confidence": f.confidence, "needsHuman": f.confidence <= result.threshold, "signals": f.signals,
        "cleared": f.cleared, "lines": f.lines, "minutes": f.minutes, "risk": f.risk, "verify": f.verify, "link": links.file(f),
    } for f in result.files]
    return json.dumps({"schemaVersion": SCHEMA_VERSION, "revision": result.revision, "threshold": result.threshold, "focus": len(result.focus), "files": files}, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scan", type=Path, required=True, help="scan.json written by scan_pr.py")
    parser.add_argument("--map", type=Path, required=True, help="map.json authored by the reviewer")
    parser.add_argument("--summary", type=Path, help="summary.json with the confirmed findings (self-review mode)")
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    parser.add_argument("--links", choices=("pr", "blob", "local", "absolute", "none"), default="local")
    parser.add_argument("--repo", help="owner/name. Default: from the origin remote")
    parser.add_argument("--pr", type=int, help="PR number, for --links pr")
    parser.add_argument("--ref", help="Commit SHA, for --links blob")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path, help="Write here instead of stdout")
    args = parser.parse_args()

    try:
        if not 0 < args.threshold <= 1:
            raise InvalidInput("--threshold must be between 0 and 1")
        result = score(load_json(args.scan), load_json(args.map), load_json(args.summary) if args.summary else None, args.threshold)
        if args.links == "blob" and (args.ref != result.revision or ":worktree:" in result.revision):
            raise InvalidInput("blob links require --ref equal to the scanned commit SHA; rescan after committing")
        links = make_links(args)
    except (InvalidInput, subprocess.CalledProcessError) as error:
        print(f"invalid input:{error}" if str(error).startswith("\n") else f"invalid input: {error}", file=sys.stderr)
        return 2

    rendered = (render_json if args.format == "json" else render_markdown)(result, links)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"{len(result.focus)} of {len(result.files)} files need a human -> {args.output}")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
