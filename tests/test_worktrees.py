from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "orchestration_cli.py"
sys.path.insert(0, str(ROOT / "scripts"))

from orchestration_core import cleanup_inventory  # noqa: E402


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class WorktreeTests(unittest.TestCase):
    def test_linked_worktree_uses_primary_relative_profile_root_and_status_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            container = Path(temp)
            primary = container / "repo"
            canonical_root = container / "worktrees"
            linked = canonical_root / "linked"
            primary.mkdir()
            canonical_root.mkdir()
            self.assertEqual(0, git(primary, "init").returncode)
            self.assertEqual(0, git(primary, "config", "user.email", "test@example.invalid").returncode)
            self.assertEqual(0, git(primary, "config", "user.name", "Test").returncode)
            (primary / "README.md").write_text("base\n", encoding="utf-8")
            self.assertEqual(0, git(primary, "add", "README.md").returncode)
            self.assertEqual(0, git(primary, "commit", "-m", "base").returncode)
            baseline = git(primary, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(
                0,
                git(primary, "worktree", "add", "-b", "codex/linked", str(linked), baseline).returncode,
            )
            (linked / "ROADMAP.md").write_text("| Task |\n| T-LINK |\n", encoding="utf-8")
            profile_path = linked / "profile.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "taskAdapter": "generic-markdown-v1",
                        "roadmap": "ROADMAP.md",
                        "worktreeRoot": "../worktrees",
                        "phaseBranchPattern": "codex/phase-{phase}-{slug}",
                        "taskBranchPattern": "codex/{taskId}-{slug}",
                        "maxAgents": 3,
                        "maxWriters": 2,
                        "integrationStrategy": "no-ff",
                        "reviewCycles": 2,
                    }
                ),
                encoding="utf-8",
            )
            task = {
                "taskId": "T-LINK",
                "phaseId": "P1",
                "state": "Ready",
                "previousState": "Planned",
                "baselineSha": baseline,
                "branch": "codex/linked",
                "worktree": str(linked),
                "preferredModel": "sol",
                "approvedFallback": "terra",
                "selectedModel": "sol",
                "ownedPaths": ["scripts/**"],
                "sharedPaths": [],
                "forbiddenPaths": [],
                "dependencies": [],
                "integrationOrder": 1,
            }
            task_path = linked / "task.json"
            task_path.write_text(json.dumps(task), encoding="utf-8")

            def status(path: Path) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        str(CLI),
                        "status",
                        "--repo",
                        str(linked),
                        "--profile",
                        str(profile_path),
                        "--task",
                        str(path),
                        "--json",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            valid = status(task_path)
            valid_output = json.loads(valid.stdout)
            self.assertEqual(0, valid.returncode, valid.stdout + valid.stderr)
            self.assertTrue(valid_output["ok"])
            self.assertEqual(
                str(canonical_root.resolve()),
                valid_output["data"]["bindings"][0]["worktreeRoot"],
            )

            invalid_task = {**task, "worktree": str(canonical_root / "missing")}
            invalid_path = linked / "invalid-task.json"
            invalid_path.write_text(json.dumps(invalid_task), encoding="utf-8")
            invalid = status(invalid_path)
            invalid_output = json.loads(invalid.stdout)
            self.assertNotEqual(0, invalid.returncode, "status failure exit code must not be masked")
            self.assertFalse(invalid_output["ok"])
            self.assertIn(
                "worktree.missing",
                {item["code"] for item in invalid_output["findings"]},
            )

    def test_allocate_enforces_profile_root_and_phase_base(self):
        with tempfile.TemporaryDirectory() as temp:
            container = Path(temp)
            repo = container / "repo"
            root = container / "worktrees"
            repo.mkdir()
            root.mkdir()
            self.assertEqual(0, git(repo, "init").returncode)
            self.assertEqual(0, git(repo, "config", "user.email", "test@example.invalid").returncode)
            self.assertEqual(0, git(repo, "config", "user.name", "Test").returncode)
            (repo / "README.md").write_text("one\n", encoding="utf-8")
            self.assertEqual(0, git(repo, "add", "README.md").returncode)
            self.assertEqual(0, git(repo, "commit", "-m", "one").returncode)
            baseline = git(repo, "rev-parse", "HEAD").stdout.strip()
            (repo / "README.md").write_text("two\n", encoding="utf-8")
            self.assertEqual(0, git(repo, "commit", "-am", "two").returncode)
            wrong_base = git(repo, "rev-parse", "HEAD").stdout.strip()
            profile = repo / "profile.json"
            phase = repo / "phase.json"
            profile.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "taskAdapter": "generic-markdown-v1",
                        "roadmap": "ROADMAP.md",
                        "worktreeRoot": "../worktrees",
                        "phaseBranchPattern": "codex/phase-{phase}-{slug}",
                        "taskBranchPattern": "codex/{taskId}-{slug}",
                        "maxAgents": 3,
                        "maxWriters": 2,
                        "integrationStrategy": "no-ff",
                        "reviewCycles": 2,
                    }
                ),
                encoding="utf-8",
            )
            phase.write_text(
                json.dumps(
                    {
                        "phaseId": "P1",
                        "integrationBranch": "master",
                        "reviewedBaseSha": baseline,
                        "baselineAccepted": True,
                        "contractsFrozen": True,
                        "phaseValidation": ["test"],
                    }
                ),
                encoding="utf-8",
            )

            def allocate(path: Path, branch: str, base: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        str(CLI),
                        "worktree",
                        "allocate",
                        "--repo",
                        str(repo),
                        str(path),
                        branch,
                        "--profile",
                        str(profile),
                        "--phase",
                        str(phase),
                        "--base",
                        base,
                        "--create-branch",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            outside = allocate(container / "outside", "codex/outside", baseline)
            self.assertNotEqual(0, outside.returncode)
            self.assertIn("outside", json.loads(outside.stdout)["error"])
            wrong = allocate(root / "wrong", "codex/wrong", wrong_base)
            self.assertNotEqual(0, wrong.returncode)
            self.assertIn("differs", json.loads(wrong.stdout)["error"])
            valid = allocate(root / "valid", "codex/valid", baseline)
            self.assertEqual(0, valid.returncode, valid.stdout + valid.stderr)
            self.assertTrue((root / "valid").is_dir())
            self.assertEqual("codex/valid", git(root / "valid", "branch", "--show-current").stdout.strip())
            self.assertEqual(baseline, git(root / "valid", "rev-parse", "HEAD").stdout.strip())

            missing_task = {
                "taskId": "T-MISSING",
                "phaseId": "P1",
                "waveId": "W1",
                "state": "Ready",
                "previousState": "Planned",
                "baselineSha": baseline,
                "branch": "codex/missing",
                "worktree": str(root / "missing"),
                "preferredModel": "sol",
                "approvedFallback": "terra",
                "selectedModel": "sol",
                "ownedPaths": ["scripts/**"],
                "sharedPaths": [],
                "forbiddenPaths": [],
                "dependencies": [],
                "integrationOrder": 1,
            }
            task_path = repo / "missing-task.json"
            manifest_path = repo / "missing-manifest.json"
            task_path.write_text(json.dumps(missing_task), encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "baselineSha": baseline,
                        "tasks": [missing_task],
                        "maxAgents": 3,
                        "maxWriters": 2,
                    }
                ),
                encoding="utf-8",
            )
            dispatch = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "dispatch",
                    "validate",
                    "--repo",
                    str(repo),
                    str(manifest_path),
                    "--profile",
                    str(profile),
                    "--phase",
                    str(phase),
                    "--task",
                    str(task_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(0, dispatch.returncode)
            self.assertIn(
                "worktree.missing",
                {item["code"] for item in json.loads(dispatch.stdout)["findings"]},
            )

    def test_stale_worktree_is_detected_and_release_is_dry_run(self):
        with tempfile.TemporaryDirectory() as temp:
            container = Path(temp)
            repo = container / "repo"
            worktree = container / "task-worktree"
            repo.mkdir()
            self.assertEqual(0, git(repo, "init").returncode)
            self.assertEqual(0, git(repo, "config", "user.email", "test@example.invalid").returncode)
            self.assertEqual(0, git(repo, "config", "user.name", "Test").returncode)
            (repo / "README.md").write_text("test\n", encoding="utf-8")
            self.assertEqual(0, git(repo, "add", "README.md").returncode)
            self.assertEqual(0, git(repo, "commit", "-m", "initial").returncode)
            self.assertEqual(
                0,
                git(repo, "worktree", "add", "-b", "codex/task", str(worktree), "HEAD").returncode,
            )

            inventory = cleanup_inventory(
                repo,
                [{"taskId": "T1", "state": "Accepted", "worktree": str(worktree)}],
            )
            row = next(item for item in inventory if Path(item["worktree"]) == worktree)
            self.assertTrue(row["stale"])
            self.assertIn("worktree remove", row["recommendation"])

            process = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--repo",
                    str(repo),
                    "worktree",
                    "release",
                    str(worktree),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            self.assertFalse(json.loads(process.stdout)["executed"])
            self.assertTrue(worktree.exists(), "default release must not remove the worktree")


if __name__ == "__main__":
    unittest.main()
