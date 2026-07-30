from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from orchestration_core import (  # noqa: E402
    build_dispatch,
    parse_markdown,
    paths_overlap,
    routing_scorecard,
    validate_cross_artifacts,
    validate_dispatch,
    validate_phase,
    validate_profile,
    validate_task,
    validate_task_set,
)


def profile(**updates):
    value = {
        "schemaVersion": 1,
        "taskAdapter": "generic-markdown-v1",
        "roadmap": "docs/tasks/ROADMAP.md",
        "worktreeRoot": "../worktrees",
        "phaseBranchPattern": "codex/phase-{phase}-{slug}",
        "taskBranchPattern": "codex/{taskId}-{slug}",
        "maxAgents": 3,
        "maxWriters": 2,
        "integrationStrategy": "no-ff",
        "reviewCycles": 2,
    }
    value.update(updates)
    return value


def phase(**updates):
    value = {
        "phaseId": "P1",
        "integrationBranch": "codex/phase-p1",
        "reviewedBaseSha": "abc123",
        "baselineAccepted": True,
        "contractsFrozen": True,
        "phaseValidation": ["python -m unittest"],
    }
    value.update(updates)
    return value


def task(task_id="T1", **updates):
    value = {
        "taskId": task_id,
        "phaseId": "P1",
        "waveId": "W1",
        "state": "Ready",
        "previousState": "Planned",
        "baselineSha": "abc123",
        "branch": f"codex/{task_id.lower()}",
        "worktree": f"/tmp/{task_id.lower()}",
        "preferredModel": "gpt-5.6-sol",
        "approvedFallback": "gpt-5.6-terra",
        "selectedModel": "gpt-5.6-sol",
        "ownedPaths": [f"src/{task_id}/**"],
        "sharedPaths": [],
        "forbiddenPaths": [],
        "dependencies": [],
        "integrationOrder": 1,
        "role": "worker",
    }
    value.update(updates)
    return value


class ProfilePhaseTests(unittest.TestCase):
    def test_valid_profile(self):
        self.assertTrue(validate_profile(profile()).ok)

    def test_profile_cannot_enable_blanket_legacy_compatibility(self):
        result = validate_profile(profile(legacyCompatibilityDefault=True))
        self.assertIn("profile.legacy_default", {item.code for item in result.findings})

    def test_phase_without_baseline_cannot_dispatch(self):
        invalid = phase(baselineAccepted=False)
        self.assertFalse(validate_phase(invalid).ok)
        manifest, result = build_dispatch(profile(), invalid, [task()], "W1")
        self.assertFalse(result.ok)
        self.assertEqual("P1", manifest["phaseId"])

    def test_unfrozen_contract_is_rejected(self):
        self.assertFalse(validate_phase(phase(contractsFrozen=False)).ok)


class TaskValidationTests(unittest.TestCase):
    def test_selected_actual_model_mismatch(self):
        result = validate_task(task(state="Candidate", candidateCommit="deadbeef", actualModel="other"), phase())
        self.assertIn("model.actual", [item.code for item in result.findings])

    def test_unavailable_preferred_uses_only_approved_fallback(self):
        good = task(
            preferredModel="sol",
            approvedFallback="terra",
            selectedModel="terra",
            availableModels=["terra"],
        )
        self.assertTrue(validate_task(good, phase()).ok)
        bad = task(
            preferredModel="sol",
            approvedFallback="terra",
            selectedModel="luna",
            availableModels=["terra", "luna"],
        )
        self.assertFalse(validate_task(bad, phase()).ok)

    def test_user_pin_has_priority(self):
        value = task(userPinnedModel="sol", selectedModel="terra")
        self.assertIn("model.pin", [item.code for item in validate_task(value, phase()).findings])

    def test_candidate_requires_commit(self):
        result = validate_task(task(state="Candidate"), phase())
        self.assertIn("candidate.commit", [item.code for item in result.findings])

    def test_second_failed_review_requires_replan(self):
        value = task(
            state="Changes Requested",
            reviewCycle=2,
            reviewVerdict="changes-requested",
        )
        self.assertIn("review.replan", [item.code for item in validate_task(value, phase()).findings])

    def test_accepted_is_not_integrated(self):
        value = task(state="Accepted", candidateCommit="deadbeef", integrated=True)
        self.assertIn("state.accepted", [item.code for item in validate_task(value, phase()).findings])

    def test_validation_debt_requires_owner_and_future_gate(self):
        value = task(validationDebt=[{"reason": "PostgreSQL unavailable", "blocking": True}])
        self.assertIn("validation_debt.owner", [item.code for item in validate_task(value, phase()).findings])

    def test_baseline_drift(self):
        self.assertIn(
            "baseline.drift",
            [item.code for item in validate_task(task(baselineSha="other"), phase()).findings],
        )

    def test_illegal_transition(self):
        value = task(previousState="Ready", state="Accepted", candidateCommit="deadbeef")
        self.assertIn("state.transition", [item.code for item in validate_task(value, phase()).findings])


