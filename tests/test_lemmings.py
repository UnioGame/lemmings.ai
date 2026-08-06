from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lemmings.contracts import (  # noqa: E402
    DEFAULT_MODELS, DEFAULT_WORKER_POLICY, check_repository, detect_mode, runtime_marker,
    validate_profile, validate_review, validate_task, validate_wave,
)
from lemmings.hooks import handle, host_output, hydrate, is_read_only_shell  # noqa: E402
from lemmings import workspace as workspace_module  # noqa: E402
from lemmings.cli import build_parser  # noqa: E402


def task(**changes):
    value = {
        "schemaVersion": 1,
        "taskId": "TASK-1",
        "goal": "change",
        "acceptance": ["tests pass"],
        "dependencies": [],
        "mode": "standard",
        "state": "Ready",
        "previousState": "Planned",
        "role": "worker",
        "ownership": {"owned": ["lemmings/**"], "shared": [], "forbidden": ["secrets/**"]},
        "models": {"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": None},
        "execution": {"handoff": None, "validationEvidence": []},
        "workspace": {"policy": "auto", "backend": "current", "path": None, "estimatedGiB": 0, "approval": "not-required", "reason": "single writer"},
        "commits": {"candidate": None, "fix": []},
        "validation": {"riskToTest": [], "debt": []},
        "reviewRef": None,
        "close": {"mergeCommit": None, "integrationValidationPassed": False},
    }
    value.update(changes)
    return value


def profile(mode="auto"):
    return {
        "schemaVersion": 1,
        "mode": mode,
        "models": dict(DEFAULT_MODELS),
        "workerPolicy": dict(DEFAULT_WORKER_POLICY),
        "fallback": {"allowed": []},
    }


def run_cli(*args: str, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, "-m", "lemmings", *args], cwd=cwd or ROOT,
        capture_output=True, text=True, check=False,
    )


