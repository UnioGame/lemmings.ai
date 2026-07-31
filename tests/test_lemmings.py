from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lemmings.core import (  # noqa: E402
    DEFAULT_MODELS, check_repository, detect_mode, runtime_marker, validate_task, validate_wave,
)
from lemmings.hooks import handle, host_output, hydrate, is_read_only_shell  # noqa: E402


def task(**changes):
    value = {
        "schemaVersion": 1,
        "taskId": "TASK-1",
        "mode": "standard",
        "state": "Ready",
        "previousState": "Planned",
        "role": "complex-worker",
        "plan": {"goal": "change", "acceptance": [], "dependencies": []},
        "ownership": {"owned": ["lemmings/**"], "shared": [], "forbidden": ["secrets/**"]},
        "models": {"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": None},
        "execution": {"handoff": None, "validationEvidence": []},
        "commits": {"candidate": None, "fix": []},
        "validation": {"riskToTest": [], "debt": []},
        "review": {"status": "Pending", "cycle": 0, "head": None, "evidence": None},
        "close": {"mergeCommit": None, "integrationValidationPassed": False},
    }
    value.update(changes)
    return value


def profile(mode="auto"):
    return {"schemaVersion": 1, "mode": mode, "models": dict(DEFAULT_MODELS), "fallback": {"allowed": []}}


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
        self.assertIn("worktree", process.stdout)
        self.assertIn("scorecard", process.stdout)
        self.assertFalse((ROOT / "scripts" / "orchestration_cli.py").exists())

    def test_package_and_plugin_identity(self):
        unity = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        plugin = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("com.unigame.lemmings", unity["name"])
        self.assertEqual("Lemmings", unity["displayName"])
        self.assertEqual("https://github.com/UnioGame/lemmings.git", unity["repository"]["url"])
        self.assertEqual("lemmings", plugin["name"])
        self.assertEqual("https://github.com/UnioGame/lemmings", plugin["repository"])

    def test_autoqa_marketplace_installs_single_lemmings_plugin_hook_source(self):
        integration = ROOT / "assets" / "repo-integration" / "auto.qa"
        marketplace = json.loads((integration / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual("autoqa", marketplace["name"])
        self.assertEqual(1, len(marketplace["plugins"]))
        entry = marketplace["plugins"][0]
        self.assertEqual("lemmings", entry["name"])
        self.assertEqual("./plugins/lemmings", entry["source"]["path"])
        self.assertEqual("INSTALLED_BY_DEFAULT", entry["policy"]["installation"])
        self.assertFalse((integration / ".codex" / "hooks.json").exists())

    def test_runtime_marker_uses_git_common_lemmings_path(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            config = repo / ".codex" / "lemmings.json"
            config.parent.mkdir()
            config.write_text(json.dumps(profile()), encoding="utf-8")
            on = run_cli("runtime", "--repo", str(repo), "on")
            self.assertEqual(0, on.returncode, on.stdout + on.stderr)
            marker = runtime_marker(repo)
            self.assertTrue(marker.is_file())
            self.assertEqual("lemmings", marker.parent.name)
            off = run_cli("runtime", "--repo", str(repo), "off")
            self.assertEqual(0, off.returncode)
            self.assertFalse(marker.exists())

    def test_scorecard_is_conditional(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            one = repo / "one.json"
            one.write_text('{"model":"sol"}', encoding="utf-8")
            process = run_cli("scorecard", "--repo", str(repo), "--observation", str(one))
            self.assertEqual(0, process.returncode)
            self.assertFalse(json.loads(process.stdout)["created"])

    def test_models_set_preserves_defaults_and_records_explicit_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            config = repo / "profile.json"
            config.write_text(json.dumps(profile()), encoding="utf-8")
            process = run_cli("models", "--repo", str(repo), "--profile", "profile.json", "set", "worker=custom:high")
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            saved = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(DEFAULT_MODELS, saved["models"])
            self.assertEqual("custom:high", saved["requestedModels"]["worker"])

    def test_check_all_validates_full_strict_lifecycle(self):
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
            candidate = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            current = task(
                state="Accepted", previousState="Candidate", worktree=str(repo), baseSha=base,
                ownership={"owned": ["candidate.txt"], "shared": [], "forbidden": []},
                models={"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": DEFAULT_MODELS["complex-worker"]},
                commits={"candidate": candidate, "fix": []},
                execution={"handoff": "done", "validationEvidence": ["python -m unittest"]},
                review={"taskId": "TASK-1", "base": base, "status": "Accepted", "cycle": 0, "head": candidate, "evidence": "review.json"},
            )
            phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": base, "integrationBranch": "main", "contractsFrozen": True, "baselineReview": {"status": "Accepted", "reviewerModel": DEFAULT_MODELS["reviewer"], "evidence": "baseline-review.json"}}
            review = {"schemaVersion": 1, "taskId": "TASK-1", "base": base, "head": candidate, "status": "Accepted", "reviewerModel": DEFAULT_MODELS["reviewer"]}
            for name, value in (("profile.json", profile()), ("task.json", current), ("phase.json", phase), ("review.json", review)):
                (repo / name).write_text(json.dumps(value), encoding="utf-8")
            (repo / "baseline-review.json").write_text(json.dumps({"schemaVersion": 1, "phaseId": "P1", "status": "Accepted", "reviewerModel": DEFAULT_MODELS["reviewer"], "baselineSha": base}), encoding="utf-8")
            process = run_cli("check", "--repo", str(repo), "--profile", "profile.json", "--task", "task.json", "--phase", "phase.json", "--review", "review.json", "--all")
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            self.assertTrue(json.loads(process.stdout)["ok"])
            absolute = run_cli("check", "--repo", str(repo), "--profile", "profile.json", "--task", "task.json", "--phase", "phase.json", "--review", str(repo / "review.json"), "--all")
            self.assertEqual(0, absolute.returncode, absolute.stdout + absolute.stderr)
            missing_task = json.loads(json.dumps(current))
            missing_task["review"]["evidence"] = "reviews/missing.json"
            missing_review = {**review, "_evidencePath": "review.json"}
            missing_result = check_repository(repo, profile(), missing_task, phase, missing_review, True)
            self.assertIn("review.evidence_missing", {item.code for item in missing_result.findings})
            missing_phase = json.loads(json.dumps(phase))
            missing_phase["baselineReview"]["evidence"] = "reviews/missing-baseline.json"
            phase_result = check_repository(repo, profile(), current, missing_phase, {**review, "_evidencePath": "review.json"}, True)
            self.assertIn("phase.baseline_evidence_path", {item.code for item in phase_result.findings})
            wrong_phase = json.loads(json.dumps(phase))
            wrong_phase["phaseId"] = "P2"
            binding_result = check_repository(repo, profile(), current, wrong_phase, {**review, "_evidencePath": "review.json"}, True)
            self.assertIn("phase.baseline_binding", {item.code for item in binding_result.findings})
            (repo / "baseline-review.json").write_text(json.dumps({"schemaVersion": 999, "phaseId": "P1", "status": "Accepted", "reviewerModel": DEFAULT_MODELS["reviewer"], "baselineSha": base}), encoding="utf-8")
            schema_result = check_repository(repo, profile(), current, phase, {**review, "_evidencePath": "review.json"}, True)
            self.assertIn("phase.baseline_binding", {item.code for item in schema_result.findings})
            (repo / "baseline-review.json").write_text(json.dumps({"schemaVersion": 1, "phaseId": "P1", "status": "Accepted", "reviewerModel": DEFAULT_MODELS["reviewer"], "baselineSha": base}), encoding="utf-8")
            outside_task = json.loads(json.dumps(current))
            outside_task["ownership"]["owned"] = ["src/**"]
            outside_result = check_repository(repo, profile(), outside_task, phase, {**review, "_evidencePath": "review.json"}, True)
            self.assertIn("ownership.outside", {item.code for item in outside_result.findings})
            forbidden_task = json.loads(json.dumps(current))
            forbidden_task["ownership"]["forbidden"] = ["candidate.txt"]
            forbidden_result = check_repository(repo, profile(), forbidden_task, phase, {**review, "_evidencePath": "review.json"}, True)
            self.assertIn("ownership.forbidden", {item.code for item in forbidden_result.findings})
            shared_task = json.loads(json.dumps(current))
            shared_task["ownership"] = {"owned": ["src/**"], "shared": ["candidate.txt"], "forbidden": []}
            shared_result = check_repository(repo, profile(), shared_task, phase, {**review, "_evidencePath": "review.json"}, True)
            self.assertIn("ownership.shared", {item.code for item in shared_result.findings})

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
            value = task(state="Candidate", previousState="Active", baseSha=base, commits={"candidate": candidate, "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": DEFAULT_MODELS["complex-worker"]}, execution={"handoff": "done", "validationEvidence": ["test"]})
            result = check_repository(repo, profile("standard"), value)
            self.assertTrue(result.ok, result.as_dict())

    def test_phase_prepare_does_not_claim_unreviewed_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
            baseline = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            unreviewed = run_cli("phase", "--repo", str(repo), "prepare", "--phase-id", "P1", "--integration-branch", "main", "--output", "phase.json")
            self.assertEqual("Planned", json.loads(unreviewed.stdout)["phase"]["baselineReview"]["status"])
            evidence = repo / "reviews" / "base.json"
            evidence.parent.mkdir()
            evidence.write_text(json.dumps({"schemaVersion": 1, "phaseId": "P2", "status": "Accepted", "reviewerModel": DEFAULT_MODELS["reviewer"], "baselineSha": baseline}), encoding="utf-8")
            reviewed = run_cli("phase", "--repo", str(repo), "prepare", "--phase-id", "P2", "--integration-branch", "main", "--baseline-review-evidence", "reviews/base.json", "--output", "reviewed.json")
            self.assertEqual("Accepted", json.loads(reviewed.stdout)["phase"]["baselineReview"]["status"])
            evidence.write_text(json.dumps({"schemaVersion": 999, "phaseId": "P2", "status": "Accepted", "reviewerModel": DEFAULT_MODELS["reviewer"], "baselineSha": baseline}), encoding="utf-8")
            wrong_schema = run_cli("phase", "--repo", str(repo), "prepare", "--phase-id", "P2", "--integration-branch", "main", "--baseline-review-evidence", "reviews/base.json", "--output", "wrong-schema.json")
            self.assertEqual("Planned", json.loads(wrong_schema.stdout)["phase"]["baselineReview"]["status"])
            evidence.write_text("{", encoding="utf-8")
            malformed = run_cli("phase", "--repo", str(repo), "prepare", "--phase-id", "P3", "--integration-branch", "main", "--baseline-review-evidence", "reviews/base.json", "--output", "malformed.json")
            self.assertEqual("Planned", json.loads(malformed.stdout)["phase"]["baselineReview"]["status"])

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

    def test_worktree_allocate_inspect_and_dry_release(self):
        with tempfile.TemporaryDirectory() as temp:
            container = Path(temp)
            repo, root = container / "repo", container / "lemmings-worktrees"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
            config = profile(); config["worktreeRoot"] = str(root)
            (repo / "profile.json").write_text(json.dumps(config), encoding="utf-8")
            allocated = run_cli("worktree", "--repo", str(repo), "--profile", "profile.json", "allocate", "--task", "TASK-1", "--branch", "codex/task-1")
            self.assertEqual(0, allocated.returncode, allocated.stdout + allocated.stderr)
            worktree = root / "task-1"
            self.assertTrue(worktree.is_dir())
            inspected = run_cli("worktree", "--repo", str(repo), "--profile", "profile.json", "inspect", "--path", str(worktree))
            self.assertTrue(json.loads(inspected.stdout)["worktree"]["registered"])
            release = run_cli("worktree", "--repo", str(repo), "--profile", "profile.json", "release", "--task", "TASK-1")
            self.assertFalse(json.loads(release.stdout)["executed"])
            self.assertTrue(worktree.exists())


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
        value = task(models={"requested": "gpt-custom:high", "assigned": DEFAULT_MODELS["complex-worker"], "actual": None})
        self.assertIn("model.pin", {f.code for f in validate_task(value, profile()).findings})

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
        value = task(models={"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": "fallback"})
        configured = profile(); configured["fallback"] = {"allowed": ["fallback"]}
        self.assertIn("model.fallback_reason", {f.code for f in validate_task(value, configured).findings})

    def test_stale_review_is_rejected_and_accepted_not_integrated(self):
        value = task(
            state="Accepted", previousState="Candidate",
            models={"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": DEFAULT_MODELS["complex-worker"]},
            commits={"candidate": "aaa", "fix": ["bbb"]},
            execution={"handoff": "done", "validationEvidence": [{"command": "test", "passed": True}]},
            review={"status": "Accepted", "cycle": 1, "head": "aaa", "evidence": "reviews/TASK-1.json"},
        )
        result = validate_task(value, profile())
        self.assertIn("review.stale", {f.code for f in result.findings})
        self.assertNotIn("integration.evidence", {f.code for f in result.findings})

    def test_second_failed_review_requires_replan(self):
        value = task(state="Candidate", previousState="Candidate", commits={"candidate": "aaa", "fix": []}, execution={"handoff": "done", "validationEvidence": ["test"]}, review={"status": "ChangesRequested", "cycle": 2, "head": "aaa", "evidence": "review.json"})
        self.assertIn("review.replan", {f.code for f in validate_task(value, profile()).findings})

    def test_strict_wave_requires_unique_worktrees_and_nonoverlap(self):
        phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": "abc", "integrationBranch": "main", "contractsFrozen": True}
        left = task(mode="strict", worktree="C:/wt/one")
        right = task(taskId="TASK-2", mode="strict", worktree="C:/wt/one")
        result = validate_wave(ROOT, [left, right], phase)
        codes = {f.code for f in result.findings}
        self.assertIn("worktree.duplicate", codes)
        self.assertIn("ownership.overlap", codes)

    def test_external_resource_requires_unique_lease(self):
        phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": "abc", "integrationBranch": "main", "contractsFrozen": True, "leases": []}
        value = task(mode="strict", worktree="C:/wt/one", risks=["externalResources"])
        self.assertIn("lease.required", {f.code for f in validate_wave(ROOT, [value], phase).findings})

    def test_ready_strict_writer_requires_owned_paths(self):
        value = task(mode="strict", ownership={"owned": [], "shared": [], "forbidden": []})
        self.assertIn("ownership.required", {f.code for f in validate_task(value, profile()).findings})
        candidate = task(mode="strict", state="Candidate", previousState="Active", baseSha="base", ownership={"owned": [], "shared": [], "forbidden": []})
        self.assertIn("ownership.required", {f.code for f in validate_task(candidate, profile()).findings})

    def test_embedded_review_base_and_nonempty_range_are_required(self):
        value = task(state="Accepted", previousState="Candidate", baseSha="base", commits={"candidate": "base", "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": DEFAULT_MODELS["complex-worker"]}, execution={"handoff": "done", "validationEvidence": ["test"]}, review={"taskId": "TASK-1", "base": "other", "head": "base", "status": "Accepted", "cycle": 0, "evidence": "review.json"})
        codes = {item.code for item in validate_task(value, profile()).findings}
        self.assertIn("review.base", codes)
        empty = json.loads(json.dumps(value))
        empty["review"]["base"] = "base"
        self.assertIn("review.range", {item.code for item in validate_task(empty, profile()).findings})


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
        command = "Get-Content data.json | ConvertFrom-Json | Where-Object active | ForEach-Object name | Sort-Object | Group-Object | Measure-Object | Format-Table"
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

    def test_unclosed_powershell_quotes_are_unknown(self):
        self.assertFalse(is_read_only_shell("Get-Content 'notes (final).md"))
        self.assertFalse(is_read_only_shell('Get-Content "notes (final).md'))

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
        worker = task(role="worker", models={"requested": None, "assigned": "worker-model:medium", "actual": None})
        configured = profile(); configured["models"]["worker"] = "worker-model:medium"
        writer = handle({"event": "PreToolUse", "toolName": "Agent", "task": worker, "profile": configured, "toolInput": {"task_name": "lemmings_worker", "message": "Implement the Ready task.", "model": "worker-model", "reasoning_effort": "medium"}})
        self.assertEqual("allow", writer["decision"])
        candidate = task(state="Candidate", previousState="Active", commits={"candidate": "abc", "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": DEFAULT_MODELS["complex-worker"]}, execution={"handoff": "done", "validationEvidence": ["test"]})
        reviewer = handle({"event": "PreToolUse", "toolName": "Agent", "task": candidate, "toolInput": {"task_name": "lemmings_reviewer", "message": "Review head abc.", "model": "gpt-5.6-sol", "reasoning_effort": "high", "head": "abc"}})
        self.assertEqual("allow", reviewer["decision"])
        wrong_model = handle({"event": "PreToolUse", "toolName": "Agent", "task": candidate, "toolInput": {"task_name": "lemmings_reviewer", "message": "Review head abc.", "model": "gpt-5.6-sol", "reasoning_effort": "medium", "head": "abc"}})
        self.assertEqual("block", wrong_model["decision"])
        wrong_state = handle({"event": "PreToolUse", "toolName": "Agent", "task": worker, "toolInput": {"task_name": "lemmings_reviewer", "message": "Review current candidate.", "model": "gpt-5.6-sol", "reasoning_effort": "high"}})
        self.assertEqual("block", wrong_state["decision"])

    def test_actual_shaped_summarizer_is_bounded_read_only_role(self):
        value = task(state="Active", previousState="Ready")
        output = handle({"event": "PreToolUse", "toolName": "Agent", "mode": "strict", "task": value, "toolInput": {"task_name": "lemmings_summarizer", "message": "Summarize supplied evidence.", "model": "gpt-5.6-terra", "reasoning_effort": "low"}})
        self.assertEqual("allow", output["decision"])

    def test_strict_spawn_requires_explicit_role(self):
        output = handle({"event": "PreToolUse", "toolName": "Agent", "mode": "strict", "task": task(), "toolInput": {"model": DEFAULT_MODELS["complex-worker"]}})
        self.assertEqual("block", output["decision"])

    def test_strict_writer_spawn_and_patch_require_owned_paths(self):
        value = task(mode="strict", ownership={"owned": [], "shared": [], "forbidden": []})
        spawn = handle({"event": "PreToolUse", "toolName": "Agent", "mode": "strict", "task": value, "profile": profile(), "toolInput": {"task_name": "lemmings_complex-worker", "message": "Implement.", "model": "gpt-5.6-sol", "reasoning_effort": "medium"}})
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
        value = task(state="Candidate", commits={"candidate": "abc", "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": DEFAULT_MODELS["complex-worker"]})
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
