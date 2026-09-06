from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/lemmings/scripts"))
from unittest.mock import patch

from lemmings.contracts import (
    DEFAULT_INVOCATION_LIMITS,
    dispatchable_tasks,
    resolve_auto_mode,
    validate_agent_result,
    validate_batch,
    validate_invocation,
    validate_host_capabilities,
    validate_profile,
    validate_task,
)
from lemmings.hooks import derive_context_packet, handle
from lemmings.models import (
    advance_recovery_route,
    apply_proposal,
    apply_recovery_proposal,
    build_proposal,
    build_recovery_proposal,
    normalize_capacity_probe,
    route_failure_action,
)
from lemmings.usage import normalize_usage_export
from lemmings.workspace import claim_workspace, inspect_registry, load_registry, register_workspace, release_workspace
import lemmings.workspace as workspace_module

ROOT = Path(__file__).resolve().parents[1]


def profile() -> dict:
    return {
        "schemaVersion": 4,
        "distributionVersion": "4.1.0",
        "mode": "auto",
        "modelRoutes": {
            "codex": {
                "worker": [
                    {"providerId": "openai", "modelId": "gpt-5.6-luna", "variantId": "max"},
                    {"providerId": "openai", "modelId": "gpt-5.6-terra", "variantId": "max"},
                ],
                "reviewer": [{"providerId": "openai", "modelId": "gpt-5.6-sol", "variantId": "high"}],
                "explorer": [{"providerId": "openai", "modelId": "gpt-5.6-luna", "variantId": "high"}],
            }
        },
        "contextPolicy": {"maxPacketBytes": 16384, "maxWorkingSetItems": 12, "maxExpansions": 1},
        "orchestration": {"maxDelegationDepth": 1, "maxConcurrentWriters": 2, "maxConcurrentReaders": 2, "managerSlots": 1, "maxRepairs": 1, "maxTransportRetries": 1},
        "workspacePool": {"enabled": True, "maxIdle": 2, "maxIdleGiB": 10, "eviction": "lru"},
    }


def task(task_id: str = "TASK-1") -> dict:
    value = json.loads((ROOT / "skills/lemmings/templates/task.json").read_text(encoding="utf-8"))
    value["taskId"] = task_id
    return value


def git(repo: Path, *args: str) -> str:
    process = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return process.stdout.strip()


def init_repo(root: Path) -> str:
    git(root, "init")
    git(root, "config", "user.email", "tests@example.invalid")
    git(root, "config", "user.name", "Lemmings Tests")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "base")
    return git(root, "rev-parse", "HEAD")


