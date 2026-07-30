from __future__ import annotations

import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from hook_handler import handle, host_output, load_runtime  # noqa: E402


def ready_task(**updates):
    value = {
        "taskId": "T1",
        "state": "Ready",
        "role": "worker",
        "worktree": "/tmp/t1",
        "selectedModel": "sol",
        "ownedPaths": ["scripts/**"],
        "sharedPaths": ["contracts/**"],
        "forbiddenPaths": ["secrets/**"],
        "resourceGates": [],
    }
    value.update(updates)
    return value


class PreToolTests(unittest.TestCase):
    def test_inactive_marker_never_blocks(self):
        output = handle(
            {
                "_orchestrationActive": False,
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
            }
        )
        self.assertEqual("allow", output["decision"])
        self.assertIn("inactive", output["reason"])

    def test_spawn_requires_baseline(self):
        output = handle(
            {
                "event": "PreToolUse",
                "toolName": "Agent",
                "task": ready_task(),
                "phase": {"baselineAccepted": False, "contractsFrozen": True},
                "manifest": {"tasks": [{"taskId": "T1"}]},
            }
        )
        self.assertEqual("block", output["decision"])

    def test_spawn_selected_model_and_manifest(self):
        base = {
            "event": "PreToolUse",
            "toolName": "Agent",
            "task": ready_task(),
            "phase": {"baselineAccepted": True, "contractsFrozen": True},
            "manifest": {"tasks": [{"taskId": "T1"}]},
        }
        self.assertEqual("allow", handle({**base, "requestedModel": "sol"})["decision"])
        self.assertEqual("block", handle({**base, "requestedModel": "terra"})["decision"])
        snake = {**base, "event": None, "hook_event_name": "PreToolUse", "tool_name": "Agent"}
        self.assertEqual("allow", handle({**snake, "requestedModel": "sol"})["decision"])

    def test_shared_contract_owner_only(self):
        output = handle(
            {
                "event": "PreToolUse",
                "toolName": "apply_patch",
                "task": ready_task(),
                "file_path": "contracts/api.md",
            }
        )
        self.assertEqual("block", output["decision"])
        owner = ready_task(role="shared-contract-owner")
        self.assertEqual(
            "allow",
            handle(
                {
                    "event": "PreToolUse",
                    "toolName": "apply_patch",
                    "task": owner,
                    "file_path": "contracts/api.md",
                }
            )["decision"],
        )
        terra_owner = ready_task(
            role="shared-contract-owner",
            selectedModel="gpt-5.6-terra",
        )
        self.assertEqual(
            "block",
            handle(
                {
                    "event": "PreToolUse",
                    "toolName": "apply_patch",
                    "task": terra_owner,
                    "file_path": "contracts/api.md",
                }
            )["decision"],
        )

    def test_reviewer_cannot_patch(self):
        output = handle(
            {
                "event": "PreToolUse",
                "toolName": "apply_patch",
                "task": ready_task(role="reviewer"),
                "file_path": "scripts/a.py",
            }
        )
        self.assertEqual("block", output["decision"])

    def test_host_pretool_block_uses_released_wire_shape(self):
        output = host_output(
            {"decision": "block", "reason": "no"},
            "PreToolUse",
            {},
        )
        specific = output["hookSpecificOutput"]
        self.assertEqual("PreToolUse", specific["hookEventName"])
        self.assertEqual("deny", specific["permissionDecision"])
        self.assertEqual("no", specific["permissionDecisionReason"])

    def test_apply_patch_command_paths_are_hydrated(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            marker = repo / ".git" / "codex-orchestration" / "active.json"
            marker.parent.mkdir(parents=True)
            marker.write_text('{"active": true}\n', encoding="utf-8")
            hydrated, _, error = load_runtime(
                {
                    "cwd": str(repo),
                    "hook_event_name": "PreToolUse",
                    "tool_name": "apply_patch",
                    "tool_input": {
                        "command": "*** Begin Patch\n*** Update File: scripts/a.py\n*** End Patch\n"
                    },
                }
            )
        self.assertIsNone(error)
        self.assertEqual(["scripts/a.py"], hydrated["changedPaths"])


class ContextTests(unittest.TestCase):
    def test_bounded_context_and_one_expansion(self):
        allowed = handle(
            {
                "event": "SubagentStart",
                "contextPacket": {"taskPacket": "task.md", "interfaces": ["api.py"]},
                "contextExpansion": {"symbolOrDecision": "Api.run"},
                "expansionsUsed": 0,
            }
        )
        self.assertEqual("allow", allowed["decision"])
        exhausted = handle(
            {
                "event": "SubagentStart",
                "contextPacket": {"taskPacket": "task.md"},
                "contextExpansion": {"symbolOrDecision": "Api.run"},
                "expansionsUsed": 1,
            }
        )
        self.assertEqual("block", exhausted["decision"])
        broad = handle(
            {
                "event": "SubagentStart",
                "contextPacket": {"taskPacket": "task.md"},
                "contextExpansion": {"symbolOrDecision": "all docs", "broad": True},
            }
        )
        self.assertEqual("block", broad["decision"])

    def test_subagent_start_adds_context_without_false_block_contract(self):
        payload = {
            "contextPacket": {
                "taskPacket": "docs/tasks/T1.md",
                "interfaces": ["src/api.py"],
            }
        }
        output = host_output({"decision": "allow", "reason": "ok"}, "SubagentStart", payload)
        self.assertNotIn("decision", output)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("docs/tasks/T1.md", context)
        self.assertIn("src/api.py", context)

    def test_invalid_start_context_is_advisory_on_start(self):
        output = host_output(
            {"decision": "block", "reason": "unbounded"},
            "SubagentStart",
            {},
        )
        self.assertNotIn("decision", output)
        self.assertIn("policy violation", output["systemMessage"])


class PostToolTests(unittest.TestCase):
    def test_explicit_forbidden_path_warns_without_blocking(self):
        output = handle(
            {
                "hook_event_name": "PostToolUse",
                "task": ready_task(),
                "changedPaths": ["secrets/key.txt"],
            }
        )
        self.assertEqual("warn", output["decision"])
        self.assertIn("secrets/key.txt", output["reason"])
        self.assertIn("unsuitable until corrected", output["reason"])

    def test_explicit_owned_diff_allows(self):
        output = handle(
            {
                "event": "PostToolUse",
                "task": ready_task(),
                "changedPaths": ["scripts/orchestration_core.py"],
            }
        )
        self.assertEqual("allow", output["decision"])

    def test_shared_contract_terra_owner_warns_after_tool(self):
        output = handle(
            {
                "event": "PostToolUse",
                "task": ready_task(
                    role="shared-contract-owner",
                    selectedModel="gpt-5.6-terra",
                ),
                "changedPaths": ["contracts/api.md"],
            }
        )
        self.assertEqual("warn", output["decision"])
        self.assertIn("shared-non-sol-owner", output["reason"])

    def test_git_diff_fallback_finds_outside_owned_path(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "scripts").mkdir()
            (repo / "scripts" / "ok.py").write_text("ok = True\n", encoding="utf-8")
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            output = handle(
                {
                    "event": "PostToolUse",
                    "cwd": str(repo),
                    "task": ready_task(worktree=str(repo)),
                }
            )
            self.assertEqual("warn", output["decision"])
            self.assertIn("README.md", output["reason"])

    def test_inactive_post_tool_is_noop(self):
        output = handle(
            {
                "_orchestrationActive": False,
                "event": "PostToolUse",
                "changedPaths": ["secrets/key.txt"],
            }
        )
        self.assertEqual("allow", output["decision"])
        self.assertIn("inactive", output["reason"])


class StopTests(unittest.TestCase):
    def test_subagent_stop_requires_commit_and_evidence(self):
        task = ready_task(state="Candidate", actualModel="sol")
        missing = handle(
            {
                "event": "SubagentStop",
                "task": task,
                "handoffPresent": True,
                "validationEvidence": True,
            }
        )
        self.assertEqual("block", missing["decision"])
        task["candidateCommit"] = "deadbeef"
        complete = handle(
            {
                "event": "SubagentStop",
                "task": task,
                "handoffPresent": True,
                "validationEvidence": True,
                "dirtyWorktree": False,
            }
        )
        self.assertEqual("allow", complete["decision"])

    def test_stop_continues_only_once(self):
        payload = {
            "event": "Stop",
            "task": {"state": "Accepted"},
            "reviewEvidencePresent": False,
            "integrationEvidencePresent": False,
            "runtimeState": {},
        }
        first = handle(payload)
        self.assertEqual("continue", first["decision"])
        payload["runtimeState"] = first["stateUpdates"]
        second = handle(payload)
        self.assertEqual("allow", second["decision"])

    def test_host_stop_continuation_uses_block_decision(self):
        output = host_output(
            {"decision": "continue", "reason": "missing review"},
            "Stop",
            {},
        )
        self.assertEqual({"decision": "block", "reason": "missing review"}, output)

    def test_host_stop_active_prevents_second_continuation(self):
        output = handle(
            {
                "event": "Stop",
                "task": {"state": "Accepted"},
                "reviewEvidencePresent": False,
                "integrationEvidencePresent": False,
                "runtimeState": {},
                "stop_hook_active": True,
            }
        )
        self.assertEqual("allow", output["decision"])


if __name__ == "__main__":
    unittest.main()
