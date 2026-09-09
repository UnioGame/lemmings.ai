from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))

import lemmings.workspace as workspace_module
from lemmings.contracts import git, write_object
from lemmings.workspace import (
    inspect_registered_workspace,
    load_registry,
    prepare_workspace,
    register_workspace,
    release_workspace,
)


def init_repo(path: Path) -> str:
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "tests@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Workspace Tests"], check=True)
    (path / "owned.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "owned.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def write_task(repo: Path, base: str, destination: Path, branch: str = "codex/task", state: str = "Integrated") -> Path:
    task = {
        "schemaVersion": 4,
        "revision": 0,
        "taskId": "TASK-1",
        "state": state,
        "baseSha": base,
        "commits": {"candidate": base, "fix": []},
        "workspace": {
            "workspaceId": "WS-1",
            "backend": "code-worktree",
            "managedBy": "lemmings",
            "lifetime": "task",
            "repoRoot": str(repo),
            "destination": str(destination),
            "branch": branch,
            "baseSha": base,
            "estimatedGiB": 1.0,
            "approval": "not-required",
        },
        "close": {
            "mergeCommit": base,
            "integrationEvidence": [{"headSha": base, "command": "git diff --check", "passed": True}],
        },
    }
    path = repo / "docs" / "tasks" / "task.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task), encoding="utf-8")
    return path


class WorkspaceSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.base = init_repo(self.repo)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prepare_reserves_and_release_requires_canonical_integrated_task(self) -> None:
        destination = self.root / "worktree"
        task_path = write_task(self.repo, self.base, destination)
        prepared = prepare_workspace(
            self.repo,
            task_path=task_path,
            destination=destination,
            branch="codex/task",
            expected_revision=0,
            approval="not-required",
        )
        self.assertTrue(destination.is_dir())
        self.assertTrue(prepared["entry"]["prepared"])
        released = release_workspace(
            self.repo,
            task_path=task_path,
            task_revision=0,
            expected_revision=prepared["revision"],
            action="remove",
        )
        self.assertEqual("removed", released["action"])
        self.assertFalse(destination.exists())

    def test_legacy_boolean_evidence_cannot_remove_an_active_workspace(self) -> None:
        destination = self.root / "worktree"
        task_path = write_task(self.repo, self.base, destination)
        prepared = prepare_workspace(
            self.repo,
            task_path=task_path,
            destination=destination,
            branch="codex/task",
            expected_revision=0,
            approval="not-required",
        )
        blocked = release_workspace(
            self.repo,
            workspace_id="WS-1",
            expected_revision=prepared["revision"],
            task_state="Integrated",
            integration_evidence=True,
            action="remove",
        )
        self.assertEqual("retained", blocked["action"])
        self.assertTrue(destination.exists())
        entry = load_registry(self.repo)["entries"][0]
        self.assertEqual("quarantined", entry["state"])
        self.assertIn("canonical-task-required", entry["quarantineReason"])

    def test_primary_and_dirty_workspaces_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "primary"):
            register_workspace(
                self.repo,
                workspace_id="primary",
                path=self.repo,
                backend="code-worktree",
                managed_by="lemmings",
                lifetime="task",
                expected_revision=0,
            )
        dirty = self.root / "dirty"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-b", "dirty", str(dirty), self.base], check=True)
        (dirty / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "clean"):
            register_workspace(
                self.repo,
                workspace_id="dirty",
                path=dirty,
                backend="code-worktree",
                managed_by="lemmings",
                lifetime="task",
                expected_revision=0,
            )

    def test_relative_git_path_is_resolved_from_workspace_cwd(self) -> None:
        target = self.root / "worktree"
        target.mkdir()
        result = subprocess.CompletedProcess([], 0, ".git/MERGE_HEAD\n", "")
        with patch.object(workspace_module, "git", return_value=result):
            self.assertEqual((target / ".git" / "MERGE_HEAD").resolve(), workspace_module._git_path(target, "MERGE_HEAD"))

    def test_unknown_process_record_blocks_cleanup_even_when_pid_is_dead(self) -> None:
        destination = self.root / "worktree"
        task_path = write_task(self.repo, self.base, destination)
        prepared = prepare_workspace(
            self.repo,
            task_path=task_path,
            destination=destination,
            branch="codex/task",
            expected_revision=0,
            approval="not-required",
        )
        registry = load_registry(self.repo)
        registry["entries"][0]["processes"] = [{"pid": 999999, "invocationId": "inv", "status": "unknown"}]
        write_object(workspace_module.registry_path(self.repo), registry)
        blocked = release_workspace(
            self.repo,
            task_path=task_path,
            task_revision=0,
            expected_revision=prepared["revision"],
            action="remove",
        )
        self.assertEqual("retained", blocked["action"])
        self.assertTrue(destination.exists())
        self.assertIn("live-or-unknown-process", load_registry(self.repo)["entries"][0]["quarantineReason"])

    def test_prepare_failure_quarantines_reservation_without_rollback(self) -> None:
        subprocess.run(["git", "-C", str(self.repo), "branch", "codex/existing", self.base], check=True)
        destination = self.root / "worktree"
        task_path = write_task(self.repo, self.base, destination, branch="codex/existing")
        with self.assertRaisesRegex(ValueError, "already exists|provisioning"):
            prepare_workspace(
                self.repo,
                task_path=task_path,
                destination=destination,
                branch="codex/existing",
                expected_revision=0,
                approval="not-required",
            )
        entry = load_registry(self.repo)["entries"][0]
        self.assertEqual("quarantined", entry["state"])
        self.assertFalse(destination.exists())

    def test_understated_estimate_cannot_bypass_large_workspace_approval(self) -> None:
        destination = self.root / "large-worktree"
        task_path = write_task(self.repo, self.base, destination)
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["workspace"]["estimatedGiB"] = 0
        task_path.write_text(json.dumps(task), encoding="utf-8")
        with patch.object(workspace_module, "_size", return_value=11 * workspace_module.GIB):
            with self.assertRaisesRegex(ValueError, "understates|approval"):
                prepare_workspace(
                    self.repo,
                    task_path=task_path,
                    destination=destination,
                    branch="codex/task",
                    expected_revision=0,
                    approval="not-required",
                )
        self.assertEqual([], load_registry(self.repo)["entries"])

    def test_forged_integrated_task_cannot_remove_workspace(self) -> None:
        destination = self.root / "worktree"
        task_path = write_task(self.repo, self.base, destination)
        prepared = prepare_workspace(
            self.repo,
            task_path=task_path,
            destination=destination,
            branch="codex/task",
            expected_revision=0,
            approval="not-required",
        )
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["close"]["mergeCommit"] = "forged"
        task_path.write_text(json.dumps(task), encoding="utf-8")
        result = release_workspace(
            self.repo,
            task_path=task_path,
            task_revision=0,
            expected_revision=prepared["revision"],
            action="remove",
        )
        self.assertEqual("retained", result["action"])
        self.assertTrue(destination.exists())


if __name__ == "__main__":
    unittest.main()