class AutoAndContractV4Tests(unittest.TestCase):
    def test_profile_and_template_are_valid(self):
        self.assertTrue(validate_profile(profile()).ok)
        self.assertTrue(validate_task(task(), profile()).ok)

    def test_auto_priority_and_explicit_pin(self):
        self.assertEqual("strict", resolve_auto_mode({"modeReasons": ["submodules"], "riskClass": "low"})["resolvedMode"])
        self.assertEqual("strict", resolve_auto_mode({"writerCount": 2, "riskClass": "low"})["resolvedMode"])
        self.assertEqual("standard", resolve_auto_mode({"workerRequired": True, "riskClass": "low"})["resolvedMode"])
        self.assertEqual("simple", resolve_auto_mode({"workerRequired": False, "riskClass": "low"})["resolvedMode"])
        self.assertEqual("simple", resolve_auto_mode({"riskClass": "high"}, requested="simple")["resolvedMode"])

    def test_mode_floor_prevents_downgrade(self):
        value = task()
        value.update({"resolvedMode": "simple", "modeFloor": "standard", "riskClass": "low", "workerRequired": False, "modeReasons": ["old-standard-guarantee"]})
        self.assertIn("mode.downgrade", {item.code for item in validate_task(value, profile()).findings})

        value = task()
        value.update({"requestedMode": "simple", "resolvedMode": "simple", "modeFloor": "simple", "riskClass": "high", "modeReasons": ["explicit-mode-pin"]})
        self.assertIn("mode.pin_unsafe", {item.code for item in validate_task(value, profile()).findings})

    def test_integrated_task_survives_removed_workspace_without_quality(self):
        value = task()
        value.update({"state": "Integrated", "previousState": "Accepted", "baseSha": "base"})
        value["models"]["actual"] = value["models"]["assigned"]
        value["commits"]["candidate"] = "candidate"
        value["execution"]["handoff"] = {"changedPaths": ["owned/file"]}
        value["execution"]["validationEvidence"] = ["tests pass"]
        value["close"] = {
            "mergeCommit": "integrated",
            "integrationEvidence": [{"headSha": "integrated", "command": "tests", "passed": True}],
            "workspaceDisposition": {"workspaceId": "ws-1", "releaseAction": "removed", "releaseReason": "safe-auto", "lastClean": True, "lastHead": "candidate"},
        }
        self.assertTrue(validate_task(value, profile()).ok, validate_task(value, profile()).as_dict())

    def test_invocation_caps_and_stale_result(self):
        value = task()
        value["baseSha"] = "base"
        invocation = derive_context_packet(value, None, "worker", {"profile": profile(), "attempt": 1})
        self.assertTrue(validate_invocation(invocation).ok, validate_invocation(invocation).as_dict())
        too_large = dict(invocation)
        too_large["contextRefs"] = [{"ref": f"p/{i}", "purpose": "x", "contentHash": "h"} for i in range(13)]
        self.assertIn("context.entries", {item.code for item in validate_invocation(too_large).findings})
        result = {"schemaVersion": 4, "invocationId": invocation["invocationId"], "attempt": 1, "status": "succeeded", "candidateHead": "head", "changedPaths": [], "acceptanceEvidence": [], "validationEvidence": [], "findings": [], "blockers": [], "remainingRisks": []}
        self.assertTrue(validate_agent_result(result, invocation, value).ok)
        value["revision"] += 1
        self.assertIn("result.revision", {item.code for item in validate_agent_result(result, invocation, value).findings})

    def test_v4_context_overflow_blocks_instead_of_warning(self):
        value = task()
        value["workingSet"] = [{"ref": f"src/{i}", "purpose": "needed"} for i in range(13)]
        output = handle({"event": "SubagentStart", "task": value, "profile": profile(), "task_name": "lemmings-worker"})
        self.assertEqual("block", output["decision"])

    def test_dependency_ready_batch_and_conflicts(self):
        first, second = task("A"), task("B")
        first["state"] = "Integrated"
        first.update({"previousState": "Accepted", "baseSha": "base"})
        first["models"]["actual"] = first["models"]["assigned"]
        first["commits"]["candidate"] = "head"
        first["execution"].update({"handoff": {}, "validationEvidence": ["ok"]})
        first["close"] = {"mergeCommit": "merge", "integrationEvidence": [{"headSha": "integrated", "command": "tests", "passed": True}], "workspaceDisposition": {"workspaceId": None, "releaseAction": "current", "releaseReason": "external"}}
        second["dependencies"] = ["A"]
        second["parallelReason"] = "independent_paths"
        phase = {"taskDag": [{"taskId": "A", "dependencies": []}, {"taskId": "B", "dependencies": ["A"]}]}
        self.assertEqual(["B"], dispatchable_tasks([first, second], phase)["eligible"])
        checked = validate_batch(Path.cwd(), [first, second], phase, ["B"], profile())
        self.assertTrue(checked.ok, checked.as_dict())


