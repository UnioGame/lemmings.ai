from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))

from lemmings.budget import HARD_CONTEXT_CEILINGS, HARD_TOOL_CALL_CEILINGS, new_task_budget
from lemmings.contracts import (
    assess_repair_progress,
    validate_task,
    validate_profile,
    validate_review,
    wave_results_complete,
)
from lemmings.invocations import accept_result, extend_task_budget, record_invocation, record_route_failure


def defaults() -> dict:
    return json.loads((ROOT / "skills/lemmings/defaults.json").read_text(encoding="utf-8"))


def task() -> dict:
    return json.loads((ROOT / "skills/lemmings/templates/task.json").read_text(encoding="utf-8"))


class ProfileBudgetTests(unittest.TestCase):
    def test_defaults_are_configurable_but_absolute_ceilings_hold(self) -> None:
        profile = defaults()
        self.assertTrue(validate_profile(profile).ok, validate_profile(profile).as_dict())
        disabled = json.loads(json.dumps(profile))
        disabled["workspacePool"]["enabled"] = False
        self.assertTrue(validate_profile(disabled).ok, validate_profile(disabled).as_dict())

        for name, hard in HARD_CONTEXT_CEILINGS.items():
            broken = json.loads(json.dumps(profile))
            broken["contextPolicy"]["ceilings"][name] = hard + 1
            self.assertIn("profile.context_ceiling", {item.code for item in validate_profile(broken).findings})
        for role, hard in HARD_TOOL_CALL_CEILINGS.items():
            broken = json.loads(json.dumps(profile))
            broken["invocationBudgets"][role]["maxToolCalls"] = hard + 1
            self.assertIn("profile.invocation_ceiling", {item.code for item in validate_profile(broken).findings})
        broken = json.loads(json.dumps(profile))
        broken["orchestration"]["maxRepairs"] = 4
        self.assertIn("profile.orchestration", {item.code for item in validate_profile(broken).findings})

    def test_task_budget_rejects_boolean_limits(self) -> None:
        value = task()
        value["budget"] = new_task_budget(defaults())
        value["budget"]["policy"]["context"]["maxExpansions"] = {"initial": True, "ceiling": True}
        self.assertIn("budget.context", {item.code for item in validate_task(value, defaults()).findings})


