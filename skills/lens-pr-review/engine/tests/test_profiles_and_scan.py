#!/usr/bin/env python3
"""Tests for profile loading and the profile-driven scanner (runs against a throwaway git repository)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ENGINE))
import profiles  # noqa: E402

SCAN = ENGINE / "scan_pr.py"
EXAMPLE = str(ENGINE.parent.parent.parent / "examples" / "project-profile")


def run(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_project_profile_extends_generic_and_keeps_its_rules(self):
        project = profiles.load_profile(EXAMPLE, self.root)
        self.assertEqual(project["chain"], ["generic", "project-profile"])
        ids = {r["rule"] for r in project["rules"]}
        self.assertIn("SEC-001", ids)
        self.assertIn("ID-001", ids)
        self.assertNotIn("NULL-001", ids, "a project must not inherit the opinionated null rules by accident")

    def test_opinionated_profiles_are_opt_in(self):
        self.assertNotIn("NULL-001", {r["rule"] for r in profiles.load_profile("generic", self.root)["rules"]})
        clean = profiles.load_profile("clean-code-java", self.root)
        self.assertIn("NULL-001", {r["rule"] for r in clean["rules"]})
        self.assertIn("design-principles", clean["lenses"])

    def test_every_rule_uses_a_defined_scope_and_severity(self):
        for name in ("generic", "clean-code-java", "onion-layers", EXAMPLE):
            profile = profiles.load_profile(name, self.root)
            for rule in profile["rules"]:
                self.assertIn(rule["scope"], profile["scopes"], f"{name}:{rule['rule']}")
                self.assertIn(rule["severity"], ("blocker", "major", "minor", "nit"))

    def test_child_overrides_and_disables_by_key(self):
        local = self.root / profiles.LOCAL_PROFILE_DIR
        local.mkdir()
        (local / "profile.json").write_text(json.dumps({"extends": "generic", "disable_rules": ["LOG-001"], "lenses": {"disable": ["readability"]}}))
        merged = profiles.load_profile("auto", self.root)
        self.assertEqual(merged["name"], "local")
        self.assertNotIn("LOG-001", {r["rule"] for r in merged["rules"]})
        self.assertNotIn("readability", merged["lenses"])
        self.assertIn("reuse-existing", merged["lenses"])

    def test_unknown_profile_lists_the_known_ones(self):
        with self.assertRaises(profiles.ProfileError) as caught:
            profiles.load_profile("nope", self.root)
        self.assertIn("generic", str(caught.exception))

    def test_detects_a_profile_by_its_marker_and_falls_back_to_generic(self):
        built_in = Path(tempfile.mkdtemp())
        (built_in / "marked").mkdir()
        (built_in / "marked" / "profile.json").write_text(json.dumps({"detect": {"any_path_exists": [".marker"]}}))
        original = profiles.PROFILES_DIR
        profiles.PROFILES_DIR = built_in
        try:
            self.assertEqual(profiles.detect(self.root), "generic")
            (self.root / ".marker").write_text("")
            self.assertEqual(profiles.detect(self.root), "marked")
        finally:
            profiles.PROFILES_DIR = original

    def test_convention_docs_finds_nearest_and_root(self):
        (self.root / "AGENTS.md").write_text("root")
        (self.root / "mod" / "sub").mkdir(parents=True)
        (self.root / "mod" / "AGENTS.md").write_text("module")
        docs = profiles.convention_docs(["mod/sub/src/A.java"], ["AGENTS.md"], self.root)
        self.assertEqual(docs, ["AGENTS.md", "mod/AGENTS.md"])


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for command in (["git", "init", "-q", "-b", "master"], ["git", "config", "user.email", "t@example.com"], ["git", "config", "user.name", "T"]):
            run(self.root, *command)
        (self.root / "mod-a" / "src" / "main" / "java").mkdir(parents=True)
        (self.root / "mod-a" / "src" / "main" / "java" / "Old.java").write_text("class Old {}\n")
        (self.root / "mod-a-shared" / "src" / "main" / "java").mkdir(parents=True)
        (self.root / "mod-a-shared" / "src" / "main" / "java" / "Old.java").write_text("class Old {}\n")
        run(self.root, "git", "add", "-A")
        run(self.root, "git", "commit", "-q", "-m", "base")
        run(self.root, "git", "checkout", "-q", "-b", "change")

    def scan(self, profile="generic"):
        out = self.root / "scan.json"
        run(self.root, sys.executable, str(SCAN), "--profile", profile, "--base", "master", "--output", str(out))
        return json.loads(out.read_text())

    def commit(self, files):
        for path, text in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        run(self.root, "git", "add", "-A")
        run(self.root, "git", "commit", "-q", "-m", "change")

    def test_secret_and_empty_catch_are_found_and_tabs_are_not_flagged(self):
        self.commit({"mod-a/src/main/java/A.java": 'class A {\n\tString password = "hunter2hunter2";\n\tvoid f() { try { g(); } catch (Exception e) { } }\n}\n'})
        rules = [f["rule"] for f in self.scan()["findings"]]
        self.assertIn("SEC-001", rules)
        self.assertIn("EXC-002", rules)
        self.assertFalse([r for r in rules if r.startswith("FMT")])

    def test_prose_in_string_literals_and_comments_does_not_trigger_code_rules(self):
        self.commit({"mod-a/src/main/java/B.java": 'class B {\n  // System.out.println("x");\n  String s = "call .printStackTrace() later";\n}\n'})
        self.assertEqual([f["rule"] for f in self.scan()["findings"] if f["rule"] == "LOG-001"], [])

    def test_tests_missing_is_per_module_and_shared_modules_can_be_excluded(self):
        body = "class C {\n" + "  int a;\n" * 12 + "}\n"
        self.commit({"mod-a/src/main/java/C.java": body, "mod-a-shared/src/main/java/D.java": body})
        generic = [f["file"] for f in self.scan("generic")["findings"] if f["rule"] == "TEST-001"]
        self.assertEqual(sorted(generic), ["mod-a-shared/", "mod-a/"])
        project = [f["file"] for f in self.scan(EXAMPLE)["findings"] if f["rule"] == "TEST-001"]
        self.assertEqual(project, ["mod-a/"])

    def test_project_rule_applies_only_in_the_project_profile(self):
        self.commit({"mod-a/src/main/java/E.java": "class E {\n  java.util.Set<Long> ids;\n}\n"})
        self.assertNotIn("ID-001", [f["rule"] for f in self.scan("generic")["findings"]])
        self.assertIn("ID-001", [f["rule"] for f in self.scan(EXAMPLE)["findings"]])

    def test_opt_in_rules_apply_only_when_extended(self):
        self.commit({"mod-a/src/main/java/F.java": "class F {\n  Object f() { return null; }\n}\n"})
        self.assertNotIn("NULL-001", [f["rule"] for f in self.scan("generic")["findings"]])
        self.assertIn("NULL-001", [f["rule"] for f in self.scan("clean-code-java")["findings"]])

    def test_report_names_profile_and_signals_feed_the_map(self):
        self.commit({"mod-a/src/main/java/G.java": "class G {\n  java.math.BigDecimal x;\n}\n"})
        report = self.scan(EXAMPLE)
        self.assertEqual(report["profile"]["chain"][-1], "project-profile")
        entry = next(t for t in report["triage"] if t["file"].endswith("G.java"))
        self.assertIn("data-semantics", entry["signals"])


if __name__ == "__main__":
    unittest.main()
