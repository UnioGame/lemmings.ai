from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))

from lemmings.budget import new_task_budget
from lemmings.contracts import plan_digest, review_digest, validate_review
from lemmings.invocations import accept_result, apply_review, record_invocation, start_repair
from lemmings.readiness import prepare_candidate, readiness_digest, result_digest, validate_candidate_readiness, validation_digest


def profile() -> dict:
    return json.loads((ROOT / "skills/lemmings/defaults.json").read_text(encoding="utf-8"))


def template() -> dict:
    return json.loads((ROOT / "skills/lemmings/templates/task.json").read_text(encoding="utf-8"))


def init_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Lemmings Tests"], cwd=root, check=True)
    (root / "owned.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "owned.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def candidate_readiness(task: dict, *, check: dict | None = None) -> dict:
    item = {"version": 1, "status": "passed", "candidateHead": task["commits"]["candidate"], "baseSha": task["baseSha"],
            "planDigest": plan_digest(task), "validationDigest": validation_digest(task), "workerInvocationId": None,
            "workerResultDigest": None, "checks": [check] if check else [], "debt": [], "cleanBefore": True, "cleanAfter": True}
    item["status"] = "passed" if not check or check.get("passed") is True else "failed"
    item["digest"] = readiness_digest(item)
    return item


class CandidateGateTests(unittest.TestCase):
    def test_prepare_runs_checks_and_reviewer_creation_requires_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            base = init_repo(repo)
            task = template()
            task.update({"baseSha": base, "state": "Ready", "workingSet": [{"ref": "owned.txt", "purpose": "input"}],
                         "ownership": {"owned": ["owned.txt"], "shared": [], "forbidden": []}, "validation": {"riskToTest": [], "commands": ["git diff --check"], "allowedOutputs": [], "debt": []}})
            packet = repo / "task.json"
            packet.write_text(json.dumps(task), encoding="utf-8")
            first = record_invocation(repo, packet, profile(), "worker", 1, 0)
            (repo / "owned.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            result = {"schemaVersion": 4, "invocationId": first["invocationId"], "attempt": 1, "status": "succeeded", "candidateHead": head,
                      "changedPaths": ["owned.txt"], "acceptanceEvidence": [], "validationEvidence": [], "findings": [], "blockers": [], "remainingRisks": [],
                      "usage": {"trusted": True, "toolCalls": 0}}
            accept_result(repo, packet, profile(), result, 1)
            task = json.loads(packet.read_text(encoding="utf-8"))
            task.update({"state": "Candidate", "previousState": "Active"})
            task["commits"]["candidate"] = head
            task["models"]["actual"] = task["models"]["assigned"]
            packet.write_text(json.dumps(task), encoding="utf-8")
            prepared = __import__("lemmings.readiness", fromlist=["prepare_candidate"]).prepare_candidate(repo, packet, expected_revision=2)
            self.assertTrue(prepared["ok"], prepared)
            current = json.loads(packet.read_text(encoding="utf-8"))
            reviewer = record_invocation(repo, packet, profile(), "reviewer", 1, current["revision"])
            self.assertEqual("host-v1", reviewer["usageAccounting"])

    def test_failed_check_cannot_be_masked_by_unavailable_debt(self) -> None:
        task = template()
        task.update({"state": "Candidate", "baseSha": "base"})
        task["commits"]["candidate"] = "head"
        task["validation"]["commands"] = ["check"]
        task["execution"]["candidateReadiness"] = candidate_readiness(task, check={"command": "check", "headSha": "head", "passed": False, "classification": "executed", "exitCode": 1})
        task["execution"]["candidateReadiness"]["debt"] = [{"command": "check", "candidateHead": "head", "classification": "unavailable", "reason": "offline", "owner": "qa", "futureGate": "CI"}]
        task["execution"]["candidateReadiness"]["digest"] = readiness_digest(task["execution"]["candidateReadiness"])
        result = validate_candidate_readiness(Path.cwd(), task)
        self.assertIn("candidate.readiness_failed", {item.code for item in result.findings})

    def test_exact_unavailable_debt_makes_prepare_pass_and_stale_debt_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            base = init_repo(repo)
            task = template()
            task.update({"baseSha": base, "state": "Ready", "workingSet": [{"ref": "owned.txt", "purpose": "input"}],
                         "ownership": {"owned": ["owned.txt"], "shared": [], "forbidden": []},
                         "validation": {"riskToTest": [], "commands": ["executor-check"], "allowedOutputs": [], "debt": []}})
            packet = repo / "task.json"; packet.write_text(json.dumps(task), encoding="utf-8")
            worker = record_invocation(repo, packet, profile(), "worker", 1, 0)
            (repo / "owned.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            accept_result(repo, packet, profile(), {"schemaVersion": 4, "invocationId": worker["invocationId"], "attempt": 1, "status": "succeeded", "candidateHead": head,
                "changedPaths": ["owned.txt"], "acceptanceEvidence": [], "validationEvidence": [], "findings": [], "blockers": [], "remainingRisks": [],
                "usage": {"trusted": True, "toolCalls": 0}}, 1)
            current = json.loads(packet.read_text(encoding="utf-8")); current.update({"state": "Candidate", "previousState": "Active"}); current["commits"]["candidate"] = head
            packet.write_text(json.dumps(current), encoding="utf-8")
            debt = {"command": "executor-check", "candidateHead": head, "classification": "unavailable", "reason": "executor offline", "owner": "qa", "futureGate": "ci"}
            import lemmings.readiness as readiness_module
            real_run = readiness_module.subprocess.run
            def unavailable(*args, **kwargs):
                command = args[0] if args else kwargs.get("args")
                if isinstance(command, str) or (isinstance(command, list) and command and command[0] != "git"):
                    raise OSError("executor unavailable")
                return real_run(*args, **kwargs)
            with patch.object(readiness_module.subprocess, "run", side_effect=unavailable):
                prepared = __import__("lemmings.readiness", fromlist=["prepare_candidate"]).prepare_candidate(repo, packet, expected_revision=2, debt=[debt])
            self.assertTrue(prepared["ok"], prepared)
            self.assertEqual("passed", prepared["candidateReadiness"]["status"])
            stale = json.loads(packet.read_text(encoding="utf-8"))["execution"]["candidateReadiness"]
            stale["debt"][0]["candidateHead"] = "other-head"
            stale["digest"] = readiness_digest(stale)
            self.assertFalse(validate_candidate_readiness(repo, {**current, "execution": {**current["execution"], "candidateReadiness": stale}}).ok)

    def test_model_usage_is_ignored_without_host_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            base = init_repo(repo)
            task = template(); task.update({"baseSha": base, "workingSet": [{"ref": "owned.txt", "purpose": "input"}], "ownership": {"owned": ["owned.txt"], "shared": [], "forbidden": []}})
            packet = repo / "task.json"; packet.write_text(json.dumps(task), encoding="utf-8")
            invocation = record_invocation(repo, packet, profile(), "worker", 1, 0)
            result = {"schemaVersion": 4, "invocationId": invocation["invocationId"], "attempt": 1, "status": "blocked", "changedPaths": [], "acceptanceEvidence": [], "validationEvidence": [], "findings": [], "blockers": ["stop"], "remainingRisks": [], "usage": {"trusted": True, "toolCalls": 0}}
            accept_result(repo, packet, profile(), result, 1)
            stored = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(24, stored["budget"]["usage"]["toolCalls"]["worker"])
            self.assertEqual(["worker"], stored["budget"]["lockedRoles"])

    def test_live_head_and_dirty_tree_block_reviewer_before_reservation(self) -> None:
        def ready_repo_task(repo: Path) -> tuple[dict, Path, str, str]:
            base = init_repo(repo)
            (repo / "owned.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "add", "owned.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            value = template(); value.update({"state": "Candidate", "baseSha": base, "workingSet": [{"ref": "owned.txt", "purpose": "input"}],
                "ownership": {"owned": ["owned.txt"], "shared": [], "forbidden": []}}); value["commits"]["candidate"] = head; value["budget"] = new_task_budget(profile())
            worker_invocation = {"invocationId": "worker-1", "role": "worker"}; worker_result = {"invocationId": "worker-1", "status": "succeeded", "candidateHead": head}
            value["execution"]["invocations"] = [worker_invocation]; value["execution"]["agentResults"] = [worker_result]
            value["execution"]["candidateReadiness"] = {"version": 1, "status": "passed", "candidateHead": head, "baseSha": base,
                "planDigest": plan_digest(value), "validationDigest": validation_digest(value), "workerInvocationId": "worker-1",
                "workerResultDigest": result_digest(worker_result), "checks": [], "debt": [], "cleanBefore": True, "cleanAfter": True}
            value["execution"]["candidateReadiness"]["digest"] = readiness_digest(value["execution"]["candidateReadiness"])
            packet = repo / "task.json"; packet.write_text(json.dumps(value), encoding="utf-8")
            return value, packet, base, head

        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); value, packet, base, head = ready_repo_task(repo)
            value["commits"]["candidate"] = base; value["execution"]["candidateReadiness"]["candidateHead"] = base
            value["execution"]["candidateReadiness"]["workerResultDigest"] = result_digest(value["execution"]["agentResults"][0])
            value["execution"]["candidateReadiness"]["digest"] = readiness_digest(value["execution"]["candidateReadiness"]); packet.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "git HEAD"):
                record_invocation(repo, packet, profile(), "reviewer", 1, 0, review_lane="native::reviewer")
            self.assertEqual([], json.loads(packet.read_text(encoding="utf-8"))["budget"]["reservations"])

        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); _, packet, _, _ = ready_repo_task(repo)
            (repo / "owned.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "clean tree"):
                record_invocation(repo, packet, profile(), "reviewer", 1, 0, review_lane="native::reviewer")
            self.assertEqual([], json.loads(packet.read_text(encoding="utf-8"))["budget"]["reservations"])

    def test_host_v1_review_spec_binding_and_legacy_review_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); base = init_repo(repo)
            (repo / "owned.txt").write_text("candidate\n", encoding="utf-8"); subprocess.run(["git", "add", "owned.txt"], cwd=repo, check=True); subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            task = template(); task.update({"state": "Candidate", "baseSha": base}); task["commits"]["candidate"] = head; task["budget"] = new_task_budget(profile())
            worker = {"invocationId": "worker-1", "role": "worker"}; result = {"invocationId": "worker-1", "status": "succeeded", "candidateHead": head}; task["execution"]["invocations"] = [worker]; task["execution"]["agentResults"] = [result]
            readiness = {"version": 1, "status": "passed", "candidateHead": head, "baseSha": base, "planDigest": plan_digest(task), "validationDigest": validation_digest(task), "workerInvocationId": "worker-1", "workerResultDigest": result_digest(result), "checks": [], "debt": [], "cleanBefore": True, "cleanAfter": True}; readiness["digest"] = readiness_digest(readiness); task["execution"]["candidateReadiness"] = readiness
            packet = repo / "task.json"; packet.write_text(json.dumps(task), encoding="utf-8")
            invocation = record_invocation(repo, packet, profile(), "reviewer", 1, 0, review_lane="native::reviewer")
            current = json.loads(packet.read_text(encoding="utf-8"))
            review = {"schemaVersion": 4, "revision": 0, "reviewId": "R1", "status": "Accepted", "hostId": "native", "reviewerModel": "reviewer", "cycle": 1,
                      "subject": {"kind": "candidate", "taskId": current["taskId"], "baseSha": base, "headSha": head}, "reviewSpec": dict(invocation["reviewSpec"]), "findings": [], "validation": []}
            self.assertTrue(validate_review(review, current, profile()).ok, validate_review(review, current, profile()).as_dict())
            spec_less = {key: value for key, value in review.items() if key != "reviewSpec"}
            self.assertIn("review.invocation_binding", {item.code for item in validate_review(spec_less, current, profile()).findings})
            missing = {**review, "reviewSpec": {key: value for key, value in review["reviewSpec"].items() if key != "invocationId"}}
            self.assertIn("review.invocation_binding", {item.code for item in validate_review(missing, current, profile()).findings})
            mutated = {**review, "reviewSpec": {**review["reviewSpec"], "invocationDigest": "mutated"}}
            self.assertIn("review.invocation_binding", {item.code for item in validate_review(mutated, current, profile()).findings})
            legacy = json.loads(json.dumps(current)); legacy["execution"]["invocations"] = []; self.assertTrue(validate_review({key: value for key, value in review.items() if key != "reviewSpec"}, legacy, profile()).ok)


