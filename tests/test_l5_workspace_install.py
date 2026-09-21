from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))

from lemmings.workspace import claim_workspace, load_registry, register_workspace


class DisabledPoolTests(unittest.TestCase):
    def test_disabled_pool_does_not_reuse_idle_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            workspace = Path(temp) / "workspace"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Lemmings Tests"], cwd=repo, check=True)
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "worktree", "add", "--detach", str(workspace), head], cwd=repo, check=True, capture_output=True)
            register_workspace(
                repo,
                workspace_id="idle",
                path=workspace,
                backend="code-worktree",
                managed_by="lemmings",
                lifetime="task",
                expected_revision=0,
            )
            profile = {"workspacePool": {"enabled": False, "maxIdle": 2, "maxIdleGiB": 10, "eviction": "lru"}}
            with self.assertRaisesRegex(ValueError, "pool is disabled"):
                claim_workspace(
                    repo,
                    workspace_id="idle",
                    task_id="T1",
                    base_sha=head,
                    integration_head=head,
                    branch="task/T1",
                    expected_revision=1,
                    profile=profile,
                )
            entry = load_registry(repo)["entries"][0]
            self.assertEqual("idle", entry["state"])
            self.assertIsNone(entry["taskId"])


class UpgradeInstallerTests(unittest.TestCase):
    def test_legacy_profiles_receive_5_budget_defaults_and_keep_manual_routes(self) -> None:
        for version in ("4.1.1", "4.5.0", "5.0.0"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                repo = Path(temp) / "repo"
                repo.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
                profile_path = repo / ".agents/lemmings.json"
                profile_path.parent.mkdir(parents=True)
                profile_path.write_text(json.dumps({
                    "schemaVersion": 5,
                    "distributionVersion": version,
                    "modelRoutes": {
                        "codex": {
                            "worker": [{"providerId": "manual", "modelId": "chosen", "variantId": "high"}],
                            "reviewer": [{"providerId": "manual", "modelId": "review", "variantId": "high"}],
                            "explorer": [{"providerId": "manual", "modelId": "search", "variantId": "high"}],
                        }
                    },
                    "orchestration": {"maxRepairs": 1},
                }), encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "skills/lemmings/scripts/install.py"), "--repo", str(repo)],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                installed = json.loads(profile_path.read_text(encoding="utf-8"))
                self.assertEqual("6.1.0", installed["distributionVersion"])
                self.assertEqual(1 if version == "5.0.0" else 3, installed["orchestration"]["maxRepairs"])
                self.assertEqual(32768, installed["contextPolicy"]["ceilings"]["maxPacketBytes"])
                self.assertEqual(48, installed["invocationBudgets"]["worker"]["maxToolCalls"])
                self.assertEqual("chosen", installed["modelRoutes"]["codex"]["worker"][0]["modelId"])


if __name__ == "__main__":
    unittest.main()
