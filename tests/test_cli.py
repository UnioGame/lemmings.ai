from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "orchestration_cli.py"


class CliTests(unittest.TestCase):
    def test_consumer_profile_validate_interface(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "profile.json"
            config.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "taskAdapter": "autoqa-markdown-v1",
                        "roadmap": "docs/tasks/ROADMAP.md",
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
            process = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "profile",
                    "validate",
                    "--repo",
                    str(root),
                    "--profile",
                    str(config),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            self.assertTrue(json.loads(process.stdout)["ok"])

    def test_hook_json_stdio(self):
        hook = ROOT / "scripts" / "hook_handler.py"
        process = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({"event": "Stop", "task": {"state": "Cancelled"}}),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, process.returncode)
        self.assertEqual({}, json.loads(process.stdout))

    def test_runtime_marker_activation_and_stop_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            state_path = repo / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "task": {
                            "taskId": "T1",
                            "state": "Ready",
                            "selectedModel": "sol",
                            "role": "worker",
                            "worktree": str(repo / "task-worktree"),
                        },
                        "phase": {"baselineAccepted": True, "contractsFrozen": True},
                        "manifest": {"tasks": [{"taskId": "T1"}]},
                        "profile": {"hooks": {"policy": "hybrid"}},
                    }
                ),
                encoding="utf-8",
            )
            activate = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "runtime",
                    "activate",
                    "--repo",
                    str(repo),
                    "--state",
                    str(state_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, activate.returncode, activate.stdout + activate.stderr)
            marker = Path(json.loads(activate.stdout)["marker"])
            self.assertTrue(marker.exists())
            hook = ROOT / "scripts" / "hook_handler.py"
            spawn = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
                "tool_input": {"model": "sol"},
                "cwd": str(repo),
            }
            allowed_spawn = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps(spawn),
                capture_output=True,
                text=True,
                check=False,
            )
            blocked_spawn = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({**spawn, "tool_input": {"model": "terra"}}),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual({}, json.loads(allowed_spawn.stdout))
            blocked_specific = json.loads(blocked_spawn.stdout)["hookSpecificOutput"]
            self.assertEqual("deny", blocked_specific["permissionDecision"])

            marker_state = json.loads(marker.read_text(encoding="utf-8"))
            marker_state["task"].update({"state": "Accepted", "actualModel": "sol"})
            marker.write_text(json.dumps(marker_state), encoding="utf-8")
            payload = {
                "hook_event_name": "Stop",
                "cwd": str(repo),
                "reviewEvidencePresent": False,
                "integrationEvidencePresent": False,
            }
            first = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )
            second = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual("block", json.loads(first.stdout)["decision"])
            self.assertEqual({}, json.loads(second.stdout))
            status = subprocess.run(
                [sys.executable, str(CLI), "runtime", "status", "--repo", str(repo)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertTrue(json.loads(status.stdout)["state"]["runtimeState"]["stopContinuationUsed"])

    def test_inactive_real_hook_does_not_block_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            hook = ROOT / "scripts" / "hook_handler.py"
            process = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps(
                    {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(repo)}
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            output = json.loads(process.stdout)
            self.assertEqual({}, output)

    def test_status_validates_manifest_handoff_review_and_roadmap(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", "base"],
                check=True,
                capture_output=True,
            )
            branch = subprocess.run(
                ["git", "-C", str(repo), "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            (repo / "ROADMAP.md").write_text("| Task |\n| T1 |\n", encoding="utf-8")
            profile_path = repo / "profile.json"
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
            task_data = {
                "taskId": "T1",
                "phaseId": "P1",
                "waveId": "W1",
                "state": "Candidate",
                "baselineSha": "base",
                "branch": branch,
                "worktree": str(repo),
                "preferredModel": "sol",
                "approvedFallback": "terra",
                "selectedModel": "sol",
                "actualModel": "sol",
                "candidateCommit": "c1",
                "ownedPaths": ["scripts/**"],
                "sharedPaths": [],
                "forbiddenPaths": [],
                "dependencies": [],
                "integrationOrder": 1,
            }
            snapshot = dict(task_data)
            snapshot["state"] = "Ready"
            snapshot.pop("actualModel")
            snapshot.pop("candidateCommit")
            artifacts = {
                "task.json": task_data,
                "manifest.json": {"tasks": [snapshot]},
                "handoff.json": {
                    "taskId": "T1",
                    "actualModel": "sol",
                    "candidateCommit": "c1",
                },
                "review.json": {
                    "taskId": "T1",
                    "commitRange": "base..c1",
                    "verdict": "approved",
                },
            }
            for name, value in artifacts.items():
                (repo / name).write_text(json.dumps(value), encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "status",
                    "--repo",
                    str(repo),
                    "--profile",
                    str(profile_path),
                    "--task",
                    str(repo / "task.json"),
                    "--manifest",
                    str(repo / "manifest.json"),
                    "--handoff",
                    str(repo / "handoff.json"),
                    "--review",
                    str(repo / "review.json"),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            output = json.loads(process.stdout)
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            self.assertTrue(output["ok"])
            self.assertTrue(output["data"]["bindings"][0]["branchExists"])
            self.assertTrue(output["data"]["bindings"][0]["worktreeExists"])


if __name__ == "__main__":
    unittest.main()