class DispatchTests(unittest.TestCase):
    def test_root_glob_overlap_is_conservative(self):
        self.assertTrue(paths_overlap("**/*.md", "docs/**"))

    def test_dependency_cycle(self):
        first = task("T1", dependencies=["T2"])
        second = task("T2", dependencies=["T1"])
        result = validate_task_set([first, second])
        self.assertIn("dependency.cycle", [item.code for item in result.findings])

    def test_missing_dependency(self):
        result = validate_task_set([task(dependencies=["NOPE"])])
        self.assertIn("dependency.missing", [item.code for item in result.findings])

    def test_path_overlap(self):
        first = task("T1", ownedPaths=["src/shared"])
        second = task("T2", ownedPaths=["src/shared/file.py"])
        result = validate_task_set([first, second])
        self.assertIn("paths.overlap", [item.code for item in result.findings])
        self.assertTrue(paths_overlap("src/shared/**", "src/shared/a.py"))

    def test_dispatch_drift(self):
        original = task()
        manifest, plan_result = build_dispatch(profile(), phase(), [original], "W1")
        self.assertTrue(plan_result.ok)
        manifest["tasks"][0]["selectedModel"] = "gpt-5.6-terra"
        result = validate_dispatch(manifest, [original], phase())
        self.assertIn("dispatch.drift", [item.code for item in result.findings])

    def test_closed_resource_gate(self):
        original = task(resourceGates=[{"name": "device", "open": False}])
        manifest, _ = build_dispatch(profile(), phase(), [original], "W1")
        result = validate_dispatch(manifest, [original], phase())
        self.assertIn("dispatch.resource_gate", [item.code for item in result.findings])


