from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/lemmings/scripts"))
from unittest.mock import patch

from lemmings import cli
import lemmings.hooks as hooks_module
from lemmings.contracts import ValidationResult, runtime_marker, validate_phase, validate_profile, validate_repository_evidence, validate_review, validate_task
from lemmings.hooks import derive_context_packet, handle, hydrate, is_read_only_shell

ROOT = Path(__file__).resolve().parents[1]


def profile() -> dict:
    return {
        "schemaVersion": 4,
        "distributionVersion": "4.1.0",
        "mode": "auto",
        "modelRoutes": {"codex": {
            "worker": [{"providerId": "openai", "modelId": "gpt-5.6-luna", "variantId": "max"}],
            "reviewer": [{"providerId": "openai", "modelId": "gpt-5.6-sol", "variantId": "high"}],
            "explorer": [{"providerId": "openai", "modelId": "gpt-5.6-luna", "variantId": "high"}],
        }},
        "contextPolicy": {"maxPacketBytes": 16384, "maxWorkingSetItems": 12, "maxExpansions": 1},
        "orchestration": {"maxDelegationDepth": 1, "maxConcurrentWriters": 2, "maxConcurrentReaders": 2, "managerSlots": 1, "maxRepairs": 1, "maxTransportRetries": 1},
        "workspacePool": {"enabled": True, "maxIdle": 2, "maxIdleGiB": 10, "eviction": "lru"},
        "taskGlobs": ["docs/tasks/**/*.json"],
    }


def task(*, mode: str = "standard", state: str = "Ready", task_id: str = "T-1") -> dict:
    value = json.loads((ROOT / "skills" / "lemmings" / "templates" / "task.json").read_text(encoding="utf-8"))
    value.update({"taskId": task_id, "requestedMode": mode, "resolvedMode": mode, "modeFloor": mode, "state": state})
    value["modeReasons"] = ["explicit-mode-pin"]
    value["riskClass"] = "low" if mode == "simple" else "medium"
    value["workerRequired"] = mode != "simple"
    return value


def init_repo(path: Path) -> None:
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)


class SchemaOnlyTests(unittest.TestCase):
    def test_v2_artifacts_have_one_breaking_error(self):
        expected = "schemaVersion 2 is unsupported by Lemmings 4.0; replace the legacy bundle"
        for validator, value in ((validate_profile, {"schemaVersion": 2}), (validate_task, {"schemaVersion": 2}), (validate_phase, {"schemaVersion": 2}), (validate_review, {"schemaVersion": 2})):
            with self.subTest(validator=validator.__name__):
                checked = validator(value)
                self.assertEqual(1, len(checked.findings))
                self.assertEqual(expected, checked.findings[0].message)

    def test_distribution_versions_are_33(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("4.1.0", package["version"])
        self.assertEqual("4.1.0", plugin["version"])

    def test_specialization_is_optional_hint_and_cross_review_degrades(self):
        configured = profile()
        configured["modelRoutes"]["codex"]["worker"][0]["specializations"] = ["default", "frontend"]
        configured["modelRoutes"]["codex"]["worker"].append({"providerId": "openai", "modelId": "gpt-5.6-terra", "variantId": "max", "specializations": ["default"]})
        value = task()
        value["specialization"] = "web-ui"
        value["models"]["assigned"] = "openai/gpt-5.6-terra:max"
        self.assertTrue(validate_profile(configured).ok)
        self.assertTrue(validate_task(value, configured).ok)

        accepted = task(state="Accepted")
        accepted.update({"previousState": "Candidate", "baseSha": "base", "reviewPolicy": "single", "reviewRef": "reviews/primary.json"})
        accepted["commits"]["candidate"] = "head"
        accepted["models"]["actual"] = accepted["models"]["assigned"]
        accepted["execution"]["validationEvidence"] = ["ok"]
        accepted["capabilityDegradations"] = ["cross-review-unavailable"]
        self.assertTrue(validate_task(accepted, configured).ok, validate_task(accepted, configured).as_dict())

    def test_cross_review_requires_distinct_provider_model_identities(self):
        configured = profile()
        configured["modelRoutes"]["codex"]["reviewer"] = [
            {"providerId": "openai", "modelId": "gpt-5.6-sol", "variantId": "high"},
            {"providerId": "openai", "modelId": "gpt-5.6-terra", "variantId": "high"},
        ]
        accepted = task(state="Accepted")
        accepted.update({"previousState": "Candidate", "baseSha": "base", "reviewPolicy": "cross", "reviewRef": "reviews/primary.json", "crossReviewRefs": ["reviews/secondary.json"], "reviewHistory": ["reviews/primary.json", "reviews/secondary.json"]})
        accepted["commits"]["candidate"] = "head"
        accepted["models"]["actual"] = accepted["models"]["assigned"]
        accepted["execution"]["validationEvidence"] = ["ok"]

        def evidence(review_id: str, model: str) -> dict:
            return {"schemaVersion": 4, "revision": 0, "reviewId": review_id, "subject": {"kind": "candidate", "taskId": accepted["taskId"], "baseSha": "base", "headSha": "head"}, "status": "Accepted", "hostId": "codex", "reviewerModel": model, "cycle": 1, "findings": [], "validation": []}

        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo)
            (repo / "reviews").mkdir()
            (repo / "reviews/primary.json").write_text(json.dumps(evidence("R1", "openai/gpt-5.6-sol:high")), encoding="utf-8")
            (repo / "reviews/secondary.json").write_text(json.dumps(evidence("R2", "openai/gpt-5.6-terra:high")), encoding="utf-8")
            primary = evidence("R1", "openai/gpt-5.6-sol:high"); primary["_evidencePath"] = "reviews/primary.json"
            self.assertTrue(validate_repository_evidence(repo, accepted, None, primary, configured).ok)
            (repo / "reviews/secondary.json").write_text(json.dumps(evidence("R2", "openai/gpt-5.6-sol:low")), encoding="utf-8")
            self.assertIn("review.cross_models", {item.code for item in validate_repository_evidence(repo, accepted, None, primary, configured).findings})


