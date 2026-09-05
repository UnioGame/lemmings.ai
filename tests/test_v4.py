from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))

from lemmings import cli
from lemmings.contracts import plan_digest, validate_batch, validate_profile, validate_repository_evidence, validate_task
from lemmings.invocations import accept_result, record_invocation, result_findings


def profile() -> dict:
    return json.loads((ROOT / "skills/lemmings/defaults.json").read_text(encoding="utf-8"))


def task(task_id: str = "T1") -> dict:
    value = json.loads((ROOT / "skills/lemmings/templates/task.json").read_text(encoding="utf-8"))
    value["taskId"] = task_id
    return value


def init_repo(path: Path) -> str:
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "tests@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Lemmings Tests"], check=True)
    (path / "owned.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "owned.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


class WaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = profile()
        self.profile["orchestration"]["maxConcurrentWriters"] = 4

    def wave(self, size: int) -> tuple[list[dict], dict]:
        tasks = []
        for index in range(size):
            current = task(f"T{index}")
            current["parallelReason"] = "independent_paths"
            current["ownership"] = {"owned": [f"src/{index}/**"], "shared": [], "forbidden": []}
            current["workspace"].update({"backend": "code-worktree", "workspaceId": f"W{index}"})
            tasks.append(current)
        return tasks, {"taskDag": [{"taskId": value["taskId"], "dependencies": []} for value in tasks]}

    def test_independent_waves_one_through_four_fit_confirmed_capacity(self) -> None:
        self.assertTrue(validate_profile(self.profile).ok)
        for size in range(1, 5):
            tasks, phase = self.wave(size)
            checked = validate_batch(Path.cwd(), tasks, phase, [value["taskId"] for value in tasks], self.profile, available_slots=size + 1)
            self.assertTrue(checked.ok, checked.as_dict())

    def test_conflicts_duplicates_dependencies_and_capacity_are_rejected(self) -> None:
        tasks, phase = self.wave(4)
        tasks[1]["ownership"]["owned"] = tasks[0]["ownership"]["owned"]
        self.assertIn("ownership.overlap", {item.code for item in validate_batch(Path.cwd(), tasks, phase, ["T0", "T1"], self.profile).findings})
        tasks[1]["ownership"]["owned"] = ["src/1/**"]
        tasks[1]["workspace"]["workspaceId"] = "W0"
        self.assertIn("worktree.duplicate", {item.code for item in validate_batch(Path.cwd(), tasks, phase, ["T0", "T1"], self.profile).findings})
        tasks[1]["workspace"]["workspaceId"] = "W1"
        tasks[1]["dependencies"] = ["T0"]
        phase["taskDag"][1]["dependencies"] = ["T0"]
        self.assertIn("batch.ineligible", {item.code for item in validate_batch(Path.cwd(), tasks, phase, ["T0", "T1"], self.profile).findings})
        tasks[1]["dependencies"] = []
        phase["taskDag"][1]["dependencies"] = []
        self.assertIn("batch.capacity", {item.code for item in validate_batch(Path.cwd(), tasks, phase, [value["taskId"] for value in tasks], self.profile, available_slots=4).findings})


class EvidenceTests(unittest.TestCase):
    def test_integration_requires_passing_evidence_for_exact_merge(self) -> None:
        value = task()
        value.update({"state": "Integrated", "previousState": "Accepted", "baseSha": "base"})
        value["models"]["actual"] = value["models"]["assigned"]
        value["commits"]["candidate"] = "candidate"
        value["execution"]["validationEvidence"] = ["candidate tests"]
        value["close"] = {
            "mergeCommit": "merge",
            "integrationEvidence": [{"headSha": "other", "command": "tests", "passed": True}],
            "workspaceDisposition": {"releaseAction": "current", "releaseReason": "primary checkout"},
        }
        self.assertIn("integration.evidence", {item.code for item in validate_task(value, profile()).findings})
        value["close"]["integrationEvidence"][0]["headSha"] = "merge"
        self.assertTrue(validate_task(value, profile()).ok, validate_task(value, profile()).as_dict())

    def test_plan_review_is_bound_to_decision_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            init_repo(repo)
            value = task()
            value.update({"planReviewRequired": True, "planReviewRef": "reviews/plan.json"})
            review = {
                "schemaVersion": 4, "revision": 0, "reviewId": "PLAN-1", "status": "Accepted",
                "hostId": "codex", "reviewerModel": "openai/gpt-5.6-sol:high", "cycle": 1,
                "subject": {"kind": "plan", "ownerKind": "task", "ownerId": value["taskId"], "planDigest": plan_digest(value)},
                "findings": [], "validation": [],
            }
            target = repo / value["planReviewRef"]
            target.parent.mkdir()
            target.write_text(json.dumps(review), encoding="utf-8")
            self.assertTrue(validate_repository_evidence(repo, value, None, None, profile()).ok)
            value["goal"] = "changed goal"
            self.assertIn("review.plan_stale", {item.code for item in validate_repository_evidence(repo, value, None, None, profile()).findings})

    def test_integration_cli_runs_on_merge_head_and_keeps_candidate_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = init_repo(repo)
            value = task()
            value.update({"state": "Accepted", "previousState": "Candidate", "baseSha": head})
            value["commits"]["candidate"] = head
            value["close"]["mergeCommit"] = head
            value["validation"]["commands"] = [f'"{sys.executable}" -c "raise SystemExit(1)"']
            packet = repo / "task.json"
            packet.write_text(json.dumps(value), encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(1, cli.main(["integration", "validate", "--repo", str(repo), "--task", str(packet), "--expected-revision", "0"]))
            failed = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual("Accepted", failed["state"])
            self.assertEqual(head, failed["commits"]["candidate"])
            self.assertFalse(failed["close"]["integrationEvidence"][0]["passed"])
            failed["validation"]["commands"] = ["git diff --check"]
            packet.write_text(json.dumps(failed), encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(0, cli.main(["integration", "validate", "--repo", str(repo), "--task", str(packet), "--expected-revision", "1"]))
            passed = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(head, passed["close"]["integrationEvidence"][0]["headSha"])
            self.assertTrue(passed["close"]["integrationEvidence"][0]["passed"])


class InvocationTests(unittest.TestCase):
    def test_dispatch_is_persisted_and_late_or_changed_results_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            base = init_repo(repo)
            value = task()
            value["baseSha"] = base
            value["workingSet"] = [{"ref": "owned.txt", "purpose": "owned input"}]
            value["ownership"] = {"owned": ["owned.txt"], "shared": [], "forbidden": []}
            packet = repo / "task.json"
            packet.write_text(json.dumps(value), encoding="utf-8")
            invocation = record_invocation(repo, packet, profile(), "worker", 1, 0)
            stored = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(invocation["invocationId"], stored["execution"]["invocations"][0]["invocationId"])

            (repo / "owned.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "commit", "-qam", "candidate"], check=True)
            head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
            result = {
                "schemaVersion": 4, "invocationId": invocation["invocationId"], "attempt": 1, "status": "succeeded",
                "candidateHead": head, "changedPaths": ["owned.txt"], "acceptanceEvidence": [], "validationEvidence": [],
                "findings": [], "blockers": [], "remainingRisks": [],
            }
            changed_profile = profile()
            changed_profile["mode"] = "strict"
            self.assertIn("result.profile", {item.code for item in result_findings(repo, stored, changed_profile, result).findings})
            accepted = accept_result(repo, packet, profile(), result, 1)
            self.assertEqual(2, accepted["revision"])
            with self.assertRaisesRegex(ValueError, "revision"):
                accept_result(repo, packet, profile(), result, 2)


if __name__ == "__main__":
    unittest.main()