class InvocationLedgerTests(unittest.TestCase):
    @staticmethod
    def repo_task(root: Path) -> tuple[Path, Path, dict]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Lemmings Tests"], cwd=repo, check=True)
        (repo / "owned.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "owned.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
        value = task()
        value["baseSha"] = base
        value["workingSet"] = [{"ref": "owned.txt", "purpose": "owned input"}]
        value["ownership"] = {"owned": ["owned.txt"], "shared": [], "forbidden": []}
        packet = repo / "task.json"
        packet.write_text(json.dumps(value), encoding="utf-8")
        return repo, packet, defaults()

    @staticmethod
    def result(invocation: dict, *, trusted: bool | None = None, calls: int = 0) -> dict:
        value = {
            "schemaVersion": 4,
            "invocationId": invocation["invocationId"],
            "attempt": invocation["attempt"],
            "status": "blocked",
            "changedPaths": [],
            "acceptanceEvidence": [],
            "validationEvidence": [],
            "findings": [],
            "blockers": ["bounded stop"],
            "remainingRisks": [],
        }
        if trusted is not None:
            value["usage"] = {"trusted": trusted, "toolCalls": calls}
        return value

    def test_first_invocation_freezes_custom_profile_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo, packet, profile = self.repo_task(Path(temp))
            profile["invocationBudgets"]["worker"].update(initialToolCalls=7, maxToolCalls=9)
            invocation = record_invocation(repo, packet, profile, "worker", 1, 0)
            stored = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(7, invocation["limits"]["maxToolCalls"])
            self.assertEqual({"initial": 7, "ceiling": 9}, stored["budget"]["policy"]["toolCalls"]["worker"])

    def test_route_failure_settles_reservation_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo, packet, profile = self.repo_task(Path(temp))
            profile["modelRoutes"] = {"native": {"worker": [
                {"providerId": "test", "modelId": "worker"}
            ]}}
            first = record_invocation(repo, packet, profile, "worker", 1, 0, freeze=True)
            failure = {
                "category": "transient_transport",
                "invocationId": first["invocationId"],
                "route": {"hostId": "native", "providerId": "wrong", "modelId": "worker"},
                "resumable": True,
            }
            with self.assertRaisesRegex(ValueError, "does not match"):
                record_route_failure(packet, failure_value=failure, expected_revision=1)
            failure["route"] = {"hostId": "native", "providerId": "test", "modelId": "worker"}
            settled = record_route_failure(
                packet, failure_value=failure, expected_revision=1,
                usage={"trusted": True, "toolCalls": 3},
            )
            self.assertEqual(3, settled["consumedToolCalls"])
            second = record_invocation(repo, packet, profile, "worker", 2, 2)
            self.assertEqual(21, second["limits"]["maxToolCalls"])
            stored = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(first["invocationId"], stored["execution"]["routeFailures"][0]["invocationId"])

    def test_budget_cannot_be_removed_or_replaced_after_first_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo, packet, profile = self.repo_task(Path(temp))
            first = record_invocation(repo, packet, profile, "worker", 1, 0)
            accept_result(repo, packet, profile, self.result(first, trusted=True, calls=5), 1)
            stored = json.loads(packet.read_text(encoding="utf-8"))
            frozen = stored["budget"]
            stored["budget"] = None
            packet.write_text(json.dumps(stored), encoding="utf-8")
            self.assertIn("budget.required", {item.code for item in validate_task(stored, profile).findings})
            with self.assertRaisesRegex(ValueError, "budget is missing"):
                record_invocation(repo, packet, profile, "worker", 2, 2)

            stored["budget"] = new_task_budget(profile)
            packet.write_text(json.dumps(stored), encoding="utf-8")
            self.assertIn("budget.ledger", {item.code for item in validate_task(stored, profile).findings})
            with self.assertRaisesRegex(ValueError, "grant|usage"):
                record_invocation(repo, packet, profile, "worker", 2, 2)

            stored["budget"] = frozen
            stored["budget"]["policy"]["toolCalls"]["worker"]["initial"] = 1
            self.assertIn("budget.policy_drift", {item.code for item in validate_task(stored, profile).findings})

    def test_trusted_usage_releases_remainder_and_ceiling_is_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo, packet, profile = self.repo_task(Path(temp))
            first = record_invocation(repo, packet, profile, "worker", 1, 0)
            self.assertEqual(24, first["limits"]["maxToolCalls"])
            accept_result(repo, packet, profile, self.result(first, trusted=True, calls=5), 1)
            extend_task_budget(
                packet,
                expected_revision=2,
                kind="toolCalls",
                role="worker",
                amount=12,
                unresolved_question="Which retry path still fails?",
                progress="The parser failure was fixed.",
            )
            second = record_invocation(repo, packet, profile, "worker", 2, 3)
            self.assertEqual(31, second["limits"]["maxToolCalls"])
            accept_result(repo, packet, profile, self.result(second, trusted=True, calls=31), 4)
            extend_task_budget(
                packet,
                expected_revision=5,
                kind="toolCalls",
                role="worker",
                amount=12,
                unresolved_question="Does the final integration path pass?",
                progress="The cause is narrowed to integration.",
            )
            third = record_invocation(repo, packet, profile, "worker", 3, 6)
            self.assertEqual(12, third["limits"]["maxToolCalls"])
            with self.assertRaisesRegex(ValueError, "ceiling"):
                extend_task_budget(
                    packet,
                    expected_revision=7,
                    kind="toolCalls",
                    role="worker",
                    amount=1,
                    unresolved_question="More?",
                    progress="Some progress.",
                )

    def test_untrusted_accounting_consumes_grant_and_retry_cannot_reset_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo, packet, profile = self.repo_task(Path(temp))
            first = record_invocation(repo, packet, profile, "worker", 1, 0)
            accept_result(repo, packet, profile, self.result(first), 1)
            stored = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(24, stored["budget"]["usage"]["toolCalls"]["worker"])
            self.assertEqual(["worker"], stored["budget"]["lockedRoles"])
            with self.assertRaisesRegex(ValueError, "untrusted"):
                extend_task_budget(
                    packet,
                    expected_revision=2,
                    kind="toolCalls",
                    role="worker",
                    amount=1,
                    unresolved_question="Which path remains?",
                    progress="The failure was narrowed.",
                )
            with self.assertRaisesRegex(ValueError, "exhausted"):
                record_invocation(repo, packet, profile, "worker", 2, 2)
            stored = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(24, stored["budget"]["usage"]["toolCalls"]["worker"])
            self.assertEqual(1, len(stored["execution"]["invocations"]))
            self.assertEqual("tool-call budget exhausted", stored["budget"]["stop"]["reason"])


class RepairAndWaveTests(unittest.TestCase):
    @staticmethod
    def review(value: dict, cycle: int, ids: list[str]) -> dict:
        return {
            "schemaVersion": 4,
            "revision": 0,
            "reviewId": f"R{cycle}",
            "subject": {"kind": "candidate", "taskId": value["taskId"], "baseSha": "base", "headSha": "head"},
            "status": "ChangesRequested",
            "hostId": "native",
            "reviewerModel": "reviewer",
            "cycle": cycle,
            "findings": [
                {"findingId": item, "priority": "P1", "origin": "implementation", "summary": item}
                for item in ids
            ],
            "validation": [],
        }

    def test_progress_controls_repair_and_four_candidate_checks_are_allowed(self) -> None:
        value = task()
        value.update(state="Candidate", previousState="Active", baseSha="base")
        value["commits"]["candidate"] = "head"
        value["models"]["actual"] = value["models"]["assigned"]
        value["execution"]["validationEvidence"] = ["focused tests"]
        second = self.review(value, 2, ["F2"])
        self.assertNotIn("review.replan", {item.code for item in validate_review(second, value).findings})
        self.assertEqual("repair", assess_repair_progress(value, second))

        value["execution"]["repairHistory"] = [{
            "cycle": 1,
            "reviewRef": "reviews/r1.json",
            "progress": "The failure was reproduced but not narrowed.",
            "resolvedFindingIds": [],
            "remainingFindingIds": ["F2"],
        }]
        value["budget"] = new_task_budget(defaults())
        self.assertIn("repair.usage", {item.code for item in validate_task(value, defaults()).findings})
        value["budget"]["usage"]["repairCycles"] = 1
        self.assertEqual("replan", assess_repair_progress(value, second))
        value["execution"]["repairHistory"][0]["resolvedFindingIds"] = ["F1"]
        self.assertEqual("repair", assess_repair_progress(value, second))
        value["execution"]["repairHistory"][0]["scopeChanged"] = True
        self.assertEqual("replan", assess_repair_progress(value, second))

        fourth = self.review(value, 4, ["F4"])
        self.assertEqual("replan", assess_repair_progress(value, fourth))
        self.assertIn("review.replan", {item.code for item in validate_review(fourth, value).findings})

    def test_wave_waits_for_slow_writer_before_acceptance_gate(self) -> None:
        tasks = []
        for name in ("fast", "slow"):
            value = task()
            value["taskId"] = name
            value["execution"]["invocations"] = [{"invocationId": name + "-inv", "role": "worker"}]
            tasks.append(value)
        tasks[0]["execution"]["agentResults"] = [{"invocationId": "fast-inv", "status": "succeeded"}]
        self.assertFalse(wave_results_complete(tasks, ["fast", "slow"]))
        tasks[1]["execution"]["agentResults"] = [{"invocationId": "slow-inv", "status": "succeeded"}]
        self.assertTrue(wave_results_complete(tasks, ["fast", "slow"]))


class SkillReuseTests(unittest.TestCase):
    def test_proposal_flow_checks_installed_then_official_and_requires_choice(self) -> None:
        reference = (ROOT / "skills/lemmings/references/skill-reuse.md").read_text(encoding="utf-8")
        skill = (ROOT / "skills/lemmings/SKILL.md").read_text(encoding="utf-8")
        self.assertLess(reference.index("Local project skills"), reference.index("Official skills"))
        self.assertIn("skill-creator", skill)
        self.assertIn("If search is unavailable", reference)


if __name__ == "__main__":
    unittest.main()