class RuntimeTests(unittest.TestCase):
    def _repo(self, root: Path, value: dict) -> Path:
        repo = root / "repo"
        repo.mkdir()
        init_repo(repo)
        config = repo / ".agents" / "lemmings.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps(profile()), encoding="utf-8")
        packet = repo / "docs" / "tasks" / "task.json"
        packet.parent.mkdir(parents=True)
        packet.write_text(json.dumps(value), encoding="utf-8")
        return repo

    def test_activate_status_and_deactivate_use_relative_v4_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(Path(temp), task())
            with redirect_stdout(StringIO()):
                self.assertEqual(0, cli.main(["runtime", "activate", "--repo", str(repo), "--task", "docs/tasks/task.json"]))
                self.assertEqual(0, cli.main(["runtime", "status", "--repo", str(repo)]))
            marker = runtime_marker(repo)
            self.assertEqual({"schemaVersion": 4, "profilePath": ".agents/lemmings.json", "taskPaths": ["docs/tasks/task.json"]}, json.loads(marker.read_text(encoding="utf-8")))
            with redirect_stdout(StringIO()):
                self.assertEqual(0, cli.main(["runtime", "deactivate", "--repo", str(repo)]))
                self.assertEqual(0, cli.main(["runtime", "deactivate", "--repo", str(repo)]))
            self.assertFalse(marker.exists())

    def test_simple_does_not_create_marker_and_v2_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(Path(temp), task(mode="simple"))
            with redirect_stdout(StringIO()):
                self.assertEqual(0, cli.main(["runtime", "activate", "--repo", str(repo), "--task", "docs/tasks/task.json"]))
            self.assertFalse(runtime_marker(repo).exists())
            (repo / "docs" / "tasks" / "task.json").write_text(json.dumps({"schemaVersion": 2}), encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(1, cli.main(["runtime", "activate", "--repo", str(repo), "--task", "docs/tasks/task.json"]))

    def test_hydrate_rejects_legacy_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            init_repo(repo)
            marker = runtime_marker(repo)
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"schemaVersion": 2}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "replace the legacy bundle"):
                hydrate({"cwd": str(repo)})