class IdentityAndCliTests(unittest.TestCase):
    def test_only_lemmings_cli_surface_is_exposed(self):
        process = run_cli("--help")
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("lemmings", process.stdout)
        for command in ("check", "status", "workspace", "metrics"):
            self.assertIn(command, process.stdout)
        for removed in ("worktree", "scorecard", "models", "mode", "phase", "wave", "close"):
            self.assertNotIn("  " + removed, process.stdout)
        self.assertFalse((ROOT / "scripts" / "orchestration_cli.py").exists())
        parsed = build_parser().parse_args(["check", "--task", "a.json", "--task", "b.json", "--all"])
        self.assertEqual(["a.json", "b.json"], parsed.task)

    def test_package_and_plugin_identity(self):
        unity = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        plugin = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("unigame.ai.lemmings", unity["name"])
        self.assertEqual("Lemmings", unity["displayName"])
        self.assertEqual("https://github.com/UnioGame/unigame.ai.tools.git", unity["repository"]["url"])
        self.assertEqual("lemmings", plugin["name"])
        self.assertEqual("https://github.com/UnioGame/unigame.ai.tools", plugin["repository"])

    def test_orchestrator_lifecycle_is_consistent(self):
        lifecycle = "Discover → Plan → Refine → Implement → Verify"
        paths = [
            ROOT / "skills" / "lemmings" / "SKILL.md",
            ROOT / "README.md",
        ]
        for path in paths:
            self.assertIn(lifecycle, path.read_text(encoding="utf-8"), str(path))

    def test_consumer_specific_autoqa_assets_are_absent(self):
        root = ROOT / "assets" / "repo-integration" / "auto.qa"
        self.assertFalse(root.exists() and any(path.is_file() for path in root.rglob("*")))

    def test_runtime_marker_uses_git_common_lemmings_path(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            config = repo / ".codex" / "lemmings.json"
            config.parent.mkdir()
            config.write_text(json.dumps(profile()), encoding="utf-8")
            marker = runtime_marker(repo)
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"schemaVersion": 1, "enabled": True}), encoding="utf-8")
            self.assertTrue(marker.is_file())
            self.assertEqual("lemmings", marker.parent.name)
            status = run_cli("status", "--repo", str(repo))
            self.assertEqual(0, status.returncode, status.stdout + status.stderr)
            self.assertTrue(json.loads(status.stdout)["data"]["active"])
            self.assertNotEqual(0, run_cli("on", "--repo", str(repo)).returncode)

    def test_removed_mutation_commands_do_not_change_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            config = repo / ".codex" / "lemmings.json"
            config.parent.mkdir()
            configured = profile()
            configured["requestedModels"] = {"worker": "custom:medium"}
            config.write_text(json.dumps(configured), encoding="utf-8")

            before = config.read_text(encoding="utf-8")
            for command in (("mode", "standard"), ("models", "set", "worker=custom:high"), ("close",)):
                self.assertNotEqual(0, run_cli(*command, "--repo", str(repo)).returncode)
            self.assertEqual(before, config.read_text(encoding="utf-8"))

    def test_benchmark_is_part_of_metrics_report(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            process = run_cli("metrics", "report", "--benchmark", "--repo", str(repo))
            self.assertEqual(0, process.returncode)
            self.assertIn("benchmark", json.loads(process.stdout))

    def test_models_are_skill_owned_not_cli_mutations(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            config = repo / "profile.json"
            config.write_text(json.dumps(profile()), encoding="utf-8")
            before = config.read_text(encoding="utf-8")
            process = run_cli("models", "--repo", str(repo), "--profile", "profile.json", "set", "worker=custom:high")
            self.assertNotEqual(0, process.returncode)
            self.assertEqual(before, config.read_text(encoding="utf-8"))

    def test_workspace_estimate_and_inspect_are_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            before = set(repo.rglob("*"))
            estimate = run_cli("workspace", "estimate", "--repo", str(repo), "--backend", "code-worktree")
            self.assertEqual(0, estimate.returncode, estimate.stdout + estimate.stderr)
            self.assertEqual("code-worktree", json.loads(estimate.stdout)["backend"])
            inspect = run_cli("workspace", "inspect", "--repo", str(repo))
            self.assertEqual(0, inspect.returncode, inspect.stdout + inspect.stderr)
            self.assertEqual(before, set(repo.rglob("*")))

    def test_only_large_unity_clone_requires_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            (repo / "Assets").mkdir()
            (repo / "ProjectSettings").mkdir()
            with patch.object(workspace_module, "_tracked_size", return_value=11 * workspace_module.GIB), patch.object(workspace_module, "_submodule_paths", return_value=[]):
                automatic = workspace_module.estimate_workspace(repo, backend="auto")
                worktree = workspace_module.estimate_workspace(repo, backend="code-worktree")
                clone = workspace_module.estimate_workspace(repo, backend="unity-clone")
            self.assertEqual("code-worktree", automatic["backend"])
            self.assertFalse(automatic["approvalRequired"])
            self.assertFalse(worktree["approvalRequired"])
            self.assertTrue(clone["approvalRequired"])
            self.assertGreater(clone["estimatedGiB"], 10)

    def test_large_code_worktree_does_not_require_task_approval(self):
        value = task(workspace={"policy": "isolated", "backend": "code-worktree", "path": "C:/wt/one", "estimatedGiB": 12, "approval": "not-required", "reason": "Git worktree"})
        self.assertNotIn("workspace.approval", {item.code for item in validate_task(value, profile()).findings})

    def test_large_unity_clone_requires_task_approval(self):
        value = task(workspace={"policy": "isolated", "backend": "unity-clone", "path": "C:/clone/one", "estimatedGiB": 12, "approval": "not-required", "reason": "Full clone"})
        self.assertIn("workspace.approval", {item.code for item in validate_task(value, profile()).findings})

    def test_canonical_baseline_and_candidate_reviews(self):
        phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": "base", "integrationBranch": "codex/p1", "contractsFrozen": True, "contracts": [], "baselineReviewRef": "reviews/base.json", "taskDag": [], "leases": [], "close": {"mergeCommits": [], "phaseValidation": []}}
        baseline = {"schemaVersion": 1, "reviewId": "RB", "subject": {"kind": "baseline", "phaseId": "P1", "sha": "base"}, "reviewerModel": DEFAULT_MODELS["reviewer"], "status": "Accepted", "cycle": 1, "findings": [], "validation": []}
        self.assertTrue(validate_review(baseline, phase=phase).ok)
        value = task(state="Candidate", previousState="Active", baseSha="base", commits={"candidate": "head", "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": DEFAULT_MODELS["worker"]}, execution={"handoff": "done", "validationEvidence": ["tests"]})
        candidate = {"schemaVersion": 1, "reviewId": "RC", "subject": {"kind": "candidate", "taskId": "TASK-1", "baseSha": "base", "headSha": "head"}, "reviewerModel": DEFAULT_MODELS["reviewer"], "status": "Accepted", "cycle": 1, "findings": [], "validation": []}
        self.assertTrue(validate_review(candidate, value).ok)
        legacy = {"schemaVersion": 1, "taskId": "TASK-1", "base": "base", "head": "head", "status": "Accepted", "reviewerModel": DEFAULT_MODELS["reviewer"]}
        self.assertIn("review.subject", {item.code for item in validate_review(legacy, value).findings})

    def test_check_all_accepts_canonical_strict_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
            base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            (repo / "candidate.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "candidate.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "candidate"], check=True, capture_output=True)
            head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            reviews = repo / "reviews"; reviews.mkdir()
            baseline = {"schemaVersion": 1, "reviewId": "RB", "subject": {"kind": "baseline", "phaseId": "P1", "sha": base}, "reviewerModel": DEFAULT_MODELS["reviewer"], "status": "Accepted", "cycle": 1, "findings": [], "validation": []}
            candidate = {"schemaVersion": 1, "reviewId": "RC", "subject": {"kind": "candidate", "taskId": "TASK-1", "baseSha": base, "headSha": head}, "reviewerModel": DEFAULT_MODELS["reviewer"], "status": "Accepted", "cycle": 1, "findings": [], "validation": []}
            (reviews / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
            (reviews / "candidate.json").write_text(json.dumps(candidate), encoding="utf-8")
            phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": base, "integrationBranch": "codex/p1", "contractsFrozen": True, "contracts": [], "baselineReviewRef": "reviews/baseline.json", "taskDag": [{"taskId": "TASK-1", "dependencies": []}], "leases": [], "close": {"mergeCommits": [], "phaseValidation": []}}
            current = task(mode="strict", state="Accepted", previousState="Candidate", baseSha=base, ownership={"owned": ["candidate.txt"], "shared": [], "forbidden": []}, workspace={"policy": "isolated", "backend": "code-worktree", "path": str(repo), "estimatedGiB": 1, "approval": "not-required", "reason": "strict validation"}, models={"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": DEFAULT_MODELS["worker"]}, commits={"candidate": head, "fix": []}, execution={"handoff": "done", "validationEvidence": ["tests"]}, reviewRef="reviews/candidate.json")
            for name, value in (("profile.json", profile()), ("task.json", current), ("phase.json", phase)):
                (repo / name).write_text(json.dumps(value), encoding="utf-8")
            process = run_cli("check", "--repo", str(repo), "--profile", "profile.json", "--task", "task.json", "--phase", "phase.json", "--review", "reviews/candidate.json", "--all")
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            phase["taskDag"].append({"taskId": "TASK-2", "dependencies": []})
            (repo / "phase.json").write_text(json.dumps(phase), encoding="utf-8")
            missing = run_cli("check", "--repo", str(repo), "--profile", "profile.json", "--task", "task.json", "--phase", "phase.json", "--review", "reviews/candidate.json", "--all")
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("phase.task_artifact_missing", {item["code"] for item in json.loads(missing.stdout)["findings"]})

    def test_candidate_on_sibling_task_branch_need_not_be_current_head(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
            base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "-C", str(repo), "checkout", "-b", "task"], check=True, capture_output=True)
            (repo / "candidate.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "candidate.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "candidate"], check=True, capture_output=True)
            candidate = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "-C", str(repo), "checkout", "master"], check=True, capture_output=True)
            value = task(state="Candidate", previousState="Active", baseSha=base, commits={"candidate": candidate, "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": DEFAULT_MODELS["worker"]}, execution={"handoff": "done", "validationEvidence": ["test"]})
            result = check_repository(repo, profile("standard"), value)
            self.assertTrue(result.ok, result.as_dict())

    def test_idle_strict_profile_check_all_is_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            (repo / "profile.json").write_text(json.dumps(profile("strict")), encoding="utf-8")
            process = run_cli("check", "--repo", str(repo), "--profile", "profile.json", "--all")
            output = json.loads(process.stdout)
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            self.assertTrue(output["ok"])
            self.assertTrue(output["data"]["idle"])
            self.assertEqual("strict", output["data"]["mode"])

    def test_active_runtime_missing_task_is_error(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            config_path = repo / ".codex" / "lemmings.json"
            config_path.parent.mkdir()
            config_path.write_text(json.dumps(profile("strict")), encoding="utf-8")
            marker = runtime_marker(repo)
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"schemaVersion": 1, "enabled": True, "mode": "strict", "profilePath": ".codex/lemmings.json", "taskPath": "docs/tasks/missing.json"}), encoding="utf-8")
            process = run_cli("status", "--repo", str(repo))
            output = json.loads(process.stdout)
            self.assertNotEqual(0, process.returncode)
            self.assertIn("runtime.task_missing", {item["code"] for item in output["findings"]})

