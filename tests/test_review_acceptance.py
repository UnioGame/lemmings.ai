from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))

from lemmings.contracts import assess_repair_progress, validate_review
from lemmings.invocations import apply_review


class ReviewAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads((ROOT / "skills/lemmings/templates/task.json").read_text(encoding="utf-8"))
        self.task.update({"state": "Candidate", "baseSha": "base"})
        self.task["commits"]["candidate"] = "head"
        self.review = {
            "schemaVersion": 5, "revision": 0, "reviewId": "R1",
            "subject": {"kind": "candidate", "taskId": self.task["taskId"], "baseSha": "base", "headSha": "head"},
            "status": "Accepted", "hostId": "native", "reviewerModel": "reviewer", "cycle": 1,
            "findings": [], "validation": [],
        }

    @staticmethod
    def finding(finding_id="F1", priority="P3"):
        return {"findingId": finding_id, "priority": priority, "origin": "implementation", "summary": "Optional naming cleanup" if priority == "P3" else "Saved data is lost on retry"}

    def test_optional_followups_are_accepted_and_not_carried_into_repair(self):
        self.review["findings"] = [self.finding()]
        self.assertTrue(validate_review(self.review, self.task).ok)
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            task_path, review_path = repo / "task.json", repo / "review.json"
            task_path.write_text(json.dumps(self.task), encoding="utf-8")
            review_path.write_text(json.dumps(self.review), encoding="utf-8")
            result = apply_review(repo, task_path, review_path, expected_revision=0)
            self.assertEqual("Accepted", result["state"])
            saved = json.loads(task_path.read_text(encoding="utf-8"))
            self.assertEqual([], saved["execution"]["reviewChains"]["native::reviewer"]["findingIds"])
            self.assertEqual([], saved["execution"]["repairHistory"])
            self.assertEqual(self.review, json.loads(review_path.read_text(encoding="utf-8")))

    def test_optional_followups_cannot_request_changes(self):
        self.review.update(status="ChangesRequested", findings=[self.finding()])
        result = validate_review(self.review, self.task)
        self.assertIn("review.blocker_required", {item.code for item in result.findings})

    def test_blocking_defects_require_changes_at_every_blocking_priority(self):
        for priority in ("P0", "P1", "P2"):
            with self.subTest(priority=priority):
                self.review.update(status="Accepted", findings=[self.finding(priority=priority)])
                result = validate_review(self.review, self.task)
                self.assertIn("review.acceptance", {item.code for item in result.findings})
                self.review["status"] = "ChangesRequested"
                self.assertTrue(validate_review(self.review, self.task).ok)

    def test_failed_validation_cannot_be_accepted(self):
        self.review["validation"] = [{"command": "required check", "passed": False}]
        self.assertIn("review.acceptance", {item.code for item in validate_review(self.review, self.task).findings})

    def test_new_suggestion_does_not_hide_repeated_blocker(self):
        self.task["execution"]["repairHistory"] = [{"remainingFindingIds": ["F1"], "resolvedFindingIds": []}]
        self.review.update(status="ChangesRequested", cycle=2,
                           findings=[self.finding("F1", "P1"), self.finding("F2", "P3")])
        self.assertEqual("replan", assess_repair_progress(self.task, self.review))

    def test_carried_blocker_still_counts_without_repeating_its_text(self):
        self.task["execution"]["repairHistory"] = [{"remainingFindingIds": ["F1"], "resolvedFindingIds": []}]
        self.review.update(status="ChangesRequested", cycle=2, findings=[self.finding("F2", "P3")],
                           reviewSpec={"findingIds": ["F1"]}, findingDispositions={"F1": "remaining"})
        self.assertEqual("replan", assess_repair_progress(self.task, self.review))

    def test_acceptance_stops_before_repair_ceiling_despite_suggestions(self):
        self.task["execution"]["repairHistory"] = [{"remainingFindingIds": [], "resolvedFindingIds": ["F1"]}]
        self.review.update(cycle=2, findings=[self.finding("F2", "P3")])
        self.assertEqual("accept", assess_repair_progress(self.task, self.review))


if __name__ == "__main__":
    unittest.main()