class ModelsAndUsageV4Tests(unittest.TestCase):
    def test_reconfigure_is_stale_safe_and_changes_only_routes(self):
        config = profile()
        catalog = {"hostId": "opencode", "models": [{"providerId": "anthropic", "modelId": "claude", "variants": ["fast", "deep"]}]}
        routes = {"worker": [{"providerId": "anthropic", "modelId": "claude", "variantId": "fast", "specializations": ["default", "tests"]}], "reviewer": [{"providerId": "anthropic", "modelId": "claude", "variantId": "deep"}], "explorer": [{"providerId": "anthropic", "modelId": "claude", "variantId": "fast"}]}
        proposal = build_proposal(config, catalog, routes)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lemmings.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            result = apply_proposal(path, catalog, routes, proposal["proposalDigest"])
            updated = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertEqual(["default", "tests"], updated["modelRoutes"]["opencode"]["worker"][0]["specializations"])
            self.assertEqual(config["workspacePool"], updated["workspacePool"])
            updated["mode"] = "strict"
            path.write_text(json.dumps(updated), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "confirmation digest"):
                apply_proposal(path, catalog, routes, proposal["proposalDigest"])

    def test_opencode_kilo_and_zero_usage_are_exact(self):
        export = {"events": [{"part": {"tokens": {"input": 0, "output": 2, "reasoning": 1, "cache": {"read": 0, "write": 3}}, "cost": 0}}]}
        for host in ("opencode", "kilo"):
            with self.subTest(host=host):
                usage = normalize_usage_export(host, export)
                self.assertTrue(usage["exact"])
                self.assertEqual(0, usage["inputTokens"])
                self.assertEqual(0, usage["cacheReadTokens"])
                self.assertEqual(0, usage["reportedCost"])

    @staticmethod
    def recovery_inputs() -> tuple[list[dict], dict, dict]:
        catalogs = [
            {"hostId": "codex", "models": [
                {"providerId": "openai", "modelId": "gpt-5.6-luna", "variants": ["max", "high"]},
                {"providerId": "openai", "modelId": "gpt-5.6-terra", "variants": ["max"]},
                {"providerId": "openai", "modelId": "gpt-5.6-sol", "variants": ["high"]},
            ]},
            {"hostId": "opencode", "models": [
                {"providerId": "openai-alt", "modelId": "gpt-5.6-luna", "variants": ["max", "high"]},
                {"providerId": "openai-alt", "modelId": "gpt-5.6-sol", "variants": ["high"]},
            ]},
        ]
        failure = {
            "category": "quota_exhausted",
            "invocationId": "inv-1",
            "route": {"hostId": "codex", "providerId": "openai", "modelId": "gpt-5.6-luna", "variantId": "max"},
            "resetAt": "2026-08-22T00:00:00Z",
            "resumable": True,
        }
        impact = {"quality": "same", "cost": "similar", "speed": "continue", "limitations": []}
        plan = {"options": [
            {
                "optionId": "same-model-other-host", "summary": "Use the same model from another host.", "impact": impact,
                "roleRoutes": {
                    "worker": [
                        {"hostId": "opencode", "providerId": "openai-alt", "modelId": "gpt-5.6-luna", "variantId": "max"},
                        {"hostId": "codex", "providerId": "openai", "modelId": "gpt-5.6-terra", "variantId": "max"},
                    ],
                    "reviewer": [{"hostId": "opencode", "providerId": "openai-alt", "modelId": "gpt-5.6-sol", "variantId": "high"}],
                    "explorer": [{"hostId": "opencode", "providerId": "openai-alt", "modelId": "gpt-5.6-luna", "variantId": "high"}],
                },
            },
            {
                "optionId": "alternate-worker", "summary": "Use the configured alternate worker.", "impact": {**impact, "quality": "comparable"},
                "roleRoutes": {
                    "worker": [{"hostId": "codex", "providerId": "openai", "modelId": "gpt-5.6-terra", "variantId": "max"}],
                    "reviewer": [{"hostId": "codex", "providerId": "openai", "modelId": "gpt-5.6-sol", "variantId": "high"}],
                    "explorer": [{"hostId": "codex", "providerId": "openai", "modelId": "gpt-5.6-luna", "variantId": "high"}],
                },
            },
            {
                "optionId": "wait", "kind": "wait", "summary": "Wait for the original quota reset.", "impact": {**impact, "speed": "paused"},
            },
        ]}
        return catalogs, failure, plan

    def test_limit_failure_actions_and_capacity_probe_are_deterministic(self):
        _, failure, _ = self.recovery_inputs()
        self.assertEqual("recover", route_failure_action(failure))
        rate_limit = {**failure, "category": "rate_limited", "retryAfter": 30}
        self.assertEqual("retry", route_failure_action(rate_limit))
        self.assertEqual("recover", route_failure_action(rate_limit, transient_retries=1))
        self.assertEqual("shrink-context", route_failure_action({**failure, "category": "context_limit"}))
        probe = normalize_capacity_probe({"status": "unknown", "route": failure["route"], "remainingTokens": None})
        self.assertEqual("unknown", probe["status"])
        capabilities = {name: True for name in ("isolation", "parallelAgents", "cancellation", "structuredOutput", "usageAccounting", "capacityProbe", "modelCatalog", "toolCallLimits", "approvals")}
        self.assertTrue(validate_host_capabilities({"hostId": "opencode", "capabilities": capabilities}).ok)

    def test_task_local_recovery_is_cross_host_stale_safe_and_advances(self):
        config = profile()
        value = task()
        value["models"]["requested"] = value["models"]["assigned"]
        catalogs, failure, plan = self.recovery_inputs()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "task.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            proposal = build_recovery_proposal(config, value, catalogs, failure, plan)
            original_config = json.dumps(config, sort_keys=True)
            applied = apply_recovery_proposal(path, config, catalogs, failure, plan, "same-model-other-host", proposal["proposalDigest"])
            current = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, applied["revision"])
            self.assertEqual("opencode", current["models"]["hostId"])
            self.assertEqual("openai-alt/gpt-5.6-luna:max", current["models"]["assigned"])
            self.assertEqual(original_config, json.dumps(config, sort_keys=True))
            self.assertTrue(validate_task(current, config).ok, validate_task(current, config).as_dict())

            candidate = json.loads(json.dumps(current))
            candidate.update({"state": "Candidate", "previousState": "Active", "baseSha": "base"})
            candidate["models"]["actual"] = candidate["models"]["assigned"]
            candidate["commits"]["candidate"] = "head"
            candidate["execution"].update({"handoff": {"changedPaths": []}, "validationEvidence": ["ok"]})
            candidate["execution"]["invocations"].append(derive_context_packet(candidate, None, "reviewer", {"profile": config}))
            reviewer = handle({"event": "PreToolUse", "tool_name": "spawn_agent", "task": candidate, "profile": config, "task_name": "lemmings-reviewer", "requestedHostId": "opencode", "requestedModel": "openai-alt/gpt-5.6-sol:high", "reviewHead": "head"})
            wrong_range = handle({"event": "PreToolUse", "tool_name": "spawn_agent", "task": candidate, "profile": config, "task_name": "lemmings-reviewer", "requestedHostId": "opencode", "requestedModel": "openai-alt/gpt-5.6-sol:high", "reviewHead": "other"})
            self.assertEqual("allow", reviewer["decision"])
            self.assertEqual("block", wrong_range["decision"])

            next_failure = {**failure, "invocationId": "inv-2", "route": current["routingRecovery"]["roleRoutes"]["worker"][0]}
            advanced = advance_recovery_route(path, "worker", next_failure, 1)
            current = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("advanced", advanced["action"])
            self.assertEqual("codex", current["models"]["hostId"])
            self.assertEqual("openai/gpt-5.6-terra:max", current["models"]["assigned"])
            self.assertTrue(validate_task(current, config).ok, validate_task(current, config).as_dict())

            exhausted_failure = {**failure, "invocationId": "inv-3", "route": current["routingRecovery"]["roleRoutes"]["worker"][1]}
            exhausted = advance_recovery_route(path, "worker", exhausted_failure, 2)
            current = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("proposal-required", exhausted["action"])
            self.assertEqual("paused", current["routingRecovery"]["status"])
            self.assertTrue(validate_task(current, config).ok, validate_task(current, config).as_dict())

    def test_recovery_wait_blocks_dispatch_and_stale_confirmation_fails(self):
        config = profile()
        value = task()
        catalogs, failure, plan = self.recovery_inputs()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "task.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            proposal = build_recovery_proposal(config, value, catalogs, failure, plan)
            changed = dict(value); changed["revision"] = 1
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "confirmation digest"):
                apply_recovery_proposal(path, config, catalogs, failure, plan, "wait", proposal["proposalDigest"])

            path.write_text(json.dumps(value), encoding="utf-8")
            proposal = build_recovery_proposal(config, value, catalogs, failure, plan)
            apply_recovery_proposal(path, config, catalogs, failure, plan, "wait", proposal["proposalDigest"])
            paused = json.loads(path.read_text(encoding="utf-8"))
            output = handle({"event": "PreToolUse", "tool_name": "spawn_agent", "task": paused, "profile": config, "task_name": "lemmings-worker", "requestedModel": paused["models"]["assigned"]})
            self.assertEqual("block", output["decision"])

    def test_route_failure_can_replace_missing_agent_result(self):
        _, failure, _ = self.recovery_inputs()
        value = task()
        output = handle({"event": "SubagentStop", "task": value, "profile": profile(), "task_name": "lemmings-worker", "routeFailure": failure})
        self.assertEqual("allow", output["decision"])
        self.assertEqual("recover", output["recoveryAction"])
        configured = profile()
        value["execution"]["invocations"].append(derive_context_packet(value, None, "worker", {"profile": configured}))
        common = {"event": "PreToolUse", "tool_name": "spawn_agent", "task": value, "profile": configured, "task_name": "lemmings-worker", "requestedModel": value["models"]["assigned"]}
        unknown = handle({**common, "capacityProbe": {"status": "unknown", "route": failure["route"]}})
        depleted = handle({**common, "capacityProbe": {"status": "depleted", "route": failure["route"]}})
        self.assertEqual("allow", unknown["decision"])
        self.assertEqual("block", depleted["decision"])