class ContractTests(unittest.TestCase):
    def test_mode_detection(self):
        self.assertEqual("simple", detect_mode(profile()))
        self.assertEqual("standard", detect_mode(profile(), task()))
        risky = task(risks=["unitySerializedAssets"])
        risky.pop("mode")
        self.assertEqual("strict", detect_mode(profile(), risky))

    def test_standard_does_not_require_phase_or_separate_handoff(self):
        result = check_repository(Path.cwd(), profile("standard"), task())
        self.assertTrue(result.ok, result.as_dict())

    def test_candidate_requires_commit_and_evidence_or_debt(self):
        value = task(state="Candidate", previousState="Active")
        result = validate_task(value, profile())
        codes = {finding.code for finding in result.findings}
        self.assertIn("commit.candidate", codes)
        self.assertIn("validation.evidence", codes)
        self.assertIn("model.actual_required", codes)
        self.assertIn("execution.handoff", codes)

    def test_user_pin_has_priority(self):
        value = task(models={"requested": "gpt-custom:high", "assigned": DEFAULT_MODELS["worker"], "actual": None})
        self.assertIn("model.pin", {f.code for f in validate_task(value, profile()).findings})

    def test_retired_complex_worker_role_is_rejected(self):
        value = task(role="complex-worker")
        self.assertIn("task.role", {item.code for item in validate_task(value, profile()).findings})

    def test_sol_medium_is_approved_high_risk_assignment_for_worker_role(self):
        value = task(models={"requested": None, "assigned": DEFAULT_WORKER_POLICY["highRiskModel"], "actual": None})
        self.assertTrue(validate_task(value, profile()).ok)

    def test_terra_max_is_approved_elevated_assignment_for_worker_role(self):
        value = task(models={"requested": None, "assigned": DEFAULT_WORKER_POLICY["elevatedModel"], "actual": None})
        self.assertTrue(validate_task(value, profile()).ok)

    def test_unapproved_unpinned_worker_assignment_is_rejected(self):
        value = task(models={"requested": None, "assigned": "unapproved:model", "actual": None})
        self.assertIn("model.default_assignment", {item.code for item in validate_task(value, profile()).findings})

    def test_worker_routing_defaults_are_fixed(self):
        for route in DEFAULT_WORKER_POLICY:
            with self.subTest(route=route):
                configured = profile()
                configured["workerPolicy"][route] = "gpt-5.6-luna:low"
                self.assertIn("model.worker_route_fixed", {item.code for item in validate_profile(configured).findings})

    def test_task_role_pin_overrides_repo_default_and_mismatch_fails(self):
        configured = profile()
        configured["models"]["worker"] = "global-default:medium"
        configured["requestedModels"] = {"worker": "repo-pin:high"}
        configured["taskModels"] = {"TASK-1": {"worker": "task-pin:high"}}
        pinned = task(role="worker", models={"requested": "task-pin:high", "assigned": "task-pin:high", "actual": None})
        self.assertTrue(validate_task(pinned, configured).ok)
        mismatch = task(role="worker", models={"requested": "repo-pin:high", "assigned": "repo-pin:high", "actual": None})
        codes = {f.code for f in validate_task(mismatch, configured).findings}
        self.assertIn("model.pin_requested", codes)
        self.assertIn("model.pin_assigned", codes)

    def test_repo_pin_overrides_global_default(self):
        configured = profile()
        configured["models"]["worker"] = "global-default:medium"
        configured["requestedModels"] = {"worker": "repo-pin:high"}
        pinned = task(role="worker", models={"requested": "repo-pin:high", "assigned": "repo-pin:high", "actual": None})
        self.assertTrue(validate_task(pinned, configured).ok)

    def test_orchestrator_pin_allows_only_explicit_high_or_higher_sol(self):
        allowed = task(role="orchestrator", models={"requested": "gpt-5.6-sol:xhigh", "assigned": "gpt-5.6-sol:xhigh", "actual": None})
        self.assertTrue(validate_task(allowed, profile()).ok)
        downgraded = task(role="orchestrator", models={"requested": "gpt-5.6-sol:medium", "assigned": "gpt-5.6-sol:medium", "actual": None})
        self.assertIn("model.pin_policy", {f.code for f in validate_task(downgraded, profile()).findings})

    def test_fallback_requires_reason(self):
        value = task(models={"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": "fallback"})
        configured = profile(); configured["fallback"] = {"allowed": ["fallback"]}
        self.assertIn("model.fallback_reason", {f.code for f in validate_task(value, configured).findings})

    def test_stale_review_is_rejected_and_accepted_not_integrated(self):
        value = task(
            state="Accepted", previousState="Candidate",
            models={"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": DEFAULT_MODELS["worker"]},
            commits={"candidate": "aaa", "fix": ["bbb"]},
            execution={"handoff": "done", "validationEvidence": [{"command": "test", "passed": True}]},
            reviewRef="reviews/TASK-1.json",
        )
        review = {"schemaVersion": 1, "reviewId": "R1", "subject": {"kind": "candidate", "taskId": "TASK-1", "baseSha": None, "headSha": "aaa"}, "reviewerModel": DEFAULT_MODELS["reviewer"], "status": "Accepted", "cycle": 1, "findings": [], "validation": []}
        result = validate_review(review, value)
        self.assertIn("review.stale", {f.code for f in result.findings})
        self.assertNotIn("integration.evidence", {f.code for f in result.findings})

    def test_second_failed_review_requires_replan(self):
        value = task(state="Candidate", previousState="Candidate", baseSha="base", commits={"candidate": "aaa", "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": DEFAULT_MODELS["worker"]}, execution={"handoff": "done", "validationEvidence": ["test"]})
        review = {"schemaVersion": 1, "reviewId": "R1", "subject": {"kind": "candidate", "taskId": "TASK-1", "baseSha": "base", "headSha": "aaa"}, "reviewerModel": DEFAULT_MODELS["reviewer"], "status": "ChangesRequested", "cycle": 2, "findings": [], "validation": []}
        self.assertIn("review.replan", {f.code for f in validate_review(review, value).findings})

    def test_strict_wave_requires_unique_worktrees_and_nonoverlap(self):
        phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": "abc", "integrationBranch": "main", "contractsFrozen": True, "baselineReviewRef": "missing.json", "taskDag": [], "leases": []}
        isolated = {"policy": "isolated", "backend": "code-worktree", "path": "C:/wt/one", "estimatedGiB": 1, "approval": "not-required", "reason": "parallel"}
        left = task(mode="strict", workspace=isolated)
        right = task(taskId="TASK-2", mode="strict", workspace=isolated)
        result = validate_wave(ROOT, [left, right], phase)
        codes = {f.code for f in result.findings}
        self.assertIn("worktree.duplicate", codes)
        self.assertIn("ownership.overlap", codes)

    def test_external_resource_requires_unique_lease(self):
        phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": "abc", "integrationBranch": "main", "contractsFrozen": True, "baselineReviewRef": "missing.json", "taskDag": [], "leases": []}
        value = task(mode="strict", workspace={"policy": "isolated", "backend": "code-worktree", "path": "C:/wt/one", "estimatedGiB": 1, "approval": "not-required", "reason": "external"}, risks=["externalResources"])
        self.assertIn("lease.required", {f.code for f in validate_wave(ROOT, [value], phase).findings})

    def test_blocked_declined_clone_is_valid_without_provisioned_path(self):
        phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": "abc", "integrationBranch": "main", "contractsFrozen": True, "baselineReviewRef": "missing.json", "taskDag": [{"taskId": "TASK-1", "dependencies": []}], "leases": []}
        value = task(mode="strict", state="Blocked", previousState="Ready", risks=["sharedContracts"], workspace={"policy": "isolated", "backend": "unity-clone", "path": None, "estimatedGiB": 12, "approval": "declined", "reason": "clone-dependent validation has no safe fallback"})
        codes = {item.code for item in validate_wave(ROOT, [value], phase).findings}
        self.assertNotIn("worktree.required", codes)
        self.assertNotIn("workspace.approval", codes)
        self.assertNotIn("workspace.declined", codes)
        self.assertNotIn("workspace.path", codes)

    def test_ready_strict_serial_writer_may_use_current_checkout(self):
        phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": "abc", "integrationBranch": "main", "contractsFrozen": True, "baselineReviewRef": "missing.json", "taskDag": [{"taskId": "TASK-1", "dependencies": []}], "leases": []}
        value = task(mode="strict", risks=["sharedContracts"], workspace={"policy": "current", "backend": "current", "path": None, "estimatedGiB": 0, "approval": "not-required", "reason": "safe serial writer"})
        codes = {item.code for item in validate_wave(ROOT, [value], phase).findings}
        self.assertNotIn("worktree.required", codes)
        output = handle({"event": "PreToolUse", "toolName": "Agent", "task": value, "profile": profile(), "toolInput": {"task_name": "lemmings_worker", "message": "Implement serially in current checkout.", "model": "gpt-5.6-luna", "reasoning_effort": "max"}})
        self.assertEqual("allow", output["decision"])

    def test_ready_strict_writer_requires_owned_paths(self):
        value = task(mode="strict", ownership={"owned": [], "shared": [], "forbidden": []})
        self.assertIn("ownership.required", {f.code for f in validate_task(value, profile()).findings})
        candidate = task(mode="strict", state="Candidate", previousState="Active", baseSha="base", ownership={"owned": [], "shared": [], "forbidden": []})
        self.assertIn("ownership.required", {f.code for f in validate_task(candidate, profile()).findings})

    def test_review_base_and_nonempty_range_are_required(self):
        value = task(state="Accepted", previousState="Candidate", baseSha="base", commits={"candidate": "base", "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": DEFAULT_MODELS["worker"]}, execution={"handoff": "done", "validationEvidence": ["test"]}, reviewRef="review.json")
        review = {"schemaVersion": 1, "reviewId": "R1", "subject": {"kind": "candidate", "taskId": "TASK-1", "baseSha": "other", "headSha": "base"}, "reviewerModel": DEFAULT_MODELS["reviewer"], "status": "Accepted", "cycle": 1, "findings": [], "validation": []}
        codes = {item.code for item in validate_review(review, value).findings}
        self.assertIn("review.base", codes)
        review["subject"]["baseSha"] = "base"
        self.assertIn("review.range", {item.code for item in validate_review(review, value).findings})


