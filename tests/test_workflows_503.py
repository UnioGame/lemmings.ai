from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))

from lemmings.budget import new_task_budget, reserve_tool_calls, settle_tool_calls
from lemmings.contracts import read_object, validate_task_budget
from lemmings.invocations import _review_lane, record_invocation
from lemmings.task_workflow import TaskBriefError, prepare_task, submit_candidate


def init_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Lemmings Tests"], cwd=root, check=True)
    (root / "owned.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "owned.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def profile() -> dict:
    return json.loads((ROOT / "skills/lemmings/defaults.json").read_text(encoding="utf-8"))


def brief(*, accounting: str = "invocation-v1") -> dict:
    return {
        "schemaVersion": 1,
        "taskId": "T-503",
        "goal": "Change the owned file",
        "acceptance": ["owned.txt contains the candidate value"],
        "dependencies": [],
        "risks": ["content-regression"],
        "ownership": {"owned": ["owned.txt"], "shared": [], "forbidden": []},
        "workingSet": [{"ref": "owned.txt", "purpose": "implementation target"}],
        "validation": {
            "riskToTest": [{"risk": "content-regression", "test": "git diff --check"}],
            "commands": ["git diff --check"],
            "allowedOutputs": ["task.json"],
        },
        "managerDecision": {
            "requestedMode": "standard",
            "resolvedMode": "standard",
            "riskClass": "medium",
            "modeReasons": ["workerRequired", "candidateReview"],
            "workerRequired": True,
            "reviewRequired": True,
            "planReviewRequired": False,
            "reviewPolicy": "single",
            "workspace": {"policy": "current", "backend": "current", "reason": "one safe writer"},
            "roleAssignments": {
                "worker": {"hostId": "native", "providerId": "native", "modelId": "current-host/default"},
                "reviewer": {"hostId": "native", "providerId": "native", "modelId": "current-host/default"},
            },
            "accountingMode": accounting,
        },
    }


class TaskPrepareTests(unittest.TestCase):
    def test_prepare_preserves_semantics_hashes_context_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            base = init_repo(repo)
            task_path = repo / "task.json"
            result = prepare_task(repo, task_path, brief(), profile=profile())
            self.assertEqual({"ok", "taskId", "revision", "state", "path"}, set(result))
            task = read_object(task_path)
            self.assertEqual(base, task["baseSha"])
            self.assertEqual(brief()["acceptance"], task["acceptance"])
            self.assertEqual(brief()["validation"]["riskToTest"], task["validation"]["riskToTest"])
            self.assertEqual(64, len(task["workingSet"][0]["contentHash"]))
            self.assertEqual("invocation-v1", task["budget"]["policy"]["accountingMode"])
            before = task_path.read_bytes()
            with self.assertRaisesRegex(TaskBriefError, "will not be overwritten"):
                prepare_task(repo, task_path, brief(), profile=profile())
            self.assertEqual(before, task_path.read_bytes())

    def test_prepare_rejects_missing_manager_decisions_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            init_repo(repo)
            task_path = repo / "task.json"
            invalid = brief()
            del invalid["managerDecision"]
            with self.assertRaisesRegex(TaskBriefError, "managerDecision"):
                prepare_task(repo, task_path, invalid, profile=profile())
            self.assertFalse(task_path.exists())


class InvocationAccountingTests(unittest.TestCase):
    def test_invocation_mode_counts_creation_once_and_never_locks(self) -> None:
        budget = new_task_budget(profile(), "invocation-v1")
        for index in range(5):
            invocation_id = f"worker-{index}"
            self.assertGreater(reserve_tool_calls(budget, "worker", invocation_id), 0)
            self.assertEqual(0, settle_tool_calls(budget, invocation_id, None))
        self.assertEqual(5, budget["usage"]["invocations"]["worker"])
        self.assertEqual([], budget["lockedRoles"])
        self.assertEqual(0, reserve_tool_calls(budget, "worker", "worker-6"))
        self.assertTrue(validate_task_budget({"budget": budget}).ok)

    def test_legacy_host_budget_without_new_fields_remains_valid(self) -> None:
        budget = new_task_budget(profile(), "host-v1")
        budget["policy"].pop("accountingMode")
        budget["policy"].pop("invocationLimits")
        budget["usage"].pop("invocations")
        checked = validate_task_budget({"budget": budget})
        self.assertTrue(checked.ok, checked.findings)


class CandidateSubmitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        init_repo(self.repo)
        self.task_path = self.repo / "task.json"
        prepare_task(self.repo, self.task_path, brief(), profile=profile())
        self.invocation = record_invocation(self.repo, self.task_path, profile(), "worker", 1, 0, freeze=True)

    def commit(self, value: str) -> str:
        (self.repo / "owned.txt").write_text(value + "\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", value], cwd=self.repo, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()

    def report(self, **extra) -> dict:
        value = {
            "status": "succeeded",
            "acceptanceEvidence": ["owned.txt contains the candidate value"],
            "validationEvidence": [{"command": "git diff --check", "passed": True}],
            "findings": [], "blockers": [], "remainingRisks": [],
        }
        value.update(extra)
        return value

    def test_submit_derives_head_promotes_and_replays_without_mutation(self) -> None:
        head = self.commit("candidate")
        first = submit_candidate(self.repo, self.task_path, profile(), self.report(), invocation_id=self.invocation["invocationId"])
        self.assertTrue(first["ok"], first)
        task = read_object(self.task_path)
        self.assertEqual("Candidate", task["state"])
        self.assertEqual(head, task["commits"]["candidate"])
        self.assertEqual("current-host/default", task["models"]["actual"])
        snapshot = self.task_path.read_bytes()
        second = submit_candidate(self.repo, self.task_path, profile(), self.report(), invocation_id=self.invocation["invocationId"])
        self.assertTrue(second["reused"])
        self.assertEqual(snapshot, self.task_path.read_bytes())
        self.assertEqual(1, task["budget"]["usage"]["invocations"]["worker"])

    def test_submit_rejects_conflicting_head_and_missing_evidence_without_mutation(self) -> None:
        self.commit("candidate")
        snapshot = self.task_path.read_bytes()
        with self.assertRaisesRegex(TaskBriefError, "exact repository HEAD"):
            submit_candidate(self.repo, self.task_path, profile(), self.report(candidateHead="0" * 40), invocation_id=self.invocation["invocationId"])
        self.assertEqual(snapshot, self.task_path.read_bytes())
        missing = self.report()
        missing.pop("acceptanceEvidence")
        with self.assertRaisesRegex(TaskBriefError, "acceptanceEvidence"):
            submit_candidate(self.repo, self.task_path, profile(), missing, invocation_id=self.invocation["invocationId"])
        self.assertEqual(snapshot, self.task_path.read_bytes())

    def test_repair_appends_fix_and_keeps_active_repair_open(self) -> None:
        first_head = self.commit("candidate")
        self.assertTrue(submit_candidate(self.repo, self.task_path, profile(), self.report(), invocation_id=self.invocation["invocationId"])["ok"])
        task = read_object(self.task_path)
        task["previousState"] = "Candidate"
        task["state"] = "Repair"
        task["execution"]["activeRepair"] = {"cycle": 1, "status": "open"}
        task["revision"] += 1
        self.task_path.write_text(json.dumps(task), encoding="utf-8")
        repair = record_invocation(self.repo, self.task_path, profile(), "worker", 2, task["revision"],
                                   dispatch_kind="repair", repair_cycle=1, freeze=True)
        fix_head = self.commit("fix")
        result = submit_candidate(self.repo, self.task_path, profile(), self.report(), invocation_id=repair["invocationId"])
        self.assertTrue(result["ok"], result)
        repaired = read_object(self.task_path)
        self.assertEqual(first_head, repaired["commits"]["candidate"])
        self.assertEqual([fix_head], repaired["commits"]["fix"])
        self.assertEqual("open", repaired["execution"]["activeRepair"]["status"])

    def test_readiness_failure_is_stored_without_restarting_worker(self) -> None:
        self.commit("candidate")
        (self.repo / "unowned.tmp").write_text("dirty\n", encoding="utf-8")
        result = submit_candidate(self.repo, self.task_path, profile(), self.report(), invocation_id=self.invocation["invocationId"])
        self.assertFalse(result["ok"])
        self.assertEqual("readiness", result["kind"])
        task = read_object(self.task_path)
        self.assertEqual("failed", task["execution"]["candidateReadiness"]["status"])
        self.assertEqual(1, task["budget"]["usage"]["invocations"]["worker"])


class ReviewerIdentityTests(unittest.TestCase):
    def test_reviewer_assignment_is_authoritative_and_explicit_lane_only_asserts(self) -> None:
        task = {"roleAssignments": {"reviewer": {"hostId": "review-host", "providerId": "openai", "modelId": "review-model"}}}
        self.assertEqual("review-host::openai/review-model", _review_lane(task, {}))
        with self.assertRaisesRegex(ValueError, "must match"):
            _review_lane(task, {}, explicit="native::other")


if __name__ == "__main__":
    unittest.main()