class WorkspacePoolV4Tests(unittest.TestCase):
    def add_workspace(self, repo: Path, root: Path, name: str) -> Path:
        path = root / name
        git(repo, "worktree", "add", "-b", name, str(path), "HEAD")
        return path

    def test_same_task_release_and_cross_task_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir(); head = init_repo(repo)
            worktree = self.add_workspace(repo, root, "task-one")
            register_workspace(repo, workspace_id="ws", path=worktree, backend="code-worktree", managed_by="lemmings", lifetime="task", expected_revision=0, task_id="T1")
            claim_workspace(repo, workspace_id="ws", task_id="T1", base_sha=head, integration_head=head, branch="task-one", expected_revision=1)
            released = release_workspace(repo, workspace_id="ws", expected_revision=2, task_state="Integrated", integration_evidence=True)
            self.assertEqual("released-to-pool", released["action"])
            claimed = claim_workspace(repo, workspace_id="ws", task_id="T2", base_sha=head, integration_head=head, branch="task-two", expected_revision=3)
            self.assertEqual("T2", claimed["entry"]["taskId"])
            self.assertFalse(claimed.get("idempotent", False))

    def test_third_idle_workspace_evicts_lru(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir(); head = init_repo(repo)
            revision = 0
            for index in range(3):
                name = f"task-{index}"
                worktree = self.add_workspace(repo, root, name)
                register_workspace(repo, workspace_id=f"ws-{index}", path=worktree, backend="code-worktree", managed_by="lemmings", lifetime="task", expected_revision=revision, task_id=f"T{index}")
                revision += 1
                claim_workspace(repo, workspace_id=f"ws-{index}", task_id=f"T{index}", base_sha=head, integration_head=head, branch=name, expected_revision=revision)
                revision += 1
                release_workspace(repo, workspace_id=f"ws-{index}", expected_revision=revision, task_state="Integrated", integration_evidence=True)
                revision += 1
            registry = load_registry(repo)
            self.assertEqual({"ws-1", "ws-2"}, {entry["workspaceId"] for entry in registry["entries"]})
            self.assertFalse((root / "task-0").exists())

    def test_dirty_failure_is_quarantined_and_never_active_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir(); head = init_repo(repo)
            dirty = self.add_workspace(repo, root, "dirty")
            register_workspace(repo, workspace_id="dirty", path=dirty, backend="code-worktree", managed_by="lemmings", lifetime="task", expected_revision=0, task_id="T")
            claim_workspace(repo, workspace_id="dirty", task_id="T", base_sha=head, integration_head=head, branch="dirty", expected_revision=1)
            (dirty / "untracked.txt").write_text("dirty", encoding="utf-8")
            result = release_workspace(repo, workspace_id="dirty", expected_revision=2, task_state="Integrated", integration_evidence=True)
            self.assertEqual("retained", result["action"])
            self.assertEqual("quarantined", load_registry(repo)["entries"][0]["state"])

            clean = self.add_workspace(repo, root, "never-active")
            register_workspace(repo, workspace_id="fresh", path=clean, backend="code-worktree", managed_by="lemmings", lifetime="task", expected_revision=3)
            removed = release_workspace(repo, workspace_id="fresh", expected_revision=4, task_state="Cancelled", integration_evidence=False)
            self.assertEqual("removed", removed["action"])
            self.assertFalse(clean.exists())

    def test_user_validation_workspace_and_crash_recovery_are_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir(); init_repo(repo)
            worktree = self.add_workspace(repo, root, "validation")
            register_workspace(repo, workspace_id="validation", path=worktree, backend="code-worktree", managed_by="user", lifetime="project", expected_revision=0, kind="validation")
            snapshot = inspect_registry(repo)
            self.assertTrue(worktree.exists())
            self.assertEqual(1, len(snapshot["entries"]))
            result = release_workspace(repo, workspace_id="validation", expected_revision=1, task_state="Integrated", integration_evidence=True, action="remove")
            self.assertEqual("retained", result["action"])
            self.assertTrue(worktree.exists())

    def test_never_active_standalone_unity_clone_is_safely_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir(); init_repo(repo)
            clone = root / "unity-clone"
            subprocess.run(["git", "clone", str(repo), str(clone)], capture_output=True, text=True, check=True)
            register_workspace(repo, workspace_id="clone", path=clone, backend="unity-clone", managed_by="lemmings", lifetime="phase", expected_revision=0)
            result = release_workspace(repo, workspace_id="clone", expected_revision=1, task_state="Cancelled", integration_evidence=False)
            self.assertEqual("removed", result["action"], result)
            self.assertFalse(clone.exists())

    def test_claim_requires_current_registry_revision_and_integration_head(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir(); head = init_repo(repo)
            worktree = self.add_workspace(repo, root, "pooled")
            register_workspace(repo, workspace_id="ws", path=worktree, backend="code-worktree", managed_by="lemmings", lifetime="task", expected_revision=0)
            with self.assertRaisesRegex(ValueError, "integration head"):
                claim_workspace(repo, workspace_id="ws", task_id="T", base_sha=head, integration_head="other", branch="new", expected_revision=1)
            with self.assertRaisesRegex(ValueError, "stale workspace registry"):
                claim_workspace(repo, workspace_id="ws", task_id="T", base_sha=head, integration_head=head, branch="new", expected_revision=0)

    def test_cleanup_code_has_no_force_reset_clean_or_prune(self):
        source = (ROOT / "skills/lemmings/scripts/lemmings/workspace.py").read_text(encoding="utf-8")
        self.assertNotIn('"--force"', source)
        self.assertNotIn('git(repo, "reset"', source)
        self.assertNotIn('git(repo, "clean"', source)
        self.assertNotIn('git(repo, "worktree", "prune"', source)

    def test_repair_oversize_and_remove_failure_dispositions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir(); head = init_repo(repo)

            repair = self.add_workspace(repo, root, "repair")
            register_workspace(repo, workspace_id="repair", path=repair, backend="code-worktree", managed_by="lemmings", lifetime="task", expected_revision=0, task_id="T1")
            claim_workspace(repo, workspace_id="repair", task_id="T1", base_sha=head, integration_head=head, branch="repair", expected_revision=1)
            retained = release_workspace(repo, workspace_id="repair", expected_revision=2, task_state="Repair", integration_evidence=False)
            self.assertEqual("retained", retained["action"])
            self.assertEqual("active", load_registry(repo)["entries"][0]["state"])

            oversized = self.add_workspace(repo, root, "oversized")
            register_workspace(repo, workspace_id="oversized", path=oversized, backend="code-worktree", managed_by="lemmings", lifetime="task", expected_revision=3, task_id="T2", estimated_gib=11)
            claim_workspace(repo, workspace_id="oversized", task_id="T2", base_sha=head, integration_head=head, branch="oversized", expected_revision=4)
            removed = release_workspace(repo, workspace_id="oversized", expected_revision=5, task_state="Integrated", integration_evidence=True)
            self.assertEqual("removed", removed["action"])

            locked = self.add_workspace(repo, root, "locked")
            register_workspace(repo, workspace_id="locked", path=locked, backend="code-worktree", managed_by="lemmings", lifetime="task", expected_revision=6, task_id="T3")
            claim_workspace(repo, workspace_id="locked", task_id="T3", base_sha=head, integration_head=head, branch="locked", expected_revision=7)
            real_git = workspace_module.git
            def fail_remove(at: Path, *args: str):
                if args[:2] == ("worktree", "remove"):
                    return subprocess.CompletedProcess([], 1, "", "file is locked")
                return real_git(at, *args)
            with patch.object(workspace_module, "git", side_effect=fail_remove):
                failed = release_workspace(repo, workspace_id="locked", expected_revision=8, task_state="Integrated", integration_evidence=True, action="remove")
            self.assertEqual("retained", failed["action"])
            entry = next(item for item in load_registry(repo)["entries"] if item["workspaceId"] == "locked")
            self.assertEqual("quarantined", entry["state"])
            self.assertIn("locked", entry["quarantineReason"])

            unfinished = self.add_workspace(repo, root, "unfinished")
            register_workspace(repo, workspace_id="unfinished", path=unfinished, backend="code-worktree", managed_by="lemmings", lifetime="task", expected_revision=9, task_id="T4")
            claim_workspace(repo, workspace_id="unfinished", task_id="T4", base_sha=head, integration_head=head, branch="unfinished", expected_revision=10)
            operation = Path(git(unfinished, "rev-parse", "--git-path", "MERGE_HEAD"))
            operation.parent.mkdir(parents=True, exist_ok=True)
            operation.write_text(head + "\n", encoding="utf-8")
            blocked = release_workspace(repo, workspace_id="unfinished", expected_revision=11, task_state="Integrated", integration_evidence=True)
            self.assertEqual("retained", blocked["action"])
            unfinished_entry = next(item for item in load_registry(repo)["entries"] if item["workspaceId"] == "unfinished")
            self.assertIn("unfinished-git-operation", unfinished_entry["quarantineReason"])


if __name__ == "__main__":
    unittest.main()
