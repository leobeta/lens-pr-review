#!/usr/bin/env python3
"""Assemble the lens playbook for a profile.

Each lens is one Markdown file in lenses/ with a small front matter (id, summary, may_block). A profile enables lenses
(`lenses.enable` / `lenses.disable`) and may add notes per lens in profiles/<name>/lens-notes/<lens-id>.md. Adding or
changing a lens is editing one file; no code changes.

Usage:
  lenses.py [--profile auto|<name>|<dir>] [--list | --lens <id>]

Without flags, prints the full playbook (lens text plus the profile's notes) for the reviewer to follow.
Standard library only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles as profile_loader  # noqa: E402

LENSES_DIR = profile_loader.SKILL_DIR / "lenses"
MAY_BLOCK = ("never", "if-verified")


def parse_front_matter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data


def load_lens(lens_id: str) -> dict:
    path = LENSES_DIR / f"{lens_id}.md"
    if not path.is_file():
        raise profile_loader.ProfileError(f"Lens '{lens_id}' is enabled but {path} does not exist")
    text = path.read_text(encoding="utf-8")
    meta = parse_front_matter(text)
    if meta.get("id") != lens_id:
        raise profile_loader.ProfileError(f"{path}: front matter id must be '{lens_id}', found '{meta.get('id')}'")
    if meta.get("may_block") not in MAY_BLOCK:
        raise profile_loader.ProfileError(f"{path}: may_block must be one of {', '.join(MAY_BLOCK)}")
    return {"id": lens_id, "summary": meta.get("summary", ""), "may_block": meta["may_block"], "text": text, "path": path}


def all_lens_ids() -> list[str]:
    return sorted(p.stem for p in LENSES_DIR.glob("*.md"))


def notes_for(lens_id: str, profile: dict) -> list[tuple[str, str]]:
    result = []
    for directory in profile["lens_notes"]:
        note = directory / f"{lens_id}.md"
        if note.is_file():
            result.append((directory.parent.name, note.read_text(encoding="utf-8").strip()))
    return result


def unknown_note_files(profile: dict) -> list[str]:
    known = set(all_lens_ids())
    return [str(f) for d in profile["lens_notes"] for f in d.glob("*.md") if f.stem not in known]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="auto")
    parser.add_argument("--list", action="store_true", help="One line per enabled lens")
    parser.add_argument("--lens", help="Print only this lens (with the profile's notes)")
    args = parser.parse_args()

    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or ".")
    try:
        profile = profile_loader.load_profile(args.profile, root)
        lenses = [load_lens(lens_id) for lens_id in profile["lenses"]]
    except profile_loader.ProfileError as error:
        print(str(error), file=sys.stderr)
        return 2
    orphans = unknown_note_files(profile)
    if orphans:
        print("Lens notes that match no lens: " + ", ".join(orphans), file=sys.stderr)
        return 2

    if args.list:
        for lens in lenses:
            print(f"{lens['id']:28} may_block={lens['may_block']:11} {lens['summary']}")
        return 0
    selected = [lens for lens in lenses if args.lens in (None, lens["id"])]
    if not selected:
        print(f"Lens '{args.lens}' is not enabled in profile '{profile['name']}'", file=sys.stderr)
        return 2
    print(f"# Lens playbook for profile `{profile['name']}` (chain: {' -> '.join(profile['chain'])})\n")
    for lens in selected:
        print(lens["text"].strip() + "\n")
        for source, note in notes_for(lens["id"], profile):
            print(f"## Notes from profile `{source}`\n\n{note}\n")
        print("---\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