class HookPolicyTests(unittest.TestCase):
    def test_declared_validation_command_is_allowed_for_worker(self):
        value = task(mode="strict")
        value["validation"]["commands"] = ["python -m pytest -q"]
        payload = {"event": "PreToolUse", "toolName": "exec_command", "task": value, "task_name": "lemmings-worker", "toolInput": {"command": "python -m pytest -q"}}
        self.assertEqual("allow", handle(payload)["decision"])

    def test_unknown_patch_paths_block_strict_and_warn_standard(self):
        strict = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": task(mode="strict"), "task_name": "lemmings-worker", "toolInput": {"patch": "not a recognized patch"}})
        standard = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": task(), "task_name": "lemmings-worker", "toolInput": {"patch": "not a recognized patch"}})
        self.assertEqual("block", strict["decision"])
        self.assertEqual("warn", standard["decision"])

    def test_known_patch_checks_every_path(self):
        value = task(mode="strict")
        value["ownership"] = {"owned": ["lemmings/**"], "shared": [], "forbidden": ["forbidden/**"]}
        result = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": value, "task_name": "lemmings-worker", "toolInput": {"patch": "*** Update File: lemmings/ok.py\n*** Update File: forbidden/no.py\n"}})
        self.assertEqual("block", result["decision"])

    def test_reviewer_and_explorer_are_read_only(self):
        for role in ("reviewer", "explorer"):
            with self.subTest(role=role):
                result = handle({"event": "PreToolUse", "toolName": "apply_patch", "task": task(), "task_name": f"lemmings-{role}", "toolInput": {"patch": "*** Update File: x\n"}})
                self.assertEqual("block", result["decision"])

    def test_cross_review_uses_distinct_models_across_hosts_and_degrades_openly(self):
        value = task(state="Candidate")
        value.update({"previousState": "Active", "baseSha": "base", "reviewPolicy": "cross"})
        value["commits"]["candidate"] = "head"
        value["models"]["actual"] = value["models"]["assigned"]
        value["execution"]["validationEvidence"] = ["ok"]
        configured = profile()
        configured["modelRoutes"]["opencode"] = {
            "worker": [{"providerId": "openai-alt", "modelId": "gpt-5.6-luna", "variantId": "max"}],
            "reviewer": [{"providerId": "openai-alt", "modelId": "gpt-5.6-sol", "variantId": "high"}],
            "explorer": [{"providerId": "openai-alt", "modelId": "gpt-5.6-luna", "variantId": "high"}],
        }
        value["execution"]["invocations"].append(derive_context_packet(value, None, "reviewer", {"profile": configured}))
        cross = handle({"event": "PreToolUse", "tool_name": "spawn_agent", "task": value, "profile": configured, "task_name": "lemmings-reviewer", "requestedHostId": "codex", "requestedModel": "openai/gpt-5.6-sol:high", "reviewHead": "head"})
        self.assertEqual("allow", cross["decision"])
        self.assertNotIn("capabilityDegradation", cross)
        single_profile = profile()
        single_value = json.loads(json.dumps(value))
        single_value["execution"]["invocations"] = [derive_context_packet(single_value, None, "reviewer", {"profile": single_profile})]
        single = handle({"event": "PreToolUse", "tool_name": "spawn_agent", "task": single_value, "profile": single_profile, "task_name": "lemmings-reviewer", "requestedHostId": "codex", "requestedModel": "openai/gpt-5.6-sol:high", "reviewHead": "head"})
        self.assertEqual("cross-review-unavailable", single.get("capabilityDegradation"))

    def test_candidate_no_longer_requires_handoff(self):
        value = task(state="Candidate")
        value.update({"previousState": "Active", "baseSha": "base"})
        value["commits"]["candidate"] = "head"
        value["models"]["actual"] = value["models"]["assigned"]
        value["execution"]["validationEvidence"] = ["ok"]
        self.assertTrue(validate_task(value, profile()).ok, validate_task(value, profile()).as_dict())

    def test_hook_launcher_never_hides_invalid_input_as_success(self):
        output = StringIO()
        with patch("sys.stdin", StringIO("{invalid")), redirect_stdout(output):
            self.assertEqual(1, hooks_module.main())
        self.assertIn("invalid Lemmings hook input", output.getvalue())

        output = StringIO()
        payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": str(ROOT), "tool_name": "spawn_agent"})
        with patch("sys.stdin", StringIO(payload)), patch.object(hooks_module, "hydrate", side_effect=ValueError("broken launcher")), redirect_stdout(output):
            self.assertEqual(0, hooks_module.main())
        self.assertEqual("deny", json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"])

    def test_agent_result_paths_must_match_actual_candidate_diff(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            init_repo(repo)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "tests@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Tests"], check=True)
            source = repo / "owned.txt"
            source.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "owned.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
            base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            source.write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "commit", "-am", "candidate"], check=True, capture_output=True)
            head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            value = task(state="Candidate")
            value.update({"baseSha": base, "previousState": "Active"})
            value["ownership"] = {"owned": ["owned.txt"], "shared": [], "forbidden": []}
            value["commits"]["candidate"] = head
            invocation = derive_context_packet(value, {}, "worker", {"profile": profile()})
            value["execution"]["invocations"].append(invocation)
            result = {"schemaVersion": 4, "invocationId": invocation["invocationId"], "attempt": invocation["attempt"], "status": "succeeded", "candidateHead": head, "changedPaths": ["wrong.txt"], "acceptanceEvidence": [], "validationEvidence": [], "findings": [], "blockers": [], "remainingRisks": []}
            payload = {"event": "SubagentStop", "cwd": str(repo), "task": value, "profile": profile(), "task_name": "lemmings-worker", "agentInvocation": invocation, "agentResult": result}
            self.assertEqual("block", handle(payload)["decision"])
            result["changedPaths"] = ["owned.txt"]
            self.assertEqual("allow", handle(payload)["decision"])