class CrossArtifactTests(unittest.TestCase):
    def test_review_verdict_separator_normalization_and_real_mismatch(self):
        value = task(
            "ORCH-1",
            state="Changes Requested",
            previousState="Sol Review",
            candidateCommit="c1",
            actualModel="gpt-5.6-sol",
            reviewVerdict="changes-requested",
            validationEvidence={"command": "test", "exitCode": 0},
        )
        handoff = {
            "taskId": "ORCH-1",
            "actualModel": "gpt-5.6-sol",
            "candidateCommit": "c1",
            "validationEvidence": {"command": "test", "exitCode": 0},
        }
        equivalent = validate_cross_artifacts(
            [value],
            handoffs=[handoff],
            reviews=[
                {
                    "taskId": "ORCH-1",
                    "commitRange": "abc123..c1",
                    "verdict": "Changes Requested",
                }
            ],
        )
        self.assertNotIn("review.verdict_drift", {item.code for item in equivalent.findings})

        mismatch = validate_cross_artifacts(
            [value],
            handoffs=[handoff],
            reviews=[
                {
                    "taskId": "ORCH-1",
                    "commitRange": "abc123..c1",
                    "verdict": "Accepted",
                }
            ],
        )
        self.assertIn("review.verdict_drift", {item.code for item in mismatch.findings})

    def test_accepted_without_review_fails_strict_contract(self):
        value = task(
            state="Accepted",
            previousState="Sol Review",
            candidateCommit="c1",
            actualModel="gpt-5.6-sol",
            validationEvidence={"command": "test", "exitCode": 0},
        )
        result = validate_cross_artifacts(
            [value],
            handoffs=[
                {
                    "taskId": "T1",
                    "actualModel": "gpt-5.6-sol",
                    "candidateCommit": "c1",
                    "validationEvidence": {"command": "test", "exitCode": 0},
                }
            ],
        )
        self.assertIn("lifecycle.review", {item.code for item in result.findings})
        self.assertFalse(result.ok)

    def test_integrated_without_integration_evidence_fails(self):
        value = task(
            state="Integrated",
            previousState="Accepted",
            candidateCommit="c1",
            actualModel="gpt-5.6-sol",
            validationEvidence={"command": "test", "exitCode": 0},
        )
        result = validate_cross_artifacts(
            [value],
            handoffs=[
                {
                    "taskId": "T1",
                    "actualModel": "gpt-5.6-sol",
                    "candidateCommit": "c1",
                    "validationEvidence": {"command": "test", "exitCode": 0},
                }
            ],
            reviews=[{"taskId": "T1", "commitRange": "abc123..c1", "verdict": "Accepted"}],
        )
        self.assertIn("lifecycle.integration", {item.code for item in result.findings})
        self.assertFalse(result.ok)

    def test_legacy_autoqa_missing_artifact_is_warning(self):
        value = task(
            state="Accepted",
            previousState=None,
            candidateCommit="c1",
            actualModel="gpt-5.6-sol",
            legacyCompatibility=True,
        )
        result = validate_cross_artifacts([value], adapter="autoqa-markdown-v1")
        self.assertTrue(any(item.code.endswith(".legacy") for item in result.findings))
        self.assertFalse(any(item.severity == "error" for item in result.findings))

    def test_historical_ready_manifest_matches_advanced_live_task(self):
        live = task(
            state="Candidate",
            candidateCommit="c1",
            fixCommits=["f1"],
            actualModel="gpt-5.6-sol",
            reviewVerdict="approved",
        )
        snapshot = dict(live)
        snapshot["state"] = "Ready"
        for field in ("actualModel", "candidateCommit", "fixCommits", "reviewVerdict"):
            snapshot.pop(field)
        result = validate_cross_artifacts(
            [live],
            manifests=[{"tasks": [snapshot]}],
            handoffs=[
                {
                    "taskId": "T1",
                    "actualModel": "gpt-5.6-sol",
                    "candidateCommit": "c1",
                    "fixCommits": ["f1"],
                }
            ],
            reviews=[{"taskId": "T1", "commitRange": "abc123..f1", "verdict": "approved"}],
            roadmap_text="| Task |\n| T1 |",
        )
        artifact_codes = {
            item.code
            for item in result.findings
            if item.code.startswith(("manifest.", "handoff.", "review.", "roadmap."))
        }
        self.assertEqual(set(), artifact_codes)

    def test_cross_artifact_drift_is_reported(self):
        live = task(
            state="Candidate",
            candidateCommit="c1",
            actualModel="gpt-5.6-sol",
            reviewVerdict="approved",
        )
        snapshot = dict(live)
        snapshot.update({"state": "Ready", "selectedModel": "other"})
        result = validate_cross_artifacts(
            [live],
            manifests=[{"tasks": [snapshot]}],
            handoffs=[
                {
                    "taskId": "T1",
                    "actualModel": "other",
                    "candidateCommit": "different",
                }
            ],
            reviews=[
                {
                    "taskId": "T1",
                    "commitRange": "abc123..different",
                    "verdict": "changes-requested",
                }
            ],
            roadmap_text="No task rows",
        )
        codes = {item.code for item in result.findings}
        self.assertIn("manifest.drift", codes)
        self.assertIn("handoff.model_drift", codes)
        self.assertIn("handoff.commit_drift", codes)
        self.assertIn("review.commit_drift", codes)
        self.assertIn("review.verdict_drift", codes)
        self.assertIn("roadmap.missing_task", codes)

    def test_binding_existence_is_reflected(self):
        value = task(state="Accepted", candidateCommit="c1")
        result = validate_cross_artifacts([value])
        binding = result.data["bindings"][0]
        self.assertFalse(binding["worktreeExists"])
        self.assertIn("worktree.missing", {item.code for item in result.findings})


