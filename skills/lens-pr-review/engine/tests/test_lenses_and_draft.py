#!/usr/bin/env python3
"""Tests for lens loading and the draft checker."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ENGINE))
import lenses  # noqa: E402
import profiles  # noqa: E402

CHECK = ENGINE / "check_draft.py"
EXAMPLE = str(ENGINE.parent.parent.parent / "examples" / "project-profile")

GOOD = """# PR review draft: Example (#1)

- **Head:** `abc`
- **Base:** `origin/master`
- **Profile:** `generic`
- **CI:** passing
- **Suggested verdict:** REQUEST_CHANGES

## In plain words
It adds a widget.

## Where to look
(map)

## Suggested comments

### C1 · `a.txt:1` · correctness-and-security · blocking-suggested
- **Evidence:** The total ignores the currency stored on the row.
- **Verified by:** Read `Formatter` and traced the value from the row to the tag.
- **Comment:**
> This shows the viewer's currency, not the one stored on the row. Could we pass the currency along?
- [ ] Post this comment

### C2 · `a.txt` · reuse-existing · discuss
- **Evidence:** Found `Helper` in util.
- **Comment:**
> Don't we already have `Helper` for this?
- [ ] Post this comment

## Dismissed scanner hits
| Rule | Location | Reason |
|---|---|---|

## Not verified
- Tests were not run.
"""


def run_check(text, *extra):
    path = Path(tempfile.mkdtemp()) / "draft.md"
    path.write_text(text)
    result = subprocess.run([sys.executable, str(CHECK), str(path), "--profile", "generic", *extra], capture_output=True, text=True)
    return result.returncode, result.stdout


class LensTests(unittest.TestCase):
    def test_every_lens_file_has_valid_front_matter_and_sections(self):
        for lens_id in lenses.all_lens_ids():
            lens = lenses.load_lens(lens_id)
            for section in ("## When to apply", "## What to do before commenting", "## How to phrase it", "## Example comments", "## Not a finding when"):
                self.assertIn(section, lens["text"], f"{lens_id} lacks {section}")

    def test_every_enabled_lens_exists_and_every_note_matches_a_lens(self):
        root = Path(tempfile.mkdtemp())
        for name in ("generic", "clean-code-java", "onion-layers", EXAMPLE):
            profile = profiles.load_profile(name, root)
            for lens_id in profile["lenses"]:
                lenses.load_lens(lens_id)
            self.assertEqual(lenses.unknown_note_files(profile), [])

    def test_blocking_lenses_are_the_ones_that_verify(self):
        blockers = {i for i in lenses.all_lens_ids() if lenses.load_lens(i)["may_block"] == "if-verified"}
        self.assertIn("correctness-and-security", blockers)
        self.assertNotIn("readability", blockers)
        self.assertNotIn("reuse-existing", blockers)


class DraftCheckerTests(unittest.TestCase):
    def test_good_draft_passes(self):
        code, out = run_check(GOOD)
        self.assertEqual(code, 0, out)

    def assert_error(self, text, fragment):
        code, out = run_check(text)
        self.assertEqual(code, 2, out)
        self.assertIn(fragment, out)

    def test_blocking_needs_verification(self):
        self.assert_error(GOOD.replace("- **Verified by:** Read `Formatter` and traced the value from the row to the tag.\n", ""), "needs 'Verified by'")

    def test_lens_that_may_not_block_cannot_block(self):
        self.assert_error(GOOD.replace("C2 · `a.txt` · reuse-existing · discuss", "C2 · `a.txt` · reuse-existing · blocking-suggested"), "may not block")

    def test_rule_ids_and_ai_mentions_are_rejected(self):
        self.assert_error(GOOD.replace("Could we pass the currency along?", "See NULL-003."), "rule ids")
        self.assert_error(GOOD.replace("Could we pass the currency along?", "Claude thinks so."), "AI tools")

    def test_long_comment_and_markdown_structure_are_rejected(self):
        self.assert_error(GOOD.replace("Could we pass the currency along?", "word " * 100), "words")
        self.assert_error(GOOD.replace("> This shows", "> - This shows"), "bullets")

    def test_approve_is_never_accepted(self):
        self.assert_error(GOOD.replace("REQUEST_CHANGES", "APPROVE"), "never APPROVE")

    def test_missing_section_checkbox_and_bad_numbering(self):
        self.assert_error(GOOD.replace("## Not verified", "## Other"), "Missing section '## Not verified'")
        self.assert_error(GOOD.replace("- [ ] Post this comment\n\n### C2", "\n### C2"), "missing '- [ ] Post this comment'")
        self.assert_error(GOOD.replace("### C2", "### C3"), "Comment ids must be")

    def test_mermaid_needs_a_source_line(self):
        diagram = "\n```mermaid\nflowchart LR\n  A-->B\n```\n"
        self.assert_error(GOOD.replace("## Dismissed", "## Diagrams" + diagram + "\n## Dismissed"), "Derived from")
        ok = GOOD.replace("## Dismissed", "## Diagrams\n*Derived from:* A.java\n" + diagram + "\n## Dismissed")
        self.assertEqual(run_check(ok)[0], 0)

    def test_tone_problems_are_warnings_not_errors(self):
        code, out = run_check(GOOD.replace("This shows the viewer's currency", "Remove the viewer's currency"))
        self.assertEqual(code, 0)
        self.assertIn("starts as an order", out)

    def test_anchors_and_symbols_are_checked_against_git(self):
        repo = Path(tempfile.mkdtemp())
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@e.com"], ["git", "config", "user.name", "T"]):
            subprocess.run(cmd, cwd=repo, check=True)
        (repo / "a.txt").write_text("one\ntwo\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=repo, check=True)
        draft = repo / "draft.md"
        draft.write_text(GOOD.replace("`a.txt:1`", "`a.txt:9`"))
        result = subprocess.run([sys.executable, str(CHECK), str(draft), "--profile", "generic", "--head", "HEAD"], cwd=repo, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("outside a.txt", result.stdout)
        draft.write_text(GOOD)
        result = subprocess.run([sys.executable, str(CHECK), str(draft), "--profile", "generic", "--head", "HEAD"], cwd=repo, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("`Helper` was not found", result.stdout)


if __name__ == "__main__":
    unittest.main()
