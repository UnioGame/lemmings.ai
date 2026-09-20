from __future__ import annotations

import json
import io
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_l501 import init_repo, profile, template
from lemmings.contracts import read_object
from lemmings.cli import main
from lemmings.invocations import accept_result, record_invocation, task_lock
from lemmings.review_workflow import ReviewWorkflowError, start_review, submit_review


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        base = init_repo(self.repo)
        (self.repo / "owned.txt").write_text("candidate\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", "candidate"], cwd=self.repo, check=True)
        self.head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        task = template()
        task.update(state="Candidate", baseSha=base, workingSet=[{"ref": "owned.txt", "purpose": "affected code"}])
        task["ownership"] = {"owned": ["owned.txt"], "shared": [], "forbidden": []}
        task["commits"]["candidate"] = self.head
        task["validation"].update(commands=["git diff --check"], allowedOutputs=["evidence"])
        task["execution"]["invocations"] = [{"role": "worker", "invocationId": "worker"}]
        task["execution"]["agentResults"] = [{"invocationId": "worker", "status": "succeeded", "candidateHead": self.head, "changedPaths": ["owned.txt"]}]
        self.task_path = self.repo / "task.json"
        self.task_path.write_text(json.dumps(task), encoding="utf-8")
        self.review_path = self.repo / "evidence/review.json"
        self.profile = profile()

    def start(self):
        return start_review(self.repo, self.task_path, self.profile, expected_head=self.head, review_lane="native::native/reviewer")

    def report(self, invocation):
        return {"schemaVersion": 4, "invocationId": invocation["invocationId"], "attempt": invocation["attempt"],
                "status": "succeeded", "changedPaths": [], "acceptanceEvidence": ["acceptance checked"],
                "validationEvidence": [], "findings": [], "blockers": [], "remainingRisks": [],
                "verdict": "Accepted", "hostId": "native", "reviewerModel": "native/reviewer"}

    def submit(self, report, invocation):
        return submit_review(self.repo, self.task_path, self.profile, report, self.review_path,
                             host_receipt={"trusted": True, "source": "host-v1", "invocationId": invocation["invocationId"],
                                           "grant": invocation["limits"]["maxToolCalls"], "toolCalls": 2})

    def test_start_reuses_validation_and_dispatch_without_extra_budget(self):
        first = self.start()
        snapshot = self.task_path.read_bytes()
        again = self.start()
        self.assertTrue(again["reused"])
        self.assertEqual(first["invocationId"], again["invocationId"])
        self.assertEqual(snapshot, self.task_path.read_bytes())
        self.assertEqual(1, len(read_object(self.task_path)["budget"]["reservations"]))

    def test_submit_builds_binding_and_repeats_without_accounting_twice(self):
        invocation = self.start()["invocation"]
        report = self.report(invocation)
        self.assertEqual("Accepted", self.submit(report, invocation)["state"])
        review = read_object(self.review_path)
        self.assertEqual(invocation["reviewSpec"], review["reviewSpec"])
        self.assertEqual(self.head, review["subject"]["headSha"])
        snapshot = self.task_path.read_bytes()
        self.assertTrue(self.submit(report, invocation)["reused"])
        self.assertEqual(snapshot, self.task_path.read_bytes())
        task = read_object(self.task_path)
        self.assertEqual(1, len(task["execution"]["reviewApplications"]))
        self.assertEqual(2, task["budget"]["usage"]["toolCalls"]["reviewer"])

    def test_format_correction_keeps_same_invocation_and_all_evidence(self):
        invocation = self.start()["invocation"]
        report = self.report(invocation)
        report["findings"] = "wrong shape"
        snapshot = self.task_path.read_bytes()
        with self.assertRaises(ReviewWorkflowError) as caught:
            self.submit(report, invocation)
        self.assertEqual("artifact", caught.exception.kind)
        self.assertFalse(self.review_path.exists())
        self.assertEqual(snapshot, self.task_path.read_bytes())
        report["findings"] = []
        self.assertEqual("Accepted", self.submit(report, invocation)["state"])

    def test_interruption_after_result_accept_resumes_application(self):
        invocation = self.start()["invocation"]
        report = self.report(invocation)
        with patch("lemmings.review_workflow.apply_review", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.submit(report, invocation)
        self.assertTrue(self.review_path.is_file())
        self.assertEqual("Accepted", self.submit(report, invocation)["state"])
        results = read_object(self.task_path)["execution"]["agentResults"]
        self.assertEqual(1, sum(item["invocationId"] == invocation["invocationId"] for item in results))

    def test_dirty_code_does_not_get_accepted_by_rebuilding_metadata(self):
        invocation = self.start()["invocation"]
        (self.repo / "owned.txt").write_text("new unreviewed code", encoding="utf-8")
        snapshot = self.task_path.read_bytes()
        with self.assertRaises(ReviewWorkflowError) as caught:
            self.submit(self.report(invocation), invocation)
        self.assertEqual("evidence", caught.exception.kind)
        self.assertEqual(snapshot, self.task_path.read_bytes())

    def test_existing_review_is_never_overwritten(self):
        invocation = self.start()["invocation"]
        report = self.report(invocation)
        self.submit(report, invocation)
        snapshot = self.review_path.read_bytes()
        report["findings"] = [{"findingId": "F1", "priority": "P3", "origin": "implementation", "summary": "optional cleanup"}]
        with self.assertRaises(ReviewWorkflowError) as caught:
            self.submit(report, invocation)
        self.assertEqual("conflict", caught.exception.kind)
        self.assertEqual(snapshot, self.review_path.read_bytes())

    def test_failed_readiness_does_not_reserve_reviewer(self):
        task = read_object(self.task_path)
        task["validation"]["commands"] = ["git diff --not-a-real-option"]
        self.task_path.write_text(json.dumps(task), encoding="utf-8")
        result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual("validation", result["kind"])
        self.assertEqual([], read_object(self.task_path)["budget"]["reservations"])

    def test_task_lock_allows_composition_but_excludes_other_threads(self):
        def other_thread():
            with self.assertRaisesRegex(ValueError, "locked"):
                with task_lock(self.task_path):
                    pass
        with task_lock(self.task_path):
            with task_lock(self.task_path):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(other_thread).result()
        self.assertFalse(self.task_path.with_suffix(".json.lock").exists())

    def test_last_review_applies_replan_without_manual_state_change(self):
        task = read_object(self.task_path)
        task["execution"]["repairHistory"] = [
            {"cycle": cycle, "remainingFindingIds": ["F1"], "resolvedFindingIds": [], "narrowedCause": True}
            for cycle in range(1, 4)
        ]
        task["execution"]["activeRepair"] = {"cycle": 3, "status": "open", "targetFindingIds": ["F1"]}
        self.task_path.write_text(json.dumps(task), encoding="utf-8")
        invocation = self.start()["invocation"]
        report = self.report(invocation)
        report.update(verdict="ChangesRequested", findingDispositions={"F1": "remaining"},
                      findings=[{"findingId": "F1", "priority": "P1", "origin": "implementation", "summary": "Failure persists on supported input"}])
        self.assertEqual("Replan Required", self.submit(report, invocation)["state"])

    def test_same_invocation_cannot_be_applied_at_two_paths(self):
        invocation = self.start()["invocation"]
        report = self.report(invocation)
        self.submit(report, invocation)
        self.review_path = self.repo / "evidence/other-review.json"
        with self.assertRaises(ReviewWorkflowError) as caught:
            self.submit(report, invocation)
        self.assertEqual("conflict", caught.exception.kind)
        self.assertFalse(self.review_path.exists())

    def test_f02_shaped_finding_and_missing_transport_fields_are_normalized(self):
        invocation = self.start()["invocation"]
        report = self.report(invocation)
        for key in ("schemaVersion", "invocationId", "attempt", "changedPaths", "remainingRisks"):
            report.pop(key)
        report.update(verdict="ChangesRequested", blockers=["F02-R09"], findings=[{
            "id": "F02-R09", "priority": "P2", "path": "owned.txt", "lines": [180, 194],
            "summary": "A chapter rewardIds reference is absent from rewards.",
            "requiredCorrection": "Make every chapter rewardIds entry resolve within rewards.",
        }])
        result = submit_review(self.repo, self.task_path, self.profile, report, self.review_path, invocation_id=invocation["invocationId"])
        self.assertEqual("Repair", result["state"])
        finding = read_object(self.review_path)["findings"][0]
        self.assertEqual("F02-R09", finding["findingId"])
        self.assertEqual("implementation", finding["origin"])
        self.assertEqual(report["findings"][0]["summary"], finding["summary"])

    def test_compact_worker_report_uses_git_paths_and_replays_without_revision(self):
        task = read_object(self.task_path)
        task.update(state="Ready")
        task["commits"]["candidate"] = None
        task["execution"]["invocations"] = []
        task["execution"]["agentResults"] = []
        self.task_path.write_text(json.dumps(task), encoding="utf-8")
        invocation = record_invocation(self.repo, self.task_path, self.profile, "worker", 1, task["revision"])
        report = {"status": "succeeded", "candidateHead": self.head, "acceptanceEvidence": ["criterion met"], "validationEvidence": ["check passed"]}
        result = accept_result(self.repo, self.task_path, self.profile, report, invocation_id=invocation["invocationId"])
        self.assertTrue(result["ok"])
        recorded = read_object(self.task_path)["execution"]["agentResults"][0]
        self.assertEqual(["owned.txt"], recorded["changedPaths"])
        self.assertEqual(invocation["attempt"], recorded["attempt"])
        snapshot = self.task_path.read_bytes()
        self.assertTrue(accept_result(self.repo, self.task_path, self.profile, report, invocation_id=invocation["invocationId"])["reused"])
        self.assertEqual(snapshot, self.task_path.read_bytes())

    def test_missing_substantive_evidence_is_not_invented(self):
        invocation = self.start()["invocation"]
        report = self.report(invocation)
        report.pop("acceptanceEvidence")
        snapshot = self.task_path.read_bytes()
        with self.assertRaises(ReviewWorkflowError) as caught:
            self.submit(report, invocation)
        self.assertEqual("artifact", caught.exception.kind)
        self.assertIn("acceptanceEvidence", str(caught.exception))
        self.assertEqual(snapshot, self.task_path.read_bytes())

    def test_cli_start_and_compact_submission(self):
        artifacts = self.repo / "evidence"
        artifacts.mkdir()
        profile_path = artifacts / "profile.json"
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        common = ["--repo", str(self.repo), "--profile", str(profile_path), "--task", str(self.task_path)]
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["review", "start", *common, "--head", self.head, "--review-lane", "native::native/reviewer"])
        self.assertEqual(0, code, output.getvalue())
        invocation = json.loads(output.getvalue())["invocation"]
        report = self.report(invocation)
        report.pop("invocationId")
        report["findings"] = "invalid"
        report_path = artifacts / "report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        command = ["review", "submit", *common, "--result", str(report_path), "--review", str(self.review_path), "--invocation-id", invocation["invocationId"]]
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(command)
        self.assertEqual(1, code)
        self.assertEqual("artifact", json.loads(output.getvalue())["kind"])
        report["findings"] = []
        report_path.write_text(json.dumps(report), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(command)
        self.assertEqual(0, code, output.getvalue())
        self.assertEqual("Accepted", json.loads(output.getvalue())["state"])

    def test_conflicting_explicit_metadata_is_not_replaced(self):
        invocation = self.start()["invocation"]
        report = self.report(invocation)
        report["attempt"] += 1
        snapshot = self.task_path.read_bytes()
        with self.assertRaises(ReviewWorkflowError):
            self.submit(report, invocation)
        self.assertEqual(snapshot, self.task_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