class AdapterScorecardTests(unittest.TestCase):
    def test_generic_and_autoqa_markdown(self):
        generic = parse_markdown(
            "# Task\n\n- Task ID: ORCH-1\n- Status: Ready\n- Preferred Model: sol\n",
            "generic-markdown-v1",
        )
        self.assertEqual("ORCH-1", generic["taskId"])
        autoqa = parse_markdown(
            "| Preferred Model | sol |\n| Selected Model | terra |\n",
            "autoqa-markdown-v1",
        )
        self.assertEqual("terra", autoqa["selectedModel"])

    def test_autoqa_task_packet_compatibility(self):
        value = parse_markdown(
            """# Task P0-17: Example

- Status: Ready
- Phase/Wave: P0/W2
- Preferred model: GPT-5.4 medium
- Approved fallback: GPT-5.6 Terra medium
- Selected runtime model: GPT-5.6 Terra medium
- Actual runtime model: GPT-5.6 Terra medium
- Review cycles: `0/2`
- Legacy compatibility: true
- Branch: `codex/p0-17-example`
- Worktree: `../worktrees/p0-17`
- Baseline commit: `abc123`

## Owned Paths

- `scripts/**`
""",
            "autoqa-markdown-v1",
        )
        self.assertEqual("P0-17", value["taskId"])
        self.assertEqual("P0", value["phaseId"])
        self.assertEqual("W2", value["waveId"])
        self.assertEqual("Ready", value["state"])
        self.assertEqual("abc123", value["baselineSha"])
        self.assertEqual(0, value["reviewCycle"])
        self.assertTrue(value["legacyCompatibility"])
        self.assertEqual(["scripts/**"], value["ownedPaths"])

    def test_generic_phase_table_compatibility(self):
        value = parse_markdown(
            """# Phase P1: baseline

| Field | Value |
| --- | --- |
| Integration branch | `codex/phase-p1` |
| Reviewed base SHA | `abc123` |
| Baseline accepted | true |
| Contracts frozen | true |
""",
            "generic-markdown-v1",
        )
        self.assertEqual("P1", value["phaseId"])
        self.assertEqual("codex/phase-p1", value["integrationBranch"])
        self.assertEqual("abc123", value["reviewedBaseSha"])
        self.assertTrue(value["baselineAccepted"])

    def test_generic_task_table_compatibility(self):
        value = parse_markdown(
            """# ORCH-17: Example

| Field | Value |
| --- | --- |
| Phase / wave / state | `P1 / W2 / Ready` |
| Previous state | `Planned` |
| State history | `["Planned", "Ready"]` |
| Base SHA / branch / absolute worktree | `abc / codex/orch-17 / D:/worktrees/orch-17` |
| Preferred / approved fallback / selected / actual | `sol / terra / sol / sol` |
""",
            "generic-markdown-v1",
        )
        self.assertEqual("ORCH-17", value["taskId"])
        self.assertEqual("P1", value["phaseId"])
        self.assertEqual("W2", value["waveId"])
        self.assertEqual("Planned", value["previousState"])
        self.assertEqual(["Planned", "Ready"], value["stateHistory"])
        self.assertEqual("abc", value["baselineSha"])
        self.assertEqual("codex/orch-17", value["branch"])
        self.assertEqual("D:/worktrees/orch-17", value["worktree"])
        self.assertEqual("sol", value["actualModel"])

    def test_autoqa_handoff_and_review_identity_fields(self):
        handoff = parse_markdown(
            """# Handoff: P0-17

- Candidate commit: `c1`
- Fix commits: `f1`
- Actual model/reasoning: `GPT-5.6 Sol / medium`
""",
            "autoqa-markdown-v1",
        )
        review = parse_markdown(
            """# Sol Review: P0-17

- Candidate range: `base..f1`
- Verdict: Accepted
""",
            "autoqa-markdown-v1",
        )
        self.assertEqual("P0-17", handoff["taskId"])
        self.assertEqual("GPT-5.6 Sol / medium", handoff["actualModel"])
        self.assertEqual("f1", handoff["fixCommits"])
        self.assertEqual("P0-17", review["taskId"])
        self.assertEqual("base..f1", review["commitRange"])

    def test_routing_scorecard(self):
        value = task(
            state="Integrated",
            actualModel="gpt-5.6-sol",
            candidateCommit="deadbeef",
            tokens=120,
            paidCost=0.25,
            findings={"P2": 1},
        )
        card = routing_scorecard([value])["models"]["gpt-5.6-sol"]
        self.assertEqual(1, card["acceptedOrIntegrated"])
        self.assertEqual(120, card["tokens"])
        self.assertEqual(1, card["findings"]["P2"])

    def test_routing_scorecard_keeps_unobserved_cost_metrics_null(self):
        card = routing_scorecard([task(actualModel="sol")])["models"]["sol"]
        self.assertIsNone(card["tokens"])
        self.assertIsNone(card["latencyMs"])
        self.assertIsNone(card["paidCost"])
        self.assertEqual(
            {"tokens": 0, "latencyMs": 0, "paidCost": 0},
            card["observationCounts"],
        )


if __name__ == "__main__":
    unittest.main()
