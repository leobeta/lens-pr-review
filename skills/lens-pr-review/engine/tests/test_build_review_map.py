#!/usr/bin/env python3
"""Regression tests for evidence-based human review maps."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPTS))
import build_review_map as brm  # noqa: E402

DOMAIN = "app/src/main/java/x/domain/Rounding.java"
ADAPTER = "app/src/main/java/x/infrastructure/MongoStore.java"
TEST = "app/src/test/java/x/domain/RoundingTest.java"
REVISION = "a" * 40


def triage(path, tier="MEDIUM", reasons=None, added=20, focus=None, status="M"):
    return dict(file=path, status=status, added=added, removed=0, tier=tier, reasons=reasons or [], focus=focus or [])


def scan(*entries):
    return dict(revision=REVISION, triage=list(entries))


def review_map(*files):
    return dict(schemaVersion=2, revision=REVISION, files=list(files))


def check(kind="behavior", status="passed", **extra):
    return dict(kind=kind, status=status, evidence="RoundingTest#negativeHalf: -0.5 must become -1; surefire report passed",
                revision=REVISION, **extra)


def judged(path, checks=None, **extra):
    entry = dict(path=path, risk="Some risk", verify="Is it right?",
                verification=dict(read="Read the full file and diff", context="Checked Rounding callers and ticket acceptance criteria",
                                  checks=[check()] if checks is None else checks))
    entry.update(extra)
    return entry


class ScoringTest(unittest.TestCase):
    def score(self, entry, reasons=None, summary=None, threshold=0.75, tier="HIGH"):
        return brm.score(scan(triage(DOMAIN, tier, reasons)), review_map(entry), summary, threshold)

    def test_security_risk_stays_high_after_successful_verification(self):
        weak = self.score(judged(DOMAIN), ["Security-sensitive"])
        strong = self.score(judged(DOMAIN, checks=[check(), check("boundary")]), ["Security-sensitive"])
        self.assertEqual(weak.files[0].inherent_risk, strong.files[0].inherent_risk)
        self.assertEqual(strong.files[0].inherent_risk, 0.30)
        self.assertEqual(weak.files[0].confidence, 0.75)
        self.assertEqual(strong.files[0].confidence, 0.95)
        self.assertEqual(strong.focus, [])

    def test_no_record_is_unreviewed_even_without_risk_signals(self):
        result = brm.score(scan(triage(TEST, "LOW")), review_map(), None, brm.THRESHOLD)
        self.assertEqual(result.files[0].confidence, 0)
        self.assertEqual(result.files[0].review_status, "unreviewed")
        self.assertEqual([f.path for f in result.focus], [TEST])
        self.assertIn("review record", result.files[0].gaps[0])

    def test_read_and_context_are_required_for_completed_review(self):
        for missing in ("read", "context"):
            with self.subTest(missing=missing):
                entry = judged(DOMAIN)
                entry["verification"].pop(missing)
                result = self.score(entry)
                self.assertEqual(result.files[0].confidence, 0)
                self.assertEqual(result.files[0].review_status, "unreviewed")

    def test_completed_low_risk_review_is_not_perfect(self):
        result = brm.score(scan(triage(TEST, "LOW")), review_map(judged(TEST, checks=[])), None, brm.THRESHOLD)
        self.assertEqual(result.files[0].confidence, 0.95)
        self.assertFalse(result.focus)

    def test_behavior_check_is_required_for_production_files(self):
        result = self.score(judged(DOMAIN, checks=[]))
        self.assertEqual(result.files[0].confidence, 0.75)
        self.assertIn("Missing behavior check", result.files[0].gaps)

    def test_applicable_risks_require_specific_checks(self):
        cases = [("Security-sensitive", "boundary"), ("Public contract / event schema", "contract"),
                 ("Concurrency", "concurrency"), ("Persistence / transactions", "integration"),
                 ("Messaging / delivery semantics", "integration"), ("Production deployment / CI", "configuration"),
                 ("Build / dependencies", "configuration")]
        for reason, kind in cases:
            with self.subTest(reason=reason):
                weak = self.score(judged(DOMAIN), [reason])
                strong = self.score(judged(DOMAIN, checks=[check(), check(kind)]), [reason])
                self.assertEqual(weak.files[0].confidence, 0.75)
                self.assertEqual(strong.files[0].confidence, 0.95)

    def test_skipped_failed_and_not_applicable_checks_cannot_hide_risk(self):
        for status, expected in [("skipped", 0.65), ("failed", 0.50), ("not-applicable", 0.75)]:
            with self.subTest(status=status):
                result = self.score(judged(DOMAIN, checks=[check(), check("integration", status)]),
                                    ["Messaging / delivery semantics"])
                self.assertEqual(result.files[0].confidence, expected)
                self.assertTrue(result.files[0].gaps)

    def test_not_applicable_behavior_requires_reason_and_is_allowed_for_nonbehavior_changes(self):
        entry = judged(DOMAIN, checks=[check(status="not-applicable")])
        entry["verification"]["checks"][0]["evidence"] = "Only documentation changed; no executable behavior affected"
        self.assertEqual(self.score(entry).files[0].confidence, 0.95)
        self.assertEqual(self.score(entry, ["Domain rules (business logic)"]).files[0].confidence, 0.75)

    def test_passing_duplicate_does_not_erase_skipped_or_failed_check(self):
        for status in ("skipped", "failed"):
            result = self.score(judged(DOMAIN, checks=[check(status=status), check()]))
            self.assertLessEqual(result.files[0].confidence, 0.65)

    def test_check_for_different_revision_cannot_raise_confidence(self):
        entry = judged(DOMAIN)
        entry["verification"]["checks"][0]["revision"] = "b" * 40
        result = self.score(entry)
        self.assertEqual(result.files[0].confidence, 0.75)
        self.assertIn("different revision", " ".join(result.files[0].gaps))

    def test_scan_and_map_must_identify_same_revision(self):
        for revision in (None, "", "b" * 40):
            candidate = review_map(judged(DOMAIN))
            candidate["revision"] = revision
            with self.subTest(revision=revision), self.assertRaisesRegex(brm.InvalidInput, "revision"):
                brm.score(scan(triage(DOMAIN)), candidate, None, 0.75)
        with self.assertRaisesRegex(brm.InvalidInput, "rescan"):
            brm.score({"triage": []}, review_map(), None, 0.75)

    def test_unresolved_uncertainty_stays_in_focus_despite_passing_tests(self):
        for signal in ("unverified", "untested-change", "intent-unclear"):
            result = self.score(judged(DOMAIN, signals=[signal]))
            self.assertLessEqual(result.files[0].confidence, 0.75)
            self.assertIn(signal, " ".join(result.files[0].gaps))

    def test_summary_findings_lower_confidence_and_cannot_be_cleared(self):
        summary = {"findings": [{"rule": "J-BUG", "severity": "major", "path": DOMAIN, "disputed": True}]}
        result = self.score(judged(DOMAIN), summary=summary)
        self.assertLessEqual(result.files[0].confidence, 0.75)
        self.assertEqual(result.files[0].inherent_risk, 0)
        with self.assertRaisesRegex(brm.InvalidInput, "only scanner signals"):
            self.score(judged(DOMAIN, cleared=[dict(signal="open-major", reason="nope")]), summary=summary)

    def test_findings_apply_after_uncertainty_ceiling(self):
        result = self.score(judged(DOMAIN, signals=["unverified", "open-major"]))
        self.assertEqual(result.files[0].confidence, round(0.75 * 0.70, 2))

    def test_old_failed_check_cannot_become_less_severe_by_being_stale(self):
        entry = judged(DOMAIN, checks=[check(status="failed")])
        entry["verification"]["checks"][0]["revision"] = "b" * 40
        self.assertEqual(self.score(entry).files[0].confidence, 0.50)

    def test_false_positive_clearing_removes_risk_and_check_requirement(self):
        entry = judged(DOMAIN, cleared=[dict(signal="security", reason="Only a log message mentions secret")])
        result = self.score(entry, ["Security-sensitive"])
        self.assertEqual(result.files[0].inherent_risk, 0)
        self.assertEqual(result.files[0].confidence, 0.95)
        self.assertTrue(result.files[0].cleared)

    def test_risk_and_verify_required_for_reviewed_focus_file(self):
        entry = judged(DOMAIN, checks=[])
        entry.pop("risk")
        with self.assertRaisesRegex(brm.InvalidInput, "needs a risk and a verify question"):
            self.score(entry)

    def test_invalid_evidence_is_rejected(self):
        for bad in [None, [], "done", {"read": False}, {"checks": "passed"},
                    {"checks": ["passed"]}, {"checks": [dict(kind="behavior", status="passed", evidence="")]},
                    {"checks": [dict(kind="magic", status="passed", evidence="report", revision=REVISION)]},
                    {"checks": [dict(kind="behavior", status="green", evidence="report", revision=REVISION)]}]:
            with self.subTest(evidence=bad), self.assertRaises(brm.InvalidInput):
                self.score(judged(DOMAIN, verification=bad))

    def test_duplicate_and_malformed_entries_are_rejected(self):
        candidates = [review_map(judged(DOMAIN), judged(DOMAIN)), review_map(judged("unknown")),
                      review_map(judged(DOMAIN, signals=["vibes"])), review_map(judged(DOMAIN, lines=["40-72"])),
                      dict(schemaVersion=1, revision=REVISION, files=[]), dict(schemaVersion=2, revision=REVISION, files="bad")]
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(brm.InvalidInput):
                brm.score(scan(triage(DOMAIN)), candidate, None, 0.75)

    def test_large_change_affects_risk_not_verified_confidence(self):
        result = brm.score(scan(triage(DOMAIN, added=400)), review_map(judged(DOMAIN)), None, 0.75)
        self.assertEqual(result.files[0].signals, ["large-change"])
        self.assertEqual(result.files[0].inherent_risk, 0.06)
        self.assertEqual(result.files[0].confidence, 0.95)

    def test_focus_ranges_merge_and_reviewer_ranges_win(self):
        scanned = scan(triage(DOMAIN, focus=["L3", "L8-L9", "L40", "…"]))
        result = brm.score(scanned, review_map(judged(DOMAIN)), None, 0.75)
        self.assertEqual(result.files[0].lines, ["L3-L9", "L40"])
        result = brm.score(scanned, review_map(judged(DOMAIN, lines=["L100-L149"])), None, 0.75)
        self.assertEqual(result.files[0].minutes, 2)

    def test_threshold_boundary_is_inclusive_in_markdown_and_json(self):
        files = [brm.ScoredFile(str(v), "M", 1, 0, [], {}, [], "risk", "verify", confidence=v) for v in (0.74, 0.75, 0.76)]
        result = brm.ReviewMap(0.75, files)
        self.assertEqual([f.confidence for f in result.focus], [0.74, 0.75])
        data = json.loads(brm.render_json(result, brm.Links()))
        self.assertEqual([f["needsHuman"] for f in data["files"]], [True, True, False])
        rendered = brm.render_markdown(result, brm.Links())
        self.assertNotIn("**Should read**", rendered)
        self.assertIn("confidence above 0.75", rendered)
        self.assertEqual(len(brm.ReviewMap(0.80, files).focus), 3)


class RenderTest(unittest.TestCase):
    def setUp(self):
        scanned = scan(triage(DOMAIN, "HIGH", ["Security-sensitive"], focus=["L40-L72"]), triage(TEST, "LOW"))
        self.result = brm.score(scanned, review_map(judged(DOMAIN), judged(TEST, checks=[])), None, 0.75)

    def test_links_and_evidence_are_preserved(self):
        rendered = brm.render_markdown(self.result, brm.PrLinks("o/r", 412))
        digest = hashlib.sha256(DOMAIN.encode()).hexdigest()
        self.assertIn(f"[L40-L72](https://github.com/o/r/pull/412/files#diff-{digest}R40-R72)", rendered)
        self.assertIn("inherent risk **high (0.30)**", rendered)
        self.assertIn("Missing boundary check", rendered)
        self.assertIn("RoundingTest#negativeHalf", rendered)
        self.assertIn(REVISION, rendered)
        self.assertIn("Read the full file and diff", rendered)
        self.assertIn("Checked Rounding callers", rendered)
        self.assertIn("Human focus: 1 of 2 files", rendered)
        self.assertIn(f"[`{TEST}`]", rendered)
        self.assertIn(": 0.95 · inherent risk low", rendered)

    def test_json_exposes_risk_evidence_and_gaps_separately(self):
        data = json.loads(brm.render_json(self.result, brm.Links()))
        self.assertEqual(data["schemaVersion"], 2)
        self.assertEqual(data["revision"], REVISION)
        entry = data["files"][0]
        self.assertEqual(entry["inherentRisk"], 0.30)
        self.assertEqual(entry["confidence"], 0.75)
        self.assertEqual(entry["reviewStatus"], "reviewed")
        self.assertEqual(entry["verification"]["checks"][0]["status"], "passed")
        self.assertIn("Missing boundary check", entry["gaps"])

    def test_safe_files_keep_auditable_evidence_in_collapsed_section(self):
        entry = judged(DOMAIN, checks=[check(), check("boundary")])
        result = brm.score(scan(triage(DOMAIN, reasons=["Security-sensitive"])), review_map(entry), None, 0.75)
        rendered = brm.render_markdown(result, brm.FileLinks("../../"))
        self.assertNotIn("**Must read**", rendered)
        self.assertIn(f"[`{DOMAIN}`](../../{DOMAIN})", rendered)
        self.assertIn("RoundingTest#negativeHalf", rendered)
        self.assertIn("boundary: passed", rendered)

    def test_unreviewed_output_does_not_claim_confidence(self):
        result = brm.score(scan(triage(TEST, "LOW")), review_map(), None, 0.75)
        rendered = brm.render_markdown(result, brm.Links())
        self.assertIn("unreviewed", rendered)
        self.assertIn("Missing review record", rendered)
        self.assertNotIn("Safe to skip (", rendered)

    def test_deleted_files_and_range_limits(self):
        deleted = brm.ScoredFile(ADAPTER, "D", 0, 10, [], {}, [], "", "")
        self.assertIsNone(brm.FileLinks("../../").file(deleted))
        self.assertTrue(brm.PrLinks("o/r", 1).file(deleted).startswith("https://"))
        entry = brm.ScoredFile(ADAPTER, "M", 10, 0, [], {}, ["L1", "L20", "L40", "L60", "L80", "L100"], "r", "v")
        self.assertIn("(+2 more)", brm.item(1, entry, brm.Links())[0])

    def test_cli_uses_default_cutoff_and_rejects_legacy_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scan.json").write_text(json.dumps(scan(triage(DOMAIN, reasons=["Security-sensitive"]))))
            (root / "map.json").write_text(json.dumps(review_map(judged(DOMAIN))))
            args = [sys.executable, str(SCRIPTS / "build_review_map.py"), "--scan", str(root / "scan.json"),
                    "--map", str(root / "map.json"), "--links", "none", "--format", "json"]
            run = subprocess.run(args, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["focus"], 1)
            blob = args + ["--links", "blob", "--repo", "o/r", "--ref", "b" * 40]
            run = subprocess.run(blob, text=True, capture_output=True)
            self.assertEqual(run.returncode, 2)
            self.assertIn("scanned commit SHA", run.stderr)
            blob[-1] = REVISION
            run = subprocess.run(blob, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            (root / "map.json").write_text('{"schemaVersion":1,"files":[]}')
            run = subprocess.run(args, text=True, capture_output=True)
            self.assertEqual(run.returncode, 2)
            self.assertIn("schemaVersion", run.stderr)


if __name__ == "__main__":
    unittest.main()
