#!/usr/bin/env python3
"""Tests for threads.py (earlier review threads and fix deltas) and the draft checker's enforcement of them."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ENGINE = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import threads  # noqa: E402
from test_lenses_and_draft import GOOD, run_check  # noqa: E402

THREADS = ENGINE / "threads.py"


def run(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def comment(cid, login, body, *, reply_to=None, line=10, commit="", kind="User", path="src/A.java"):
    return {"id": cid, "in_reply_to_id": reply_to, "user": {"login": login, "type": kind}, "body": body, "path": path,
            "line": line, "original_line": 10, "commit_id": commit, "original_commit_id": commit}


class ThreadCollectionTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        for command in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.com"], ["git", "config", "user.name", "T"]):
            run(self.repo, *command)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "A.java").write_text("class A {\n  int x = 1;\n}\n")
        (self.repo / "src" / "B.java").write_text("class B {}\n")
        run(self.repo, "git", "add", "-A")
        run(self.repo, "git", "commit", "-q", "-m", "first")
        self.commented = run(self.repo, "git", "rev-parse", "HEAD")
        (self.repo / "src" / "A.java").write_text("class A {\n  int x = 2;\n}\n")
        run(self.repo, "git", "commit", "-q", "-am", "fix x")
        self.out = self.repo / "out"
        self.raw = {
            "pr": {"author": "author", "headRefOid": run(self.repo, "git", "rev-parse", "HEAD")},
            "comments": [
                comment(1, "reviewer", "Why is x one?", commit=self.commented),
                comment(2, "author", "Changed it.", reply_to=1, commit=self.commented),
                comment(3, "author", "Note to self", commit=self.commented),
                comment(4, "copilot[bot]", "Style nit", kind="Bot", commit=self.commented),
                comment(5, "reviewer", "And B?", commit=self.commented, path="src/B.java", line=None),
                comment(6, "reviewer", "Gone commit", commit="f" * 40),
            ],
            "reviews": [
                {"id": 1, "user": {"login": "reviewer", "type": "User"}, "state": "CHANGES_REQUESTED", "body": "", "submitted_at": "2026-01-01T00:00:00Z"},
                {"id": 2, "user": {"login": "other", "type": "User"}, "state": "APPROVED", "body": "Looks good", "submitted_at": "2026-01-02T00:00:00Z"},
            ],
        }
        self.raw_file = self.repo / "raw.json"
        self.raw_file.write_text(json.dumps(self.raw))

    def collect(self):
        run(self.repo, sys.executable, str(THREADS), "--raw", str(self.raw_file), "--head", "HEAD", "--out", str(self.out))
        return json.loads((self.out / "threads.json").read_text())

    def test_only_human_reviewer_threads_need_a_status(self):
        result = self.collect()
        self.assertEqual(result["needing_status"], [1, 5, 6])
        by_id = {t["id"]: t for t in result["threads"]}
        self.assertFalse(by_id[3]["needs_status"], "the PR author's own comment")
        self.assertFalse(by_id[4]["needs_status"], "bots do not ask for fixes")
        self.assertTrue(by_id[1]["author_replied"])

    def test_delta_shows_what_changed_since_the_comment(self):
        result = self.collect()
        by_id = {t["id"]: t for t in result["threads"]}
        delta = by_id[1]["delta"]
        self.assertTrue(delta["available"] and delta["touched"])
        self.assertTrue(any("fix x" in c for c in delta["commits"]))
        self.assertIn("-  int x = 1;", Path(delta["diff_file"]).read_text())
        self.assertFalse(by_id[5]["delta"]["touched"], "B.java did not change")

    def test_missing_commit_is_reported_not_guessed(self):
        delta = {t["id"]: t for t in self.collect()["threads"]}[6]["delta"]
        self.assertFalse(delta["available"])
        self.assertIsNone(delta["touched"])
        self.assertIn("not in this clone", delta["note"])

    def test_outdated_and_review_states(self):
        result = self.collect()
        by_id = {t["id"]: t for t in result["threads"]}
        self.assertTrue(by_id[5]["outdated"])
        self.assertFalse(by_id[1]["outdated"])
        self.assertEqual(result["open_change_requests"], ["reviewer"])
        self.assertEqual([n["reviewer"] for n in result["review_notes"]], ["other"])

    def test_github_is_only_read(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with mock.patch.object(threads.subprocess, "run", fake_run):
            threads.gh_get("repos/o/r/pulls/1/comments")
        joined = " ".join(seen[0])
        for flag in ("-X", "--method", " -f ", " -F ", "--input", "POST", "PATCH", "DELETE"):
            self.assertNotIn(flag, f" {joined} ")


class EarlierDiscussionCheckTests(unittest.TestCase):
    def threads_file(self, delta_touched=True):
        data = {"threads": [
            {"id": 111, "path": "a.txt", "line": 1, "original_line": 1, "needs_status": True, "delta": {"available": True, "touched": delta_touched}},
            {"id": 222, "path": "b.txt", "line": 2, "original_line": 2, "needs_status": False, "delta": {"available": False, "touched": None}},
        ]}
        path = Path(tempfile.mkdtemp()) / "threads.json"
        path.write_text(json.dumps(data))
        return path

    def draft(self, rows):
        section = "## Earlier discussion\n| Thread | Request | Status | Evidence |\n|---|---|---|---|\n" + rows + "\n\n"
        return GOOD.replace("## Dismissed", section + "## Dismissed")

    def check(self, text, threads=None):
        return run_check(text, "--threads", str(threads or self.threads_file()))

    def test_a_cited_status_row_passes(self):
        code, out = self.check(self.draft("| `111` | Why x? | Addressed | Changed to 2 in `A.x`, commit abc1234 |"))
        self.assertEqual(code, 0, out)

    def test_missing_section_and_missing_row_are_errors(self):
        code, out = self.check(GOOD)
        self.assertEqual(code, 2)
        self.assertIn("Missing section '## Earlier discussion'", out)
        code, out = self.check(self.draft("| `999` | Other | Addressed | Changed in `x` today |"))
        self.assertEqual(code, 2)
        self.assertIn("no row for thread 111", out)

    def test_status_must_be_valid_and_evidence_present(self):
        code, out = self.check(self.draft("| `111` | Why x? | Fixed | Changed in `A.x` today |"))
        self.assertIn("no valid status", out)
        code, out = self.check(self.draft("| `111` | Why x? | Unclear | ok |"))
        self.assertIn("needs evidence", out)

    def test_addressed_must_cite_the_fix(self):
        code, out = self.check(self.draft("| `111` | Why x? | Addressed | The author said it was fixed |"))
        self.assertEqual(code, 2)
        self.assertIn("cites no fix", out)

    def test_untouched_file_marked_addressed_is_flagged_as_a_warning(self):
        code, out = self.check(self.draft("| `111` | Why x? | Addressed | Changed in `A.x` somewhere |"), self.threads_file(delta_touched=False))
        self.assertEqual(code, 0, out)
        self.assertIn("did not change since the comment", out)


if __name__ == "__main__":
    unittest.main()