class ShellClassificationTests(unittest.TestCase):
    def test_git_queries_distinguish_mutation(self):
        for command in ("git status", "git branch --show-current", "git remote -v", "git tag --list release-*"):
            self.assertTrue(is_read_only_shell(command), command)
        for command in ("git branch -D stale", "git remote add origin https://example.invalid/repo", "git tag -d old"):
            self.assertFalse(is_read_only_shell(command), command)

    def test_common_powershell_pipeline_is_static(self):
        self.assertTrue(is_read_only_shell("Get-Content data.json | ConvertFrom-Json | Where-Object active | Sort-Object name | Measure-Object | Format-Table"))
        for command in ("Get-Content data.json | ForEach-Object { Remove-Item $_ }", "Invoke-Expression 'Get-Content data.json'", "[scriptblock]::Create('Get-Content data.json')", "Get-Content $(Get-Location)/data.json"):
            self.assertFalse(is_read_only_shell(command), command)

    def test_quotes_do_not_hide_executable_rg_options(self):
        self.assertTrue(is_read_only_shell("Get-Content 'notes (final).md'", dialect="windows"))
        self.assertTrue(is_read_only_shell("rg '(TODO|FIXME)' lemmings", dialect="windows"))
        for command in ("rg --pr'e' processor TODO .", 'rg --pr"e" processor TODO .', "rg --pre$null processor TODO ."):
            self.assertFalse(is_read_only_shell(command, dialect="windows"), command)

    def test_unclosed_quotes_variables_and_redirection_are_unknown(self):
        rejected = (
            "Get-Content 'notes (final).md", 'Get-Content "notes (final).md',
            "Get-Content $env:TEMP", "Get-Content ${dynamicPath}",
            "Get-Content package.json>out", "Get-Content package.json 2>&1", "Get-Content <in",
            "Get-Content package.json & git reset --hard",
        )
        for command in rejected:
            with self.subTest(command=command):
                self.assertFalse(is_read_only_shell(command, dialect="windows"))

    def test_non_git_allowlist_rejects_execution_options(self):
        for command in ("find . -delete", "find . -exec git reset --hard", "rg --pre processor TODO .", "rg --pre-glob '*.zip' TODO .", "rg --hostname-bin=helper TODO .", "ForEach-Object -MemberName Delete"):
            self.assertFalse(is_read_only_shell(command), command)
        self.assertTrue(is_read_only_shell("rg TODO lemmings"))

    def test_posix_scanner_validates_all_segments(self):
        for command in ("rg TODO lemmings | head", "rg TODO lemmings || head", "rg TODO lemmings && git status"):
            self.assertTrue(is_read_only_shell(command, dialect="posix"), command)
        for command in ("rg $(generator) .", "rg `generator` .", "rg TODO >out", "rg TODO & git status", "rg TODO lemmings | git reset --hard", "rg TODO lemmings && custom-tool run"):
            self.assertFalse(is_read_only_shell(command, dialect="posix"), command)


class CheckEfficiencyTests(unittest.TestCase):
    def test_check_all_calls_each_task_once_and_wave_once(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            init_repo(repo)
            config = repo / ".agents" / "lemmings.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps(profile()), encoding="utf-8")
            paths = []
            for task_id in ("A", "B"):
                path = repo / f"{task_id}.json"
                path.write_text(json.dumps(task(task_id=task_id)), encoding="utf-8")
                paths.append(str(path))
            phase_path = repo / "phase.json"
            phase_path.write_text(json.dumps({"schemaVersion": 4}), encoding="utf-8")
            args = argparse.Namespace(repo=str(repo), profile=None, task=paths, phase=str(phase_path), review=None, all=True, distribution=False, dispatchable=False, batch=None)
            calls = []
            with patch.object(cli, "check_task_repository", side_effect=lambda *values, **kwargs: calls.append(values[2]["taskId"]) or ValidationResult()), patch.object(cli, "validate_wave", return_value=ValidationResult()) as wave:
                with redirect_stdout(StringIO()):
                    self.assertEqual(0, cli.command_check(args))
            self.assertEqual(["A", "B"], calls)
            self.assertEqual(1, wave.call_count)

    def test_distribution_check_is_explicit(self):
        parser = cli.build_parser()
        self.assertFalse(parser.parse_args(["check"]).distribution)
        self.assertTrue(parser.parse_args(["check", "--distribution"]).distribution)


if __name__ == "__main__":
    unittest.main()
