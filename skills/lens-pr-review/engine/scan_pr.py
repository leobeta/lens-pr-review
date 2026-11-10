#!/usr/bin/env python3
"""Deterministic pre-review scanner, driven by a profile.

Scans ONLY the lines a branch adds (relative to the merge-base with --base) and produces:
  * findings  - mechanical rule hits (rules come from the active profile's rules.json)
  * triage    - a per-file risk tier, reasons and signals, and the line ranges a human should read
  * convention_docs - the AGENTS.md-style docs nearest to the changed files (listed in the profile)

Findings are candidates. The reviewer must confirm or dismiss every one; none is dropped silently.
Nothing here is project specific: rules, scopes and signals live in profiles/<name>/.

Usage:
  scan_pr.py [--profile auto|<name>|<dir>] [--base <ref>] [--head <ref> | --worktree]
             [--format json|markdown] [--output FILE] [--fail-on never|blocker|major]

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles as profile_loader  # noqa: E402

SEVERITIES = ("blocker", "major", "minor", "nit")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}
TIERS = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "SKIP": 0}


# --------------------------------------------------------------------------------------------- git


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def ref_exists(ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], capture_output=True).returncode == 0


@dataclass
class ChangedFile:
    path: str
    status: str = "M"  # A, M, D, R
    old_path: str = ""
    similarity: int = 0
    binary: bool = False
    added: dict[int, str] = field(default_factory=dict)
    removed: int = 0
    content: list[str] = field(default_factory=list)
    comment_lines: set[int] = field(default_factory=set)


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(diff_text: str) -> list[ChangedFile]:
    files: list[ChangedFile] = []
    current: ChangedFile | None = None
    new_line = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            match = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            current = ChangedFile(path=match.group(2) if match else raw.split(" b/")[-1])
            current.old_path = match.group(1) if match else current.path
            files.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.status = "A"
        elif raw.startswith("deleted file mode"):
            current.status = "D"
        elif raw.startswith("similarity index"):
            current.status = "R"
            current.similarity = int(re.sub(r"\D", "", raw) or 0)
        elif raw.startswith("rename from "):
            current.old_path = raw[len("rename from "):]
        elif raw.startswith("rename to "):
            current.path = raw[len("rename to "):]
        elif raw.startswith("Binary files"):
            current.binary = True
        elif raw.startswith("+++ ") or raw.startswith("--- "):
            continue
        elif raw.startswith("@@"):
            hunk = HUNK.match(raw)
            new_line = int(hunk.group(1)) if hunk else 0
        elif raw.startswith("+"):
            current.added[new_line] = raw[1:]
            new_line += 1
        elif raw.startswith("-"):
            current.removed += 1
    return files


def load_content(changed: ChangedFile, head: str | None) -> None:
    if changed.status == "D" or changed.binary:
        return
    try:
        text = Path(changed.path).read_text(encoding="utf-8", errors="replace") if head is None else git("show", f"{head}:{changed.path}")
    except (OSError, SystemExit):
        return
    changed.content = text.splitlines()
    changed.comment_lines = comment_line_numbers(changed.content) if changed.path.endswith(".java") else set()


def comment_line_numbers(lines: list[str]) -> set[int]:
    """1-based line numbers that are entirely comments (//, /* */, Javadoc)."""
    result: set[int] = set()
    in_block = False
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if in_block:
            result.add(number)
            in_block = "*/" not in stripped
            continue
        if stripped.startswith("//") or stripped.startswith("*"):
            result.add(number)
        elif stripped.startswith("/*"):
            result.add(number)
            in_block = "*/" not in stripped
    return result


STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')
TRAILING_COMMENT = re.compile(r"\s//.*$")


def code_only(text: str) -> str:
    """Strip string literals and trailing // comments so code rules do not fire on prose."""
    return TRAILING_COMMENT.sub("", STRING_LITERAL.sub('""', text))


# ----------------------------------------------------------------------------------------- predicates


def statement_from(changed: ChangedFile, number: int, max_lines: int = 15) -> str:
    """The statement starting at `number`, joined until the terminating semicolon."""
    parts: list[str] = []
    for index in range(number - 1, min(number - 1 + max_lines, len(changed.content))):
        parts.append(code_only(changed.content[index]).strip())
        if parts[-1].endswith(";") or parts[-1].endswith("{"):
            break
    return " ".join(parts)


def next_code_line(changed: ChangedFile, number: int) -> str:
    for index in range(number, min(number + 5, len(changed.content))):
        stripped = changed.content[index].strip()
        if stripped and not stripped.startswith("@"):
            return stripped
    return ""


def var_not_new_or_build(changed: ChangedFile, number: int, text: str) -> bool:
    statement = statement_from(changed, number) if changed.content else code_only(text)
    rhs = statement.split("=", 1)[1].strip() if "=" in statement else ""
    return not (rhs.startswith("new ") or re.search(r"\.build\(\)\s*;?$", rhs))


def field_injection(changed: ChangedFile, number: int, text: str) -> bool:
    inline = code_only(text).split("@Autowired", 1)[1].strip()
    declaration = inline or next_code_line(changed, number)
    return declaration.endswith(";") and "(" not in declaration


def annotates_class(changed: ChangedFile, number: int, text: str) -> bool:
    return bool(re.search(r"\bclass\s+\w+", next_code_line(changed, number)))


PREDICATES: dict[str, Callable[[ChangedFile, int, str], bool]] = {
    "var_not_new_or_build": var_not_new_or_build,
    "field_injection": field_injection,
    "annotates_class": annotates_class,
}


# ----------------------------------------------------------------------------------------- findings


@dataclass
class Finding:
    rule: str
    severity: str
    category: str
    title: str
    file: str
    line: int | None
    snippet: str
    guidance: str


@dataclass
class LineRule:
    rule: str
    severity: str
    category: str
    title: str
    guidance: str
    pattern: re.Pattern[str]
    scope: Callable[[str], bool]
    raw: bool = False  # match the raw line (secrets) instead of code_only()
    include_comments: bool = False
    predicate: Callable[[ChangedFile, int, str], bool] | None = None
    once_per_file: bool = False

    def matches(self, changed: ChangedFile, number: int, text: str) -> bool:
        if not self.scope(changed.path):
            return False
        if not self.include_comments and number in changed.comment_lines:
            return False
        subject = text if self.raw else code_only(text)
        if not self.pattern.search(subject):
            return False
        return self.predicate is None or self.predicate(changed, number, text)


def build_rules(profile: dict, scopes: dict[str, Callable[[str], bool]]) -> list[LineRule]:
    rules = []
    for spec in profile["rules"]:
        if spec["severity"] not in SEVERITY_RANK:
            raise SystemExit(f"Rule {spec['rule']}: unknown severity '{spec['severity']}'")
        if spec["scope"] not in scopes:
            raise SystemExit(f"Rule {spec['rule']}: unknown scope '{spec['scope']}' (profile defines {', '.join(sorted(scopes))})")
        predicate = spec.get("predicate")
        if predicate and predicate not in PREDICATES:
            raise SystemExit(f"Rule {spec['rule']}: unknown predicate '{predicate}'")
        rules.append(LineRule(spec["rule"], spec["severity"], spec["category"], spec["title"], spec["guidance"],
                              re.compile(spec["pattern"]), scopes[spec["scope"]], spec.get("raw", False),
                              spec.get("include_comments", False), PREDICATES.get(predicate) if predicate else None,
                              spec.get("once_per_file", False)))
    return rules


# --------------------------------------------------------------------------- built-in file-level checks


def module_of(path: str, marker: str) -> str:
    """The module a path belongs to: everything before `marker` (default /src/), else the first segment."""
    index = f"/{path}".find(marker)
    return path[: index - 1] if index > 1 else path.split("/", 1)[0]


def check_tests_missing_in_module(files, scopes, options, head, marker) -> list[Finding]:
    production: dict[str, int] = {}
    tested: set[str] = set()
    for changed in files:
        if changed.status == "D":
            continue
        module = module_of(changed.path, marker)
        if scopes["production"](changed.path):
            code = [n for n in changed.added if n not in changed.comment_lines and changed.added[n].strip()]
            production[module] = production.get(module, 0) + len(code)
        if scopes["test"](changed.path):
            tested.add(module)
    minimum = options.get("min_lines", 10)
    skip = re.compile(options["exclude_module_regex"]) if options.get("exclude_module_regex") else None
    production = {m: n for m, n in production.items() if not (skip and skip.search(m))}
    return [
        Finding("TEST-001", options.get("severity", "major"), "tests", f"Production code changed in '{module}' with no test changes", f"{module}/",
                None, f"{lines} added production lines", "Add or update tests for the new or changed behaviour.")
        for module, lines in sorted(production.items()) if lines >= minimum and module not in tested
    ]


NON_BEHAVIOURAL = re.compile(
    r"\b(?:record|interface|enum|@interface)\s+{name}\b|\bclass\s+{name}\s+extends\s+\w*(?:Exception|Error)\b"
    r"|@(?:Configuration|ConfigurationProperties|SpringBootApplication)\b"
)


def referenced_by_tests(name: str, head: str | None, changed_tests: str, test_globs: list[str]) -> bool:
    if re.search(rf"\b{re.escape(name)}\b", changed_tests):
        return True
    tree = [] if head is None else [head]
    result = subprocess.run(["git", "grep", "-q", "-w", "-F", name, *tree, "--", *test_globs], capture_output=True)
    return result.returncode == 0


def check_new_class_without_test(files, scopes, options, head, marker) -> list[Finding]:
    tracked = git("ls-files") if head is None else git("ls-tree", "-r", "--name-only", head)
    test_names = {Path(p).stem for p in tracked.splitlines() if scopes["test"](p)}
    changed_tests = "\n".join("\n".join(f.content) for f in files if scopes["test"](f.path))
    suffixes = options.get("suffixes", ["Test", "Tests", "IT", "IntegrationTest"])
    test_globs = options.get("test_globs", ["*/src/test/*"])
    findings = []
    for changed in files:
        name = Path(changed.path).stem
        if changed.status != "A" or not changed.path.endswith(".java") or not scopes["production"](changed.path) or name == "package-info":
            continue
        if re.search(NON_BEHAVIOURAL.pattern.format(name=re.escape(name)), "\n".join(changed.content)):
            continue
        if not any(f"{name}{suffix}" in test_names for suffix in suffixes) and not referenced_by_tests(name, head, changed_tests, test_globs):
            findings.append(Finding("TEST-002", options.get("severity", "minor"), "tests", "New behavioural class without tests", changed.path, None, "",
                                    f"No test references {name}. Say which test covers it, or add one."))
    return findings


def check_test_deleted(files, scopes, options, head, marker) -> list[Finding]:
    return [
        Finding("TEST-004", options.get("severity", "major"), "tests", "Test file deleted", changed.path, None, "",
                "Confirm the behaviour is still covered elsewhere; deleted tests need an explicit reason.")
        for changed in files if changed.status == "D" and scopes["test"](changed.path) and changed.path.endswith(".java")
    ]


BUILTIN_CHECKS = {
    "tests-missing-in-module": check_tests_missing_in_module,
    "new-class-without-test": check_new_class_without_test,
    "test-deleted": check_test_deleted,
}


# ------------------------------------------------------------------------------------------- triage


@dataclass
class TriageEntry:
    file: str
    status: str
    added: int
    removed: int
    tier: str
    reasons: list[str]
    signals: list[str]
    focus: list[str]


def ranges(numbers: Iterable[int], gap: int = 3, limit: int = 12) -> list[str]:
    result: list[list[int]] = []
    for number in sorted(set(numbers)):
        if result and number - result[-1][1] <= gap:
            result[-1][1] = number
        else:
            result.append([number, number])
    return [f"L{a}" if a == b else f"L{a}-L{b}" for a, b in result[:limit]] + (["…"] if len(result) > limit else [])


def triage(files: list[ChangedFile], findings: list[Finding], profile: dict, scopes: dict) -> list[TriageEntry]:
    path_signals = [(s, re.compile(s["path"])) for s in profile["path_signals"]]
    line_signals = [(s, re.compile(s["line"])) for s in profile["line_signals"]]
    domain_logic = re.compile(profile["domain_logic_pattern"]) if profile["domain_logic_pattern"] else None
    blocking = {(f.file, f.line) for f in findings if f.severity in ("blocker", "major")}
    entries = []
    for changed in files:
        reasons: list[str] = []
        signals: list[str] = []
        tier = "MEDIUM"
        matched = next((s for s, pattern in path_signals if pattern.search(changed.path)), None)
        if matched:
            tier = matched["tier"]
            reasons.append(matched["reason"])
            if matched.get("signal"):
                signals.append(matched["signal"])
        focus: set[int] = set()
        if domain_logic and scopes.get("domain", lambda _: False)(changed.path):
            logic = [n for n, text in changed.added.items() if n not in changed.comment_lines and domain_logic.search(code_only(text))]
            if logic:
                tier = "HIGH"
                reasons.append("Domain rules (business logic)")
                signals.append("business-rule")
                focus.update(logic)
        if tier not in ("LOW", "SKIP") and not changed.path.endswith(".md"):
            for signal, pattern in line_signals:
                hits = [n for n, text in changed.added.items()
                        if n not in changed.comment_lines and pattern.search(code_only(text) if changed.path.endswith(".java") else text)]
                if hits:
                    reasons.append(signal["reason"])
                    if signal.get("signal"):
                        signals.append(signal["signal"])
                    focus.update(hits)
                    tier = max(tier, signal["tier"], key=TIERS.get)
        flagged = sorted(line for file, line in blocking if file == changed.path and line)
        if flagged:
            reasons.append("Confirmed-candidate blocker/major findings")
            focus.update(flagged)
            tier = max(tier, "HIGH", key=TIERS.get) if tier != "SKIP" else tier
        if changed.status == "D" and scopes["test"](changed.path):
            tier, reasons, signals = "HIGH", reasons + ["Test removed"], signals + ["guard-rail"]
        if changed.status == "R" and changed.similarity == 100:
            tier, reasons, signals = "LOW", ["Pure rename (100% similarity)"], []
        if tier == "HIGH" and not focus:
            focus.update(changed.added)
        entries.append(TriageEntry(changed.path, changed.status, len(changed.added), changed.removed, tier, reasons, sorted(set(signals)), ranges(focus)))
    entries.sort(key=lambda e: (-TIERS[e.tier], -e.added, e.file))
    return entries


# ------------------------------------------------------------------------------------------- output


def build_report(base: str, head_label: str, merge_base: str, files: list[ChangedFile], findings: list[Finding],
                 profile: dict, scopes: dict, docs: list[str]) -> dict:
    entries = triage(files, findings, profile, scopes)
    added_by_tier = {tier: sum(e.added for e in entries if e.tier == tier) for tier in TIERS}
    total_added = sum(e.added for e in entries) or 1
    return {
        "profile": {"name": profile["name"], "chain": profile["chain"], "lenses": profile["lenses"]},
        "base": base,
        "head": head_label,
        "merge_base": merge_base,
        "convention_docs": docs,
        "stats": {
            "files": len(files),
            "added": sum(len(f.added) for f in files),
            "removed": sum(f.removed for f in files),
            "files_by_tier": {tier: sum(1 for e in entries if e.tier == tier) for tier in TIERS},
            "added_lines_by_tier": added_by_tier,
            "human_focus_share": round(100 * added_by_tier["HIGH"] / total_added, 1),
        },
        "findings_by_severity": {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES},
        "findings": [asdict(f) for f in findings],
        "triage": [asdict(e) for e in entries],
    }


def render_markdown(report: dict) -> str:
    stats = report["stats"]
    out = [
        "## PR pre-review scan",
        "",
        f"Profile `{report['profile']['name']}` (extends: {', '.join(report['profile']['chain'][:-1]) or 'nothing'}). "
        f"`{report['base']}...{report['head']}`: **{stats['files']} files**, +{stats['added']} / -{stats['removed']} lines. "
        f"Human focus: **{stats['files_by_tier']['HIGH']} files / {stats['added_lines_by_tier']['HIGH']} added lines ({stats['human_focus_share']}%)**.",
        "",
        "Convention docs to read: " + (", ".join(f"`{d}`" for d in report["convention_docs"]) or "none found"),
        "",
        "| Severity | Count |", "|---|---|",
        *[f"| {s} | {n} |" for s, n in report["findings_by_severity"].items()],
        "",
        "### Human review map (HIGH)",
        "",
        "| File | +/- | Why | Look at |", "|---|---|---|---|",
    ]
    for entry in (e for e in report["triage"] if e["tier"] == "HIGH"):
        out.append(f"| `{entry['file']}` | +{entry['added']}/-{entry['removed']} | {'; '.join(entry['reasons'])} | {', '.join(entry['focus'])} |")
    medium = [e for e in report["triage"] if e["tier"] == "MEDIUM"]
    low = [e for e in report["triage"] if e["tier"] in ("LOW", "SKIP")]
    out += ["", f"<details><summary>Skim ({len(medium)} MEDIUM files)</summary>", ""]
    out += [f"- `{e['file']}` (+{e['added']}/-{e['removed']}): {'; '.join(e['reasons'])}" for e in medium]
    out += ["", "</details>", "", f"<details><summary>Safe to skip ({len(low)} LOW/SKIP files)</summary>", ""]
    out += [f"- `{e['file']}`: {'; '.join(e['reasons'])}" for e in low]
    out += ["", "</details>", "", "### Rule findings (candidates, confirm before raising)", "",
            "| Severity | Rule | Location | Issue | Snippet |", "|---|---|---|---|---|"]
    for finding in sorted(report["findings"], key=lambda f: (SEVERITY_RANK[f["severity"]], f["file"], f["line"] or 0)):
        location = f"`{finding['file']}`" + (f":{finding['line']}" if finding["line"] else "")
        snippet = finding["snippet"].replace("|", "\\|").replace("`", "'")
        out.append(f"| {finding['severity']} | {finding['rule']} | {location} | {finding['title']} | `{snippet}` |")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------------- main


def revision_id(head: str | None) -> str:
    """Bind review evidence to a commit or to the complete dirty worktree snapshot."""
    commit = git("rev-parse", f"{head or 'HEAD'}^{{commit}}").strip()
    if head is not None:
        return commit
    digest = hashlib.sha256()
    diff = subprocess.run(["git", "diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-color", "HEAD"],
                          capture_output=True, check=True).stdout
    digest.update(diff)
    for path in sorted(git("ls-files", "--others", "--exclude-standard", "-z").split("\0")):
        if not path:
            continue
        target = Path(path)
        digest.update(path.encode() + b"\0")
        if target.is_symlink():
            digest.update(hashlib.sha256(target.readlink().as_posix().encode()).digest())
        elif target.is_file():
            digest.update(hashlib.sha256(target.read_bytes()).digest())
        digest.update(b"\0")
    return f"{commit}:worktree:{digest.hexdigest()}"


def choose_base(requested: str | None, candidates: list[str]) -> str:
    if requested:
        return requested
    for candidate in candidates:
        if ref_exists(candidate):
            return candidate
    raise SystemExit(f"No base ref found (tried {', '.join(candidates)}). Pass --base <ref>.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="auto", help="Profile name, directory, 'local' or 'auto' (default)")
    parser.add_argument("--base", help="Base ref (merge-base is used). Default: first existing of the profile's candidates")
    parser.add_argument("--head", default="HEAD", help="Head ref. Default: HEAD")
    parser.add_argument("--worktree", action="store_true", help="Scan the working tree (includes uncommitted changes) instead of --head")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", help="Write to this file instead of stdout")
    parser.add_argument("--fail-on", choices=("never", "blocker", "major"), default="never")
    args = parser.parse_args()

    output = Path(args.output).absolute() if args.output else None
    root = Path(git("rev-parse", "--show-toplevel").strip())
    os.chdir(root)

    try:
        profile = profile_loader.load_profile(args.profile, root)
    except profile_loader.ProfileError as error:
        raise SystemExit(str(error))
    scopes = profile_loader.compile_scopes(profile["scopes"])
    for required in ("production", "test"):
        if required not in scopes:
            raise SystemExit(f"Profile '{profile['name']}' must define the scopes 'production' and 'test'")
    rules = build_rules(profile, scopes)

    base = choose_base(args.base, profile["base_ref_candidates"])
    head = None if args.worktree else args.head
    revision = revision_id(head)
    merge_base = git("merge-base", base, args.head if head else "HEAD").strip()
    diff_args = ["diff", "-U0", "-M", "--no-color", "--no-ext-diff", merge_base] + ([head] if head else [])
    files = parse_diff(git(*diff_args))
    if head is None:
        for path in git("ls-files", "--others", "--exclude-standard").splitlines():
            files.append(ChangedFile(path=path, status="A"))
    for changed in files:
        load_content(changed, head)
        if head is None and changed.status == "A" and not changed.added:
            changed.added = dict(enumerate(changed.content, start=1))

    findings: list[Finding] = []
    for changed in files:
        reported_once: set[str] = set()
        for number, text in sorted(changed.added.items()):
            for rule in rules:
                if rule.rule in reported_once:
                    continue
                if rule.matches(changed, number, text):
                    if rule.once_per_file:
                        reported_once.add(rule.rule)
                    findings.append(Finding(rule.rule, rule.severity, rule.category, rule.title, changed.path, number, text.strip()[:160], rule.guidance))
    for name, options in profile["builtin_checks"].items():
        if name not in BUILTIN_CHECKS:
            raise SystemExit(f"Unknown built-in check '{name}'. Known: {', '.join(BUILTIN_CHECKS)}")
        if options.get("enabled", True):
            findings.extend(BUILTIN_CHECKS[name](files, scopes, options, head, profile["module_marker"]))

    if revision_id(head) != revision:
        raise SystemExit("Snapshot changed while scanning; run the scanner again")
    docs = profile_loader.convention_docs([f.path for f in files], profile["convention_files"], root)
    report = build_report(base, "WORKTREE" if head is None else args.head, merge_base, files, findings, profile, scopes, docs)
    report["revision"] = revision
    rendered = json.dumps(report, indent=2) + "\n" if args.format == "json" else render_markdown(report)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)

    threshold = {"never": -1, "blocker": 0, "major": 1}[args.fail_on]
    return 1 if any(SEVERITY_RANK[f.severity] <= threshold for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
