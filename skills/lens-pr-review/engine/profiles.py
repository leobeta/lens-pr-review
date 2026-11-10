#!/usr/bin/env python3
"""Profile loader shared by the engine scripts.

A profile is a directory under profiles/ (or a repo-local .lens-pr-review/) holding:
  profile.json   scopes, signals, convention files, enabled lenses, built-in checks, `extends`
  rules.json     mechanical line rules for the scanner (optional)
  lens-notes/    <lens-id>.md files appended to the matching lens when that profile is active (optional)

Profiles can extend others (`"extends": "generic"` or a list). Merge rules, parent first, child last:
  scopes, builtin_checks, scalars   child overrides by key
  rules, path_signals, line_signals child overrides by `rule` / `id`, otherwise appended; signals sort by `priority`
  disable_rules                     union, and the listed rule ids are removed from the final rule set
  lenses                            enabled = parents' enabled - child disable + child enable
  convention_files                  union, order kept

Standard library only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
SKILL_DIR = ENGINE_DIR.parent
PROFILES_DIR = SKILL_DIR / "profiles"
LOCAL_PROFILE_DIR = ".lens-pr-review"


class ProfileError(Exception):
    pass


def _read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError(f"Cannot read {path}: {error}") from error


def _locate(name: str, repo_root: Path) -> Path:
    candidate = Path(name)
    if candidate.is_dir() and (candidate / "profile.json").is_file():
        return candidate.resolve()
    built_in = PROFILES_DIR / name
    if (built_in / "profile.json").is_file():
        return built_in
    local = repo_root / LOCAL_PROFILE_DIR
    if name == "local" and (local / "profile.json").is_file():
        return local
    known = sorted(p.name for p in PROFILES_DIR.iterdir() if (p / "profile.json").is_file())
    raise ProfileError(f"Unknown profile '{name}'. Built-in profiles: {', '.join(known)}")


def _chain(directory: Path, repo_root: Path, seen: tuple[str, ...] = ()) -> list[Path]:
    """Directories to merge, parents first, the profile itself last."""
    if str(directory) in seen:
        raise ProfileError(f"Profile inheritance cycle at {directory}")
    raw = _read_json(directory / "profile.json")
    parents = raw.get("extends", [])
    parents = [parents] if isinstance(parents, str) else parents
    chain: list[Path] = []
    for parent in parents:
        for item in _chain(_locate(parent, repo_root), repo_root, seen + (str(directory),)):
            if item not in chain:
                chain.append(item)
    chain.append(directory)
    return chain


def _merge_by_key(parent: list[dict], child: list[dict], key: str) -> list[dict]:
    merged = {item[key]: item for item in parent}
    for item in child:
        merged[item[key]] = item
    return list(merged.values())


def _by_priority(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: item.get("priority", 50))


def detect(repo_root: Path) -> str:
    """Pick a profile for this repository: a repo-local one wins, then the first whose `detect` matches."""
    if (repo_root / LOCAL_PROFILE_DIR / "profile.json").is_file():
        return "local"
    for directory in sorted(PROFILES_DIR.iterdir()):
        profile = directory / "profile.json"
        if not profile.is_file() or directory.name == "generic":
            continue
        patterns = _read_json(profile).get("detect", {}).get("any_path_exists", [])
        if any(next(repo_root.glob(pattern), None) is not None for pattern in patterns):
            return directory.name
    return "generic"


def load_profile(name: str | None, repo_root: Path) -> dict:
    """Return the merged profile. `name` is a profile name, a directory, 'local', or None/'auto'."""
    if name in (None, "", "auto"):
        name = detect(repo_root)
    directory = _locate(name, repo_root)
    chain = _chain(directory, repo_root)

    merged: dict = {
        "name": name, "chain": [d.name for d in chain], "scopes": {}, "rules": [], "path_signals": [], "line_signals": [],
        "builtin_checks": {}, "convention_files": [], "lens_notes": [], "disable_rules": [],
    }
    enabled: list[str] = []
    for folder in chain:
        raw = _read_json(folder / "profile.json")
        for key in ("scopes", "builtin_checks"):
            merged[key].update(raw.get(key, {}))
        for key in ("description", "base_ref_candidates", "module_marker", "domain_logic_pattern"):
            if key in raw:
                merged[key] = raw[key]
        merged["path_signals"] = _merge_by_key(merged["path_signals"], raw.get("path_signals", []), "id")
        merged["line_signals"] = _merge_by_key(merged["line_signals"], raw.get("line_signals", []), "id")
        for convention in raw.get("convention_files", []):
            if convention not in merged["convention_files"]:
                merged["convention_files"].append(convention)
        merged["disable_rules"] += [r for r in raw.get("disable_rules", []) if r not in merged["disable_rules"]]
        lenses = raw.get("lenses", {})
        enabled = [lens for lens in enabled if lens not in lenses.get("disable", [])]
        enabled += [lens for lens in lenses.get("enable", []) if lens not in enabled]
        rules_file = folder / "rules.json"
        if rules_file.is_file():
            merged["rules"] = _merge_by_key(merged["rules"], _read_json(rules_file), "rule")
        notes = folder / "lens-notes"
        if notes.is_dir():
            merged["lens_notes"].append(notes)

    merged["rules"] = [r for r in merged["rules"] if r["rule"] not in merged["disable_rules"]]
    merged["path_signals"] = _by_priority(merged["path_signals"])
    merged["line_signals"] = _by_priority(merged["line_signals"])
    merged["lenses"] = enabled
    merged.setdefault("base_ref_candidates", ["origin/main", "origin/master"])
    merged.setdefault("module_marker", "/src/")
    merged.setdefault("domain_logic_pattern", "")
    return merged


def compile_scopes(scopes: dict) -> dict:
    """Scope name -> predicate(path). A scope matches when any `include` matches and no `exclude` does."""
    compiled = {}
    for name, spec in scopes.items():
        include = [re.compile(p) for p in spec.get("include", [".*"])]
        exclude = [re.compile(p) for p in spec.get("exclude", [])]

        def matches(path: str, include=include, exclude=exclude) -> bool:
            return any(p.search(path) for p in include) and not any(p.search(path) for p in exclude)

        compiled[name] = matches
    return compiled


def convention_docs(paths: list[str], convention_files: list[str], repo_root: Path) -> list[str]:
    """Nearest convention docs (e.g. AGENTS.md) for the changed paths, plus the repo-root ones, from the working tree."""
    found: list[str] = []
    wanted = list(convention_files)
    for pattern in wanted:
        if (repo_root / pattern).is_file() and pattern not in found:
            found.append(pattern)
    names = [p for p in wanted if "/" not in p]
    for path in paths:
        parent = Path(path).parent
        while str(parent) not in ("", "."):
            for name in names:
                candidate = (parent / name).as_posix()
                if (repo_root / candidate).is_file() and candidate not in found:
                    found.append(candidate)
            parent = parent.parent
    return found