class HookTests(unittest.TestCase):
    def test_read_only_shell_is_allowed(self):
        self.assertTrue(is_read_only_shell("git status"))
        self.assertTrue(is_read_only_shell("rg TODO lemmings"))
        output = handle({"event": "PreToolUse", "toolName": "shell_command", "mode": "strict", "task": task(), "toolInput": {"command": "git diff --check"}})
        self.assertEqual("allow", output["decision"])

    def test_mutating_git_queries_are_not_read_only(self):
        self.assertFalse(is_read_only_shell("git branch -D stale"))
        self.assertFalse(is_read_only_shell("git remote add origin https://example.invalid/repo"))
        self.assertFalse(is_read_only_shell("git tag -d old"))
        self.assertTrue(is_read_only_shell("git branch --show-current"))
        self.assertTrue(is_read_only_shell("git remote -v"))
        self.assertTrue(is_read_only_shell("git tag --list release-*"))

    def test_common_powershell_read_pipeline_is_allowed(self):
        command = "Get-Content data.json | ConvertFrom-Json | Where-Object active | Sort-Object name | Group-Object active | Measure-Object | Format-Table"
        self.assertTrue(is_read_only_shell(command))
        self.assertFalse(is_read_only_shell("Get-Content data.json | ForEach-Object { Remove-Item $_ }"))
        self.assertFalse(is_read_only_shell("ForEach-Object { git reset --hard }"))
        self.assertFalse(is_read_only_shell("Get-Content $(Get-Location)/data.json"))
        self.assertFalse(is_read_only_shell('Get-Content `"dynamic-path`"'))

    def test_dynamic_powershell_evaluation_is_not_read_only(self):
        self.assertFalse(is_read_only_shell("Invoke-Expression 'Get-Content data.json'"))
        self.assertFalse(is_read_only_shell("[scriptblock]::Create('Get-Content data.json')"))
        self.assertFalse(is_read_only_shell("Get-Content (git reset --hard)"))
        self.assertFalse(is_read_only_shell("[System.Management.Automation.ScriptBlock]::Create('git reset --hard')"))
        self.assertFalse(is_read_only_shell('Get-Content "$(git reset --hard)"'))

    def test_quote_aware_powershell_literals_are_read_only(self):
        self.assertTrue(is_read_only_shell("Get-Content 'notes (final).md'"))
        self.assertTrue(is_read_only_shell("rg '(TODO|FIXME)' lemmings"))
        self.assertTrue(is_read_only_shell('Get-Content "notes (final).md"'))
        self.assertTrue(is_read_only_shell("Get-Content 'it''s (final).md'"))

    def test_powershell_quote_fragments_cannot_hide_options(self):
        self.assertFalse(is_read_only_shell("rg --pr'e' processor TODO .", dialect="windows"))
        self.assertFalse(is_read_only_shell('rg --pr"e" processor TODO .', dialect="windows"))
        self.assertTrue(is_read_only_shell("Get-Content 'fully quoted notes.md'", dialect="windows"))

    def test_powershell_variable_expansion_is_not_static(self):
        dynamic_commands = (
            "rg --pre$null processor TODO .",
            "rg --pre$null=processor TODO .",
            'rg "--pre$null=processor" TODO .',
            "Get-Content $env:TEMP",
            "Get-Content ${dynamicPath}",
        )
        for command in dynamic_commands:
            with self.subTest(command=command):
                self.assertFalse(is_read_only_shell(command, dialect="windows"))

        self.assertTrue(is_read_only_shell("Get-Content '$literal (final).txt'", dialect="windows"))

    def test_unclosed_powershell_quotes_are_unknown(self):
        self.assertFalse(is_read_only_shell("Get-Content 'notes (final).md"))
        self.assertFalse(is_read_only_shell('Get-Content "notes (final).md'))

    def test_unquoted_background_ampersand_is_not_read_only(self):
        self.assertFalse(is_read_only_shell("Get-Content package.json & git reset --hard"))
        self.assertTrue(is_read_only_shell("Get-Content 'research & notes.md'"))
        self.assertTrue(is_read_only_shell("Get-Content package.json && git status"))

    def test_unquoted_redirection_metacharacters_are_not_read_only(self):
        redirections = (
            "Get-Content package.json>out",
            "Get-Content package.json>>out",
            "Get-Content package.json 2>err",
            "Get-Content package.json *>all",
            "Get-Content package.json 2>&1",
            "Get-Content <in",
            "Get-Content <<<input",
        )
        for command in redirections:
            with self.subTest(command=command):
                self.assertFalse(is_read_only_shell(command))

        self.assertTrue(is_read_only_shell("Get-Content 'research <draft> notes.md'"))

    def test_non_git_allowlist_rejects_execution_and_destructive_options(self):
        rejected = (
            "find . -delete",
            "find . -exec git reset --hard",
            "Format-Volume",
            "format D:",
            "rg --pre processor TODO .",
            "rg --pre=processor TODO .",
            "rg --pre-glob '*.zip' TODO .",
            "rg --pre-glob=*.zip TODO .",
            "rg --hostname-bin=hostname-helper TODO .",
            "ForEach-Object -MemberName Delete",
        )
        for command in rejected:
            with self.subTest(command=command):
                self.assertFalse(is_read_only_shell(command))

        self.assertTrue(is_read_only_shell("rg TODO lemmings"))
        self.assertTrue(is_read_only_shell("Get-Content data.json | Format-Table"))

    def test_posix_tokenization_normalizes_fragmented_rg_options(self):
        bypasses = (
            r"rg --pre\=processor TODO .",
            r"rg --p\re processor TODO .",
            "rg --pr'e' processor TODO .",
        )
        for command in bypasses:
            with self.subTest(command=command):
                self.assertFalse(is_read_only_shell(command, dialect="posix"))

        self.assertTrue(is_read_only_shell("rg '(TODO|FIXME)$' lemmings", dialect="posix"))
        self.assertTrue(is_read_only_shell(r"rg --p\re TODO .", dialect="windows"))

    def test_posix_scanner_rejects_dynamic_or_control_syntax(self):
        rejected = (
            "rg $(generator) .",
            "rg `generator` .",
            "rg TODO >out",
            "rg TODO & git status",
            "rg 'unterminated",
            "| rg TODO .",
            "rg TODO . |",
            "rg TODO . || | head",
        )
        for command in rejected:
            with self.subTest(command=command):
                self.assertFalse(is_read_only_shell(command, dialect="posix"))

    def test_posix_control_operators_validate_every_nonempty_segment(self):
        self.assertTrue(is_read_only_shell("rg TODO lemmings | head", dialect="posix"))
        self.assertTrue(is_read_only_shell("rg TODO lemmings || head", dialect="posix"))
        self.assertTrue(is_read_only_shell("rg TODO lemmings && git status", dialect="posix"))
        self.assertFalse(is_read_only_shell("rg TODO lemmings | git reset --hard", dialect="posix"))
        self.assertFalse(is_read_only_shell("rg TODO lemmings && custom-tool run", dialect="posix"))

    def test_exact_ownership_glob_matches_only_expected_files(self):
        value = task(ownership={"owned": ["src/**/*.py"], "shared": [], "forbidden": []})
        allowed = handle({"event": "PreToolUse", "toolName": "apply_patch", "cwd": str(ROOT), "task": value, "changedPaths": ["src/deep/module.py"]})
        rejected = handle({"event": "PreToolUse", "toolName": "apply_patch", "cwd": str(ROOT), "task": value, "changedPaths": ["src/secret.json"]})
        self.assertEqual("allow", allowed["decision"])
        self.assertEqual("block", rejected["decision"])

    def test_unknown_shell_warns_standard_and_blocks_strict(self):
        payload = {"event": "PreToolUse", "toolName": "shell_command", "task": task(), "toolInput": {"command": "custom-tool run"}}
        warned = handle(payload)
        self.assertEqual("warn", warned["decision"])
        self.assertNotIn("permissionDecision", host_output(warned, "PreToolUse", {}).get("hookSpecificOutput", {}))
        payload["mode"] = "strict"
        self.assertEqual("block", handle(payload)["decision"])

    def test_spawn_blocks_profile_task_pin_mismatch(self):
        configured = profile()
        configured["taskModels"] = {"TASK-1": {"worker": "pinned:high"}}
        value = task(role="worker", models={"requested": None, "assigned": "other:medium", "actual": None})
        output = handle({"event": "PreToolUse", "toolName": "Agent", "task": value, "profile": configured})
        self.assertEqual("block", output["decision"])
        self.assertIn("effective pin", output["reason"])

    def test_writer_and_reviewer_dispatch_are_role_aware(self):
        worker = task(role="worker")
        configured = profile()
        writer = handle({"event": "PreToolUse", "toolName": "Agent", "task": worker, "profile": configured, "toolInput": {"task_name": "lemmings_worker", "message": "Implement the Ready task.", "model": "gpt-5.6-luna", "reasoning_effort": "max"}})
        self.assertEqual("allow", writer["decision"])
        candidate = task(state="Candidate", previousState="Active", commits={"candidate": "abc", "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": DEFAULT_MODELS["worker"]}, execution={"handoff": "done", "validationEvidence": ["test"]})
        reviewer = handle({"event": "PreToolUse", "toolName": "Agent", "task": candidate, "toolInput": {"task_name": "lemmings_reviewer", "message": "Review head abc.", "model": "gpt-5.6-sol", "reasoning_effort": "high", "head": "abc"}})
        self.assertEqual("allow", reviewer["decision"])
        wrong_model = handle({"event": "PreToolUse", "toolName": "Agent", "task": candidate, "toolInput": {"task_name": "lemmings_reviewer", "message": "Review head abc.", "model": "gpt-5.6-sol", "reasoning_effort": "medium", "head": "abc"}})
        self.assertEqual("block", wrong_model["decision"])
        wrong_state = handle({"event": "PreToolUse", "toolName": "Agent", "task": worker, "toolInput": {"task_name": "lemmings_reviewer", "message": "Review current candidate.", "model": "gpt-5.6-sol", "reasoning_effort": "high"}})
        self.assertEqual("block", wrong_state["decision"])

    def test_high_risk_worker_uses_sol_medium_without_a_separate_role(self):
        value = task(models={"requested": None, "assigned": DEFAULT_WORKER_POLICY["highRiskModel"], "actual": None})
        output = handle({
            "event": "PreToolUse",
            "toolName": "Agent",
            "task": value,
            "profile": profile(),
            "toolInput": {
                "task_name": "lemmings_worker",
                "message": "Implement the high-risk Ready task.",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
            },
        })
        self.assertEqual("allow", output["decision"])

    def test_elevated_worker_uses_terra_max_without_a_separate_role(self):
        value = task(models={"requested": None, "assigned": DEFAULT_WORKER_POLICY["elevatedModel"], "actual": None})
        output = handle({
            "event": "PreToolUse",
            "toolName": "Agent",
            "task": value,
            "profile": profile(),
            "toolInput": {
                "task_name": "lemmings_worker",
                "message": "Implement the elevated Ready task.",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "max",
            },
        })
        self.assertEqual("allow", output["decision"])

    def test_embedded_model_effort_cannot_hide_conflicting_spawn_effort(self):
        value = task(models={"requested": None, "assigned": DEFAULT_WORKER_POLICY["elevatedModel"], "actual": None})
        output = handle({
            "event": "PreToolUse",
            "toolName": "Agent",
            "task": value,
            "profile": profile(),
            "toolInput": {
                "task_name": "lemmings_worker",
                "message": "Implement.",
                "model": "gpt-5.6-terra:max",
                "reasoning_effort": "medium",
            },
        })
        self.assertEqual("block", output["decision"])

    def test_invalid_profile_worker_route_is_blocked_before_dispatch(self):
        configured = profile()
        configured["workerPolicy"]["elevatedModel"] = "rogue:model"
        value = task(models={"requested": None, "assigned": "rogue:model", "actual": None})
        output = handle({
            "event": "PreToolUse",
            "toolName": "Agent",
            "task": value,
            "profile": configured,
            "toolInput": {"task_name": "lemmings_worker", "model": "rogue", "reasoning_effort": "model"},
        })
        self.assertEqual("block", output["decision"])
        self.assertIn("workerPolicy.elevatedModel", output["reason"])

    def test_worker_spawn_without_explicit_model_is_blocked_for_all_routes(self):
        for assigned in (DEFAULT_MODELS["worker"], *DEFAULT_WORKER_POLICY.values()):
            with self.subTest(assigned=assigned):
                value = task(models={"requested": None, "assigned": assigned, "actual": None})
                output = handle({
                    "event": "PreToolUse",
                    "toolName": "Agent",
                    "task": value,
                    "profile": profile(),
                    "toolInput": {"task_name": "lemmings_worker", "message": "Implement."},
                })
                self.assertEqual("block", output["decision"])
                self.assertIn("must be explicit", output["reason"])

    def test_actual_shaped_summarizer_is_bounded_read_only_role(self):
        value = task(state="Active", previousState="Ready")
        output = handle({"event": "PreToolUse", "toolName": "Agent", "mode": "strict", "task": value, "toolInput": {"task_name": "lemmings_summarizer", "message": "Summarize supplied evidence.", "model": "gpt-5.6-terra", "reasoning_effort": "low"}})
        self.assertEqual("allow", output["decision"])

    def test_strict_spawn_requires_explicit_role(self):
        output = handle({"event": "PreToolUse", "toolName": "Agent", "mode": "strict", "task": task(), "toolInput": {"model": DEFAULT_MODELS["worker"]}})
        self.assertEqual("block", output["decision"])

    def test_strict_writer_spawn_and_patch_require_owned_paths(self):
        value = task(mode="strict", ownership={"owned": [], "shared": [], "forbidden": []})
        spawn = handle({"event": "PreToolUse", "toolName": "Agent", "mode": "strict", "task": value, "profile": profile(), "toolInput": {"task_name": "lemmings_worker", "message": "Implement.", "model": "gpt-5.6-luna", "reasoning_effort": "max"}})
        write = handle({"event": "PreToolUse", "toolName": "apply_patch", "mode": "strict", "task": value, "changedPaths": ["lemmings/core.py"]})
        self.assertEqual("block", spawn["decision"])
        self.assertEqual("block", write["decision"])

    def test_ownership_and_reviewer_are_blocked(self):
        outside = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": task(), "changedPaths": ["README.md"]})
        self.assertEqual("block", outside["decision"])
        review_task = task(role="reviewer")
        reviewer = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": review_task, "changedPaths": ["lemmings/core.py"]})
        self.assertEqual("block", reviewer["decision"])
        identified = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": task(), "task_name": "lemmings_reviewer", "changedPaths": ["lemmings/core.py"]})
        self.assertEqual("block", identified["decision"])

    def test_actual_read_only_roles_cannot_patch_or_mutate_shell(self):
        for role in ("reviewer", "explorer", "summarizer", "validator"):
            patch = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": task(), "task_name": f"lemmings_{role}", "changedPaths": ["lemmings/core.py"]})
            self.assertEqual("block", patch["decision"], role)
        for role in ("reviewer", "explorer", "summarizer"):
            shell = handle({"event": "PreToolUse", "toolName": "shell_command", "task": task(), "task_name": f"lemmings_{role}", "toolInput": {"command": "git reset --hard"}})
            self.assertEqual("block", shell["decision"], role)
        validator_task = task(role="validator")
        validator_task["validation"]["commands"] = ["python -m unittest"]
        allowed = handle({"event": "PreToolUse", "toolName": "shell_command", "task": validator_task, "task_name": "lemmings_validator", "toolInput": {"command": "python -m unittest"}})
        denied = handle({"event": "PreToolUse", "toolName": "shell_command", "task": validator_task, "task_name": "lemmings_validator", "toolInput": {"command": "python arbitrary.py"}})
        self.assertEqual("allow", allowed["decision"])
        self.assertEqual("block", denied["decision"])

    def test_absolute_owned_path_is_repo_relative_and_external_path_is_blocked(self):
        allowed = handle({"event": "PreToolUse", "toolName": "apply_patch", "cwd": str(ROOT), "task": task(), "changedPaths": [str(ROOT / "lemmings" / "core.py")]})
        self.assertEqual("allow", allowed["decision"])
        with tempfile.TemporaryDirectory() as temp:
            blocked = handle({"event": "PreToolUse", "toolName": "apply_patch", "cwd": str(ROOT), "task": task(), "changedPaths": [str(Path(temp) / "outside.py")]})
        self.assertEqual("block", blocked["decision"])
        self.assertIn("outside repository", blocked["reason"])

    def test_hydrate_resolves_runtime_artifacts_from_nested_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            nested = repo / "src" / "nested"
            nested.mkdir(parents=True)
            task_path = repo / "docs" / "tasks" / "TASK-1.json"
            task_path.parent.mkdir(parents=True)
            task_path.write_text(json.dumps(task()), encoding="utf-8")
            marker = runtime_marker(repo)
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"schemaVersion": 1, "taskPath": "docs/tasks/TASK-1.json"}), encoding="utf-8")
            payload = hydrate({"cwd": str(nested), "hook_event_name": "SubagentStart"})
            self.assertTrue(payload["_lemmingsActive"])
            self.assertEqual("TASK-1", payload["task"]["taskId"])
            self.assertEqual(str(repo.resolve()), payload["_repoRoot"])

    def test_stop_has_no_continuation_behavior(self):
        output = handle({"event": "Stop", "task": task(state="Accepted")})
        self.assertEqual("allow", output["decision"])
        self.assertEqual({}, host_output(output, "Stop", {}))

    def test_subagent_stop_requires_embedded_handoff(self):
        value = task(state="Candidate", commits={"candidate": "abc", "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["worker"], "actual": DEFAULT_MODELS["worker"]})
        output = handle({"event": "SubagentStop", "task": value})
        self.assertEqual("block", output["decision"])

    def test_subagent_stop_is_role_aware_for_read_only_roles(self):
        candidate = task(state="Candidate", previousState="Active", commits={"candidate": "abc", "fix": []})
        reviewer = handle({"event": "SubagentStop", "task": candidate, "task_name": "lemmings_reviewer", "reviewHead": "abc", "verdict": "Accepted"})
        validator = handle({"event": "SubagentStop", "task": candidate, "task_name": "lemmings_validator", "validationEvidence": ["tests pass"]})
        explorer = handle({"event": "SubagentStop", "task": candidate, "task_name": "lemmings_explorer", "boundedOutput": True})
        summarizer = handle({"event": "SubagentStop", "task": candidate, "task_name": "lemmings_summarizer", "output": "summary"})
        self.assertTrue(all(item["decision"] == "allow" for item in (reviewer, validator, explorer, summarizer)))

    def test_subagent_start_derives_bounded_nonempty_context(self):
        value = task(secretLogs=["must-not-leak"])
        value["execution"]["interfaces"] = ["lemmings/core.py"]
        result = handle({"event": "SubagentStart", "task": value, "profile": profile()})
        output = host_output(result, "SubagentStart", {"task": value})
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("TASK-1", context)
        self.assertIn("lemmings/core.py", context)
        self.assertNotIn("must-not-leak", context)


if __name__ == "__main__":
    unittest.main()
