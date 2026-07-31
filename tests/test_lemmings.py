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
from lemmings.hooks import handle, host_output, is_read_only_shell  # noqa: E402


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
        self.assertEqual("lemmings", plugin["name"])

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

    def test_check_all_validates_full_strict_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            current = task(
                state="Accepted", previousState="Candidate", worktree=str(repo),
                models={"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": DEFAULT_MODELS["complex-worker"]},
                commits={"candidate": "candidate", "fix": []},
                execution={"handoff": "done", "validationEvidence": ["python -m unittest"]},
                review={"status": "Accepted", "cycle": 0, "head": "candidate", "evidence": "review.json"},
            )
            phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": "base", "integrationBranch": "main", "contractsFrozen": True}
            review = {"schemaVersion": 1, "taskId": "TASK-1", "base": "base", "head": "candidate", "status": "Accepted", "reviewerModel": DEFAULT_MODELS["reviewer"]}
            for name, value in (("profile.json", profile()), ("task.json", current), ("phase.json", phase), ("review.json", review)):
                (repo / name).write_text(json.dumps(value), encoding="utf-8")
            process = run_cli("check", "--repo", str(repo), "--profile", "profile.json", "--task", "task.json", "--phase", "phase.json", "--review", "review.json", "--all")
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            self.assertTrue(json.loads(process.stdout)["ok"])

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

    def test_user_pin_has_priority(self):
        value = task(models={"requested": "gpt-custom:high", "assigned": DEFAULT_MODELS["complex-worker"], "actual": None})
        self.assertIn("model.pin", {f.code for f in validate_task(value, profile()).findings})

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
        result = validate_wave([left, right], phase)
        codes = {f.code for f in result.findings}
        self.assertIn("worktree.duplicate", codes)
        self.assertIn("ownership.overlap", codes)

    def test_external_resource_requires_unique_lease(self):
        phase = {"schemaVersion": 1, "phaseId": "P1", "baselineSha": "abc", "integrationBranch": "main", "contractsFrozen": True, "leases": []}
        value = task(mode="strict", worktree="C:/wt/one", risks=["externalResources"])
        self.assertIn("lease.required", {f.code for f in validate_wave([value], phase).findings})


class HookTests(unittest.TestCase):
    def test_read_only_shell_is_allowed(self):
        self.assertTrue(is_read_only_shell("git status"))
        self.assertTrue(is_read_only_shell("rg TODO lemmings"))
        output = handle({"event": "PreToolUse", "toolName": "shell_command", "mode": "strict", "task": task(), "toolInput": {"command": "git diff --check"}})
        self.assertEqual("allow", output["decision"])

    def test_unknown_shell_warns_standard_and_blocks_strict(self):
        payload = {"event": "PreToolUse", "toolName": "shell_command", "task": task(), "toolInput": {"command": "custom-tool run"}}
        warned = handle(payload)
        self.assertEqual("warn", warned["decision"])
        self.assertNotIn("permissionDecision", host_output(warned, "PreToolUse", {}).get("hookSpecificOutput", {}))
        payload["mode"] = "strict"
        self.assertEqual("block", handle(payload)["decision"])

    def test_ownership_and_reviewer_are_blocked(self):
        outside = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": task(), "changedPaths": ["README.md"]})
        self.assertEqual("block", outside["decision"])
        review_task = task(role="reviewer")
        reviewer = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": review_task, "changedPaths": ["lemmings/core.py"]})
        self.assertEqual("block", reviewer["decision"])

    def test_stop_has_no_continuation_behavior(self):
        output = handle({"event": "Stop", "task": task(state="Accepted")})
        self.assertEqual("allow", output["decision"])
        self.assertEqual({}, host_output(output, "Stop", {}))

    def test_subagent_stop_requires_embedded_handoff(self):
        value = task(state="Candidate", commits={"candidate": "abc", "fix": []}, models={"requested": None, "assigned": DEFAULT_MODELS["complex-worker"], "actual": DEFAULT_MODELS["complex-worker"]})
        output = handle({"event": "SubagentStop", "task": value})
        self.assertEqual("block", output["decision"])


if __name__ == "__main__":
    unittest.main()