class RepairAndReviewChainTests(unittest.TestCase):
    def test_repair_start_is_idempotent_and_delta_requires_dispositions(self) -> None:
        task = template(); task.update({"state": "Candidate", "previousState": "Active", "baseSha": "base"}); task["commits"]["candidate"] = "head"; task["budget"] = new_task_budget(profile())
        review = {"schemaVersion": 4, "revision": 0, "reviewId": "R1", "status": "ChangesRequested", "hostId": "native", "reviewerModel": "reviewer", "cycle": 1,
                  "subject": {"kind": "candidate", "taskId": task["taskId"], "baseSha": "base", "headSha": "head"}, "findings": [{"findingId": "F1", "priority": "P1", "origin": "implementation", "summary": "fix"}], "validation": []}
        with tempfile.TemporaryDirectory() as temp:
            packet = Path(temp) / "task.json"; packet.write_text(json.dumps(task), encoding="utf-8")
            review["_evidencePath"] = "r1.json"
            task["execution"]["reviewApplications"] = [{"reviewRef": "r1.json", "digest": review_digest(review), "status": "ChangesRequested"}]
            packet.write_text(json.dumps(task), encoding="utf-8")
            first = start_repair(packet, expected_revision=0, progress="narrowed parser cause", plan="fix parser", review=review, review_ref="r1.json")
            self.assertTrue(first["ok"])
            second = start_repair(packet, expected_revision=1, progress="narrowed parser cause", plan="fix parser", review=review, review_ref="r1.json")
            self.assertTrue(second["idempotent"])
        readiness = candidate_readiness(task); task["execution"]["candidateReadiness"] = readiness; task["models"]["actual"] = task["models"]["assigned"]
        previous = {**review, "status": "Accepted", "reviewSpec": {"mode": "full", "fullBaseSha": "base", "candidateHead": "head", "readinessDigest": readiness["digest"], "planDigest": plan_digest(task), "validationDigest": validation_digest(task)}}
        previous_for_delta = {**previous, "subject": {**previous["subject"], "headSha": "old"}, "findings": [{**previous["findings"][0], "priority": "P2"}]}
        delta = {"schemaVersion": 4, "revision": 0, "reviewId": "R2", "status": "Accepted", "hostId": "native", "reviewerModel": "reviewer", "cycle": 1,
                 "subject": {"kind": "candidate", "taskId": task["taskId"], "baseSha": "base", "headSha": "head"}, "reviewSpec": {"mode": "delta", "fullBaseSha": "base", "candidateHead": "head", "readinessDigest": readiness["digest"], "planDigest": plan_digest(task), "validationDigest": validation_digest(task), "previousReviewRef": "r1.json", "previousReviewDigest": __import__("lemmings.contracts", fromlist=["review_digest"]).review_digest(previous_for_delta), "previousHead": "old", "findingIds": ["F1"], "inspectionRange": {"from": "old", "to": "head"}}, "previousReview": previous_for_delta, "findingDispositions": {"F1": "resolved"}, "findings": [], "validation": []}
        self.assertTrue(validate_review(delta, task).ok, validate_review(delta, task).as_dict())
        missing_disposition = {**delta, "findingDispositions": {}}
        self.assertIn("review.delta_dispositions", {item.code for item in validate_review(missing_disposition, task).findings})

    def test_apply_review_closes_repair_and_rejects_forged_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); base = init_repo(repo)
            task = template(); task.update({"state": "Candidate", "baseSha": base}); task["commits"]["candidate"] = "head"; task["budget"] = new_task_budget(profile())
            packet = repo / "task.json"; packet.write_text(json.dumps(task), encoding="utf-8")
            review = {"schemaVersion": 4, "revision": 0, "reviewId": "R1", "status": "ChangesRequested", "hostId": "native", "reviewerModel": "reviewer", "cycle": 1,
                      "subject": {"kind": "candidate", "taskId": task["taskId"], "baseSha": base, "headSha": "head"},
                      "findings": [{"findingId": "F1", "priority": "P1", "origin": "implementation", "summary": "fix"}, {"findingId": "F2", "priority": "P1", "origin": "validation", "summary": "also fix"}], "validation": []}
            review_path = repo / "r1.json"; review_path.write_text(json.dumps(review), encoding="utf-8")
            applied = apply_review(repo, packet, review_path, expected_revision=0, profile=profile())
            self.assertEqual("Repair", applied["state"])
            first = start_repair(packet, expected_revision=1, progress="identified F1", plan="fix F1", review=review, review_ref="r1.json", target_finding_ids=["F1"])
            self.assertTrue(first["ok"])
            with self.assertRaisesRegex(ValueError, "subset of material"):
                start_repair(packet, expected_revision=2, progress="forged target", plan="different plan", review=review, review_ref="r1.json", target_finding_ids=["P3"])
            forged = dict(review); forged["findings"] = [{**review["findings"][0], "summary": "different"}]
            with self.assertRaisesRegex(ValueError, "persisted immutable"):
                start_repair(packet, expected_revision=2, progress="forged", plan="bad", review=forged, review_ref="r1.json")
            review2 = {**review, "reviewId": "R2", "findings": [{"findingId": "F2", "priority": "P1", "origin": "validation", "summary": "new"}], "findingDispositions": {"F1": "resolved", "F2": "remaining"}}
            review2_path = repo / "r2.json"; review2_path.write_text(json.dumps(review2), encoding="utf-8")
            renamed = {**review2, "reviewId": "R2-bad", "findingDispositions": {"renamed": "resolved"}}
            renamed_path = repo / "r2-bad.json"; renamed_path.write_text(json.dumps(renamed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "disposition"):
                apply_review(repo, packet, renamed_path, expected_revision=2, profile=profile())
            applied2 = apply_review(repo, packet, review2_path, expected_revision=2, profile=profile())
            self.assertEqual("Repair", applied2["state"])
            saved = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(["F1"], saved["execution"]["repairHistory"][0]["resolvedFindingIds"])
            self.assertEqual(["F2"], saved["execution"]["repairHistory"][0]["remainingFindingIds"])
            next_repair = start_repair(packet, expected_revision=3, progress="resolved F1", plan="fix F2",
                                       review=review2, review_ref="r2.json")
            self.assertTrue(next_repair["ok"])

    def test_repair_repeat_without_progress_requires_replan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); base = init_repo(repo)
            task = template(); task.update({"state": "Candidate", "baseSha": base}); task["commits"]["candidate"] = "head"; task["budget"] = new_task_budget(profile())
            packet = repo / "task.json"; packet.write_text(json.dumps(task), encoding="utf-8")
            review = {"schemaVersion": 4, "revision": 0, "reviewId": "R1", "status": "ChangesRequested", "hostId": "native", "reviewerModel": "reviewer", "cycle": 1,
                      "subject": {"kind": "candidate", "taskId": task["taskId"], "baseSha": base, "headSha": "head"},
                      "findings": [{"findingId": "F1", "priority": "P1", "origin": "implementation", "summary": "fix"}], "validation": []}
            path = repo / "r1.json"; path.write_text(json.dumps(review), encoding="utf-8")
            apply_review(repo, packet, path, expected_revision=0, profile=profile())
            first = start_repair(packet, expected_revision=1, progress="identified", plan="fix", review=review, review_ref="r1.json")
            self.assertTrue(first["ok"])
            same = {**review, "reviewId": "R2", "findingDispositions": {"F1": "remaining"}}; same_path = repo / "r2.json"; same_path.write_text(json.dumps(same), encoding="utf-8")
            applied = apply_review(repo, packet, same_path, expected_revision=2, profile=profile())
            self.assertEqual("Replan Required", applied["state"])

    def test_apply_review_requires_canonical_predecessor_and_cross_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); base = init_repo(repo)
            task = template(); task.update({"state": "Candidate", "baseSha": base, "reviewPolicy": "cross"}); task["commits"]["candidate"] = "head"
            packet = repo / "task.json"; packet.write_text(json.dumps(task), encoding="utf-8")
            predecessor = {"schemaVersion": 4, "revision": 0, "reviewId": "P", "status": "Accepted", "hostId": "native", "reviewerModel": "a",
                          "subject": {"kind": "candidate", "taskId": task["taskId"], "baseSha": base, "headSha": "old"}, "findings": [], "validation": []}
            predecessor_path = repo / "previous.json"; predecessor_path.write_text(json.dumps(predecessor), encoding="utf-8")
            delta = {"schemaVersion": 4, "revision": 0, "reviewId": "D", "status": "Accepted", "hostId": "native", "reviewerModel": "a", "cycle": 1,
                     "subject": {"kind": "candidate", "taskId": task["taskId"], "baseSha": base, "headSha": "head"},
                     "reviewSpec": {"mode": "delta", "fullBaseSha": base, "candidateHead": "head", "previousReviewRef": "previous.json", "previousReviewDigest": review_digest(predecessor), "previousHead": "old", "findingIds": ["N"], "inspectionRange": {"from": "old", "to": "head"}}, "findings": [], "validation": []}
            delta_path = repo / "delta.json"; delta_path.write_text(json.dumps(delta), encoding="utf-8")
            predecessor["reviewId"] = "mutated"; predecessor_path.write_text(json.dumps(predecessor), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "delta predecessor digest"):
                apply_review(repo, packet, delta_path, expected_revision=0, profile=profile())
            delta["reviewSpec"]["previousReviewRef"] = "../outside.json"; delta_path.write_text(json.dumps(delta), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "immutable predecessor"):
                apply_review(repo, packet, delta_path, expected_revision=0, profile=profile())
            first = {**delta, "reviewId": "A", "reviewSpec": {"mode": "full", "fullBaseSha": base, "candidateHead": "head", "reviewLane": "native::a"}, "reviewerModel": "a"}
            mismatched = {**first, "reviewId": "M", "reviewSpec": {**first["reviewSpec"], "reviewLane": "native::b"}}
            mismatched_path = repo / "mismatch.json"; mismatched_path.write_text(json.dumps(mismatched), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reviewLane must match"):
                apply_review(repo, packet, mismatched_path, expected_revision=0, profile=profile())
            first_path = repo / "a.json"; first_path.write_text(json.dumps(first), encoding="utf-8"); first_result = apply_review(repo, packet, first_path, expected_revision=0, profile=profile())
            self.assertEqual("Candidate", first_result["state"])
            second = {**first, "reviewId": "B", "reviewerModel": "b", "reviewSpec": {**first["reviewSpec"], "reviewLane": "native::b"}}
            second_path = repo / "b.json"; second_path.write_text(json.dumps(second), encoding="utf-8"); second_result = apply_review(repo, packet, second_path, expected_revision=1, profile=profile())
            self.assertEqual("Accepted", second_result["state"])

    def test_candidate_review_lanes_are_independent_but_same_lane_basis_is_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); base = init_repo(repo)
            (repo / "owned.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "add", "owned.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            task = template(); task.update({"state": "Candidate", "baseSha": base}); task["commits"]["candidate"] = head; task["budget"] = new_task_budget(profile()); task["budget"]["policy"]["toolCalls"]["reviewer"]["initial"] = 32
            worker_invocation = {"invocationId": "worker-1", "role": "worker"}
            worker_result = {"invocationId": "worker-1", "status": "succeeded", "candidateHead": head}
            task["execution"]["invocations"] = [worker_invocation]; task["execution"]["agentResults"] = [worker_result]
            task["execution"]["candidateReadiness"] = {"version": 1, "status": "passed", "candidateHead": head, "baseSha": base, "planDigest": plan_digest(task),
                "validationDigest": validation_digest(task), "workerInvocationId": "worker-1", "workerResultDigest": result_digest(worker_result), "checks": [], "debt": [], "cleanBefore": True, "cleanAfter": True}
            task["execution"]["candidateReadiness"]["digest"] = readiness_digest(task["execution"]["candidateReadiness"])
            packet = repo / "task.json"; packet.write_text(json.dumps(task), encoding="utf-8")
            first = record_invocation(repo, packet, profile(), "reviewer", 1, 0, review_lane="native::a")
            first_result = {"schemaVersion": 4, "invocationId": first["invocationId"], "attempt": 1, "status": "succeeded",
                            "changedPaths": [], "acceptanceEvidence": [], "validationEvidence": [], "findings": [],
                            "blockers": [], "remainingRisks": []}
            accept_result(repo, packet, profile(), first_result, 1, trusted_usage={
                "trusted": True, "source": "host-v1", "invocationId": first["invocationId"],
                "grant": first["limits"]["maxToolCalls"], "toolCalls": 0,
            })
            second = record_invocation(repo, packet, profile(), "reviewer", 1, 2, review_lane="native::b")
            self.assertEqual("full", first["reviewSpec"]["mode"]); self.assertEqual("full", second["reviewSpec"]["mode"])
            with self.assertRaisesRegex(ValueError, "already dispatched"):
                record_invocation(repo, packet, profile(), "reviewer", 1, 3, review_lane="native::a")

    def test_applied_review_builds_lane_delta_and_plan_change_forces_full(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); base = init_repo(repo)
            (repo / "owned.txt").write_text("candidate\n", encoding="utf-8"); subprocess.run(["git", "add", "owned.txt"], cwd=repo, check=True); subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
            old_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            task = template(); task.update({"state": "Candidate", "baseSha": base, "workingSet": [{"ref": "owned.txt", "purpose": "input"}], "ownership": {"owned": ["owned.txt"], "shared": [], "forbidden": []}, "validation": {"riskToTest": [], "commands": [], "allowedOutputs": ["r1.json"], "debt": []}}); task["commits"]["candidate"] = old_head; task["budget"] = new_task_budget(profile())
            packet = repo / "task.json"; packet.write_text(json.dumps(task), encoding="utf-8")
            review = {"schemaVersion": 4, "revision": 0, "reviewId": "R1", "status": "ChangesRequested", "hostId": "native", "reviewerModel": "reviewer", "cycle": 1,
                      "subject": {"kind": "candidate", "taskId": task["taskId"], "baseSha": base, "headSha": old_head}, "reviewSpec": {"mode": "full", "fullBaseSha": base, "candidateHead": old_head, "reviewLane": "native::reviewer", "reviewerHost": "native", "reviewerModel": "reviewer", "planDigest": plan_digest(task), "validationDigest": validation_digest(task)},
                      "findings": [{"findingId": "F1", "priority": "P1", "origin": "implementation", "summary": "fix"}], "validation": []}
            review_path = repo / "r1.json"; review_path.write_text(json.dumps(review), encoding="utf-8"); apply_review(repo, packet, review_path, expected_revision=0, profile=profile())
            current = json.loads(packet.read_text(encoding="utf-8")); (repo / "owned.txt").write_text("fixed\n", encoding="utf-8"); subprocess.run(["git", "add", "owned.txt"], cwd=repo, check=True); subprocess.run(["git", "commit", "-qm", "fix"], cwd=repo, check=True)
            new_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip(); current["state"] = "Candidate"; current["previousState"] = "Repair"; current["commits"]["candidate"] = new_head; current["revision"] = current["revision"]
            current["execution"]["invocations"].append({"invocationId": "worker-new", "role": "worker"}); current["execution"]["agentResults"].append({"invocationId": "worker-new", "status": "succeeded", "candidateHead": new_head}); packet.write_text(json.dumps(current), encoding="utf-8")
            prepared = prepare_candidate(repo, packet, expected_revision=current["revision"])
            self.assertTrue(prepared["ok"], prepared); saved = json.loads(packet.read_text(encoding="utf-8")); chain = saved["execution"]["reviewChains"]["native::reviewer"]["reviewSpec"]
            self.assertEqual("delta", chain["mode"]); self.assertEqual("r1.json", chain["previousReviewRef"]); self.assertEqual(old_head, chain["previousHead"]); self.assertEqual({"F1"}, set(chain["findingIds"]))
            saved["acceptance"].append("new contract"); (repo / "owned.txt").write_text("fixed again\n", encoding="utf-8"); subprocess.run(["git", "add", "owned.txt"], cwd=repo, check=True); subprocess.run(["git", "commit", "-qm", "contract"], cwd=repo, check=True)
            second_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip(); saved["commits"]["candidate"] = second_head; saved["revision"] += 1; saved["execution"]["invocations"].append({"invocationId": "worker-new-2", "role": "worker"}); saved["execution"]["agentResults"].append({"invocationId": "worker-new-2", "status": "succeeded", "candidateHead": second_head}); packet.write_text(json.dumps(saved), encoding="utf-8")
            prepared_again = prepare_candidate(repo, packet, expected_revision=saved["revision"]); self.assertTrue(prepared_again["ok"], prepared_again); final_spec = json.loads(packet.read_text(encoding="utf-8"))["execution"]["reviewChains"]["native::reviewer"]["reviewSpec"]
            self.assertEqual("full", final_spec["mode"]); self.assertIsNone(final_spec["previousReviewRef"])


if __name__ == "__main__":
    unittest.main()
