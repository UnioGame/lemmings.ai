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
