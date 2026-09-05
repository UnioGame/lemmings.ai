from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/lemmings/scripts"))
from unittest.mock import patch

from lemmings import hooks
from lemmings.contracts import runtime_marker, validate_task
from lemmings.quality import build_quality_report, summarize_quality
from lemmings.telemetry import (
    annotate_regression,
    bind_run,
    build_report,
    cleanup_events,
    enter_stage,
    finish_run,
    import_quality,
    iso_timestamp,
    iter_events,
    parse_timestamp,
    read_binding,
    record_event,
    record_hook_event,
    set_telemetry_mode,
    summarize_task,
    telemetry_root,
    telemetry_status,
)


ROOT = Path(__file__).resolve().parents[1]


def init_repo(path: Path) -> None:
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [os.sys.executable, str(ROOT / "skills/lemmings/scripts/run.py"), *args], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )


def task(state: str = "Active", cohort: str | None = "feature-small") -> dict:
    value = json.loads((ROOT / "skills" / "lemmings" / "templates" / "task.json").read_text(encoding="utf-8"))
    value.update({"taskId": "TASK-17", "goal": "measure delivery", "acceptance": ["report is complete"], "state": state, "baseSha": "base"})
    value["ownership"] = {"owned": ["lemmings/**"], "shared": [], "forbidden": []}
    value["workingSet"] = [{"ref": "lemmings/telemetry.py#record_event", "purpose": "telemetry contract"}]
    value["models"]["actual"] = value["models"]["assigned"]
    value["commits"]["candidate"] = "head"
    value["execution"]["validationEvidence"] = [{"passed": True}]
    value["reviewRef"] = "reviews/TASK-17.json"
    if cohort:
        value["telemetryCohort"] = cohort
    return value


def quality() -> dict:
    return {
        "schemaVersion": 4,
        "taskId": "TASK-17",
        "baseSha": "base",
        "headSha": "head",
        "recordedAt": iso_timestamp(),
        "signals": [{
            "name": "line-coverage", "category": "coverage", "baseline": 74.2,
            "value": 76.1, "unit": "percent", "direction": "higher-better",
            "threshold": 74.2, "status": "pass", "sourceRef": "ci/build/123",
        }],
    }


class TelemetryTests(unittest.TestCase):
    def test_quality_summary_aggregates_attempts_reviews_and_escalation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo)
            reviews = repo / "docs/tasks/reviews"; reviews.mkdir(parents=True)
            first_ref = "docs/tasks/reviews/TASK-17-1.json"
            second_ref = "docs/tasks/reviews/TASK-17-2.json"
            (repo / first_ref).write_text(json.dumps({"status": "ChangesRequested", "findings": [{"priority": "P1", "origin": "implementation"}, {"priority": "P2", "origin": "plan-contract"}]}), encoding="utf-8")
            (repo / second_ref).write_text(json.dumps({"status": "Accepted", "findings": [{"priority": "P3", "origin": "implementation"}]}), encoding="utf-8")
            current = task("Integrated")
            current["execution"]["attempts"] = [
                {"attempt": 1, "kind": "candidate", "actualModel": "gpt-5.6-luna:max", "headSha": "head", "validationFailures": 1, "reviewRef": first_ref, "reviewStatus": "ChangesRequested"},
                {"attempt": 2, "kind": "fix", "actualModel": "gpt-5.6-terra:max", "headSha": "fix", "validationFailures": 0, "reviewRef": second_ref, "reviewStatus": "Accepted"},
            ]
            current["reviewHistory"] = [first_ref, second_ref]
            current["reviewRef"] = second_ref
            summary = summarize_quality(repo, current, "completed")
            self.assertTrue(summary["complete"])
            self.assertFalse(summary["firstPassAccepted"])
            self.assertEqual(1, summary["repairCycles"])
            self.assertEqual(1, summary["workerModelChanges"])
            self.assertEqual(1, summary["validationFailures"])
            self.assertEqual(1, summary["findings"]["implementation"]["P1"])
            self.assertEqual(1, summary["findings"]["plan-contract"]["P2"])
            self.assertTrue(summary["workerRouteEscalated"])

    def test_cross_reviews_same_head_and_cycle_count_once(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo)
            reviews = repo / "docs/tasks/reviews"; reviews.mkdir(parents=True)
            primary = "docs/tasks/reviews/TASK-17-primary.json"
            secondary = "docs/tasks/reviews/TASK-17-secondary.json"
            for reference, review_id in ((primary, "R1"), (secondary, "R2")):
                (repo / reference).write_text(json.dumps({"reviewId": review_id, "status": "Accepted", "cycle": 1, "subject": {"headSha": "head"}, "findings": []}), encoding="utf-8")
            current = task("Integrated")
            current["reviewRef"] = primary
            current["crossReviewRefs"] = [secondary]
            current["reviewHistory"] = [primary, secondary]
            current["execution"]["attempts"] = [{"attempt": 1, "kind": "candidate", "actualModel": "gpt-5.6-luna:max", "headSha": "head", "validationFailures": 0, "reviewRef": primary, "reviewStatus": "Accepted"}]
            summary = summarize_quality(repo, current, "completed")
            self.assertEqual(1, summary["reviewCycles"])
            self.assertEqual(0, summary["repeatedReviews"])
            self.assertTrue(summary["firstPassAccepted"])

    def test_metrics_finish_does_not_mutate_task_with_telemetry_off(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo)
            packets = repo / "docs/tasks"; reviews = packets / "reviews"
            reviews.mkdir(parents=True)
            review_ref = "docs/tasks/reviews/TASK-17.json"
            (repo / review_ref).write_text(json.dumps({"status": "Accepted", "findings": []}), encoding="utf-8")
            current = task("Integrated")
            current["execution"]["attempts"] = [{"attempt": 1, "kind": "candidate", "actualModel": "gpt-5.6-luna:max", "headSha": "head", "validationFailures": 0, "reviewRef": review_ref, "reviewStatus": "Accepted"}]
            current["reviewHistory"] = [review_ref]
            current["reviewRef"] = review_ref
            packet = packets / "TASK-17.json"; packet.write_text(json.dumps(current), encoding="utf-8")
            process = run_cli("metrics", "finish", "--repo", str(repo), "--task", str(packet), "--outcome", "completed")
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            output = json.loads(process.stdout)
            self.assertFalse(output["recorded"])
            self.assertNotIn("taskQuality", output)
            self.assertNotIn("qualitySummary", json.loads(packet.read_text(encoding="utf-8")))

    def test_incomplete_task_is_reported_but_not_comparable(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo)
            packets = repo / "docs/tasks"; packets.mkdir(parents=True)
            incomplete = task("Integrated")
            (packets / "incomplete.json").write_text(json.dumps(incomplete), encoding="utf-8")
            report = build_quality_report(repo, {"taskGlobs": ["docs/tasks/*.json"]})
            self.assertEqual(1, report["incompleteTasks"])
            self.assertEqual([], report["comparison"]["recommendations"])

    def test_routing_recommendation_waits_for_five_integrated_tasks_per_model(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo)
            packets = repo / "docs/tasks"; reviews = packets / "reviews"
            reviews.mkdir(parents=True)
            for model_index, model in enumerate(("gpt-5.6-luna:max", "gpt-5.6-terra:max")):
                for index in range(5):
                    task_id = f"TASK-{model_index}-{index}"
                    review_ref = f"docs/tasks/reviews/{task_id}.json"
                    (repo / review_ref).write_text(json.dumps({"status": "Accepted", "findings": []}), encoding="utf-8")
                    current = task("Integrated")
                    current.update({"taskId": task_id, "telemetryCohort": "same", "reviewRef": review_ref, "reviewHistory": [review_ref]})
                    current["models"] = {"requested": None, "assigned": model, "actual": model}
                    current["execution"]["attempts"] = [{"attempt": 1, "kind": "candidate", "actualModel": model, "headSha": "head", "validationFailures": 0, "reviewRef": review_ref, "reviewStatus": "Accepted"}]
                    (packets / f"{task_id}.json").write_text(json.dumps(current), encoding="utf-8")
            report = build_quality_report(repo, {"taskGlobs": ["docs/tasks/*.json"]})
            self.assertEqual("compare-routing", report["comparison"]["recommendations"][0]["recommendation"])
            (packets / "TASK-1-4.json").unlink()
            report = build_quality_report(repo, {"taskGlobs": ["docs/tasks/*.json"]})
            self.assertEqual([], report["comparison"]["recommendations"])

    def test_telemetry_cohort_contract_is_optional_but_typed(self):
        value = task(); value["ownership"] = {"owned": [], "shared": [], "forbidden": []}
        self.assertNotIn("telemetry.cohort", {item.code for item in validate_task(value).findings})
        value["telemetryCohort"] = []
        self.assertIn("telemetry.cohort", {item.code for item in validate_task(value).findings})

    def test_public_cli_controls_lifecycle_and_report(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo)
            enabled = run_cli("metrics", "basic", "--repo", str(repo))
            staged = run_cli("metrics", "stage", "discover", "--repo", str(repo), "--task", "TASK-17")
            finished = run_cli("metrics", "finish", "--repo", str(repo), "--task", "TASK-17", "--outcome", "completed")
            report = run_cli("metrics", "report", "--repo", str(repo), "--task", "TASK-17")
            for process in (enabled, staged, finished, report):
                self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            self.assertEqual("basic", json.loads(enabled.stdout)["mode"])
            self.assertEqual("unsupported", json.loads(report.stdout)["capabilities"]["tokenUsage"])

    def test_off_does_not_record_events(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo)
            result = enter_stage(repo, repo, "discover", task=task())
            self.assertFalse(result["recorded"])
            self.assertEqual([], list(iter_events(repo)))

    def test_stage_transition_is_idempotent_and_report_has_wall_time(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            first = enter_stage(repo, repo, "discover", task=task())
            repeated = enter_stage(repo, repo, "discover", task=task())
            enter_stage(repo, repo, "plan", task=task())
            finish_run(repo, repo, "completed", task=task("Integrated"), event_type="task.integrated")
            report = build_report(repo, task_id="TASK-17")
            self.assertTrue(first["recorded"])
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(1, report["performance"]["finishedRuns"])
            self.assertIsNotNone(report["performance"]["medianLeadTimeSeconds"])
            self.assertEqual("unsupported", report["capabilities"]["tokenUsage"])

    def test_duplicate_hook_event_is_deduplicated_and_private(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            payload = {
                "hook_event_name": "PreToolUse", "cwd": str(repo), "session_id": "s1",
                "turn_id": "t1", "tool_use_id": "tool-1", "tool_name": "shell_command",
                "tool_input": {"command": "print SUPER_SECRET"},
            }
            record_hook_event(repo, payload, {"decision": "allow"})
            record_hook_event(repo, payload, {"decision": "allow"})
            events = list(iter_events(repo))
            self.assertEqual(1, len(events))
            serialized = json.dumps(events)
            self.assertNotIn("SUPER_SECRET", serialized)
            self.assertNotIn(str(repo), serialized)

    def test_full_import_validates_contract_and_sha_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            result = import_quality(repo, repo, quality(), "TASK-17", task())
            self.assertTrue(result["recorded"])
            invalid = quality(); invalid["headSha"] = "stale"
            with self.assertRaisesRegex(ValueError, "headSha"):
                import_quality(repo, repo, invalid, "TASK-17", task())
            secret = quality(); secret["signals"][0]["sourceRef"] = "https://user:password@example.invalid/build"
            with self.assertRaisesRegex(ValueError, "secret-like"):
                import_quality(repo, repo, secret, "TASK-17", task())
            extra = quality(); extra["rawLog"] = "SUPER_SECRET_LOG"; extra["signals"][0]["toolOutput"] = "PRIVATE_OUTPUT"
            import_quality(repo, repo, extra, "TASK-17", task())
            stored = json.dumps(list(iter_events(repo)))
            self.assertNotIn("SUPER_SECRET_LOG", stored)
            self.assertNotIn("PRIVATE_OUTPUT", stored)

    def test_import_by_task_id_uses_unique_local_packet_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            packet = repo / "TASK-17.json"; packet.write_text(json.dumps(task()), encoding="utf-8")
            observation = repo / "quality.json"; observation.write_text(json.dumps(quality()), encoding="utf-8")
            bind_run(repo, repo, task_id="TASK-17", task_path=str(packet))
            process = run_cli("metrics", "import", "--repo", str(repo), "--task", "TASK-17", "--file", str(observation))
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)

    def test_regression_requires_integration_and_reports_detection_and_resolution(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            with self.assertRaisesRegex(ValueError, "Integrated"):
                annotate_regression(repo, repo, task_id="TASK-17", kind="escaped-defect", severity="P1", relation="confirmed", reference="BUG-1")
            enter_stage(repo, repo, "verify", task=task())
            finish_run(repo, repo, "completed", task=task("Integrated"), event_type="task.integrated")
            integrated = next(event for event in iter_events(repo) if event["type"] == "task.integrated")["timestampUtc"]
            detected = iso_timestamp(parse_timestamp(integrated) + timedelta(seconds=5))
            resolved = iso_timestamp(parse_timestamp(integrated) + timedelta(seconds=12))
            annotate_regression(repo, repo, task_id="TASK-17", kind="escaped-defect", severity="P1", relation="confirmed", reference="BUG-1", detected_at=detected)
            annotate_regression(repo, repo, task_id="TASK-17", kind="regression-resolved", severity="P1", relation="confirmed", reference="BUG-1", resolved_at=resolved, fix_commit="fix")
            regression = build_report(repo)["regressions"]["confirmed"][0]
            self.assertEqual(5, regression["timeToDetectionSeconds"])
            self.assertEqual(7, regression["timeToResolutionSeconds"])

    def test_suspected_regression_does_not_count_as_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            enter_stage(repo, repo, "verify", task=task())
            finish_run(repo, repo, "completed", task=task("Integrated"), event_type="task.integrated")
            annotate_regression(repo, repo, task_id="TASK-17", kind="revert", severity="P2", relation="suspected", reference="REV-1")
            report = build_report(repo)
            self.assertEqual([], report["regressions"]["confirmed"])
            self.assertEqual(1, len(report["regressions"]["suspected"]))

    def test_parallel_worktrees_have_distinct_bindings(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            left = repo / "left"; right = repo / "right"; left.mkdir(); right.mkdir(); init_repo(left); init_repo(right)
            one = bind_run(repo, left, task_id="TASK-1")
            two = bind_run(repo, right, task_id="TASK-2")
            self.assertNotEqual(one["runId"], two["runId"])
            self.assertEqual("TASK-1", read_binding(repo, left)["taskId"])
            self.assertEqual("TASK-2", read_binding(repo, right)["taskId"])

    def test_nested_cwd_resolves_the_worktree_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            nested = repo / "src" / "nested"; nested.mkdir(parents=True)
            expected = bind_run(repo, repo, task_id="TASK-17")
            self.assertEqual(expected["runId"], read_binding(repo, nested)["runId"])

    def test_active_hook_runtime_ignores_telemetry_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            first = repo / "TASK-1.json"; second = repo / "TASK-2.json"
            first.write_text(json.dumps({**task(), "taskId": "TASK-1"}), encoding="utf-8")
            second.write_text(json.dumps({**task(), "taskId": "TASK-2"}), encoding="utf-8")
            marker = runtime_marker(repo); marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"schemaVersion": 4, "taskPaths": ["TASK-1.json"]}), encoding="utf-8")
            bind_run(repo, repo, task_id="TASK-2", task_path=str(second))
            hydrated = hooks.hydrate({"cwd": str(repo), "hook_event_name": "SubagentStart"})
            self.assertEqual("TASK-1", hydrated["task"]["taskId"])
            self.assertNotIn("_telemetryTask", hydrated)

    def test_unbound_hook_is_visible_as_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            record_hook_event(repo, {"hook_event_name": "SessionStart", "cwd": str(repo), "session_id": "s1"})
            report = build_report(repo)
            self.assertEqual(1, report["completeness"]["unboundEvents"])
            self.assertFalse(report["completeness"]["complete"])

    def test_finished_binding_does_not_capture_later_hook_events(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            enter_stage(repo, repo, "verify", task=task())
            finish_run(repo, repo, "completed", task=task("Integrated"), event_type="task.integrated")
            record_hook_event(repo, {"hook_event_name": "SessionStart", "cwd": str(repo), "session_id": "later"})
            later = next(item for item in iter_events(repo) if item.get("sessionId") == "later")
            self.assertNotIn("taskId", later)
            self.assertTrue(later["data"]["unbound"])

    def test_cleanup_is_dry_run_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            record_event(repo, "session.started", source="hook", cwd=repo, timestamp="2020-01-01T00:00:00Z")
            event_file = next((telemetry_root(repo) / "events").rglob("*.json"))
            os.utime(event_file, (1, 1))
            inspected = cleanup_events(repo, "90d")
            self.assertFalse(inspected["executed"])
            self.assertTrue(event_file.exists())
            cleanup_events(repo, "90d", True)
            self.assertFalse(event_file.exists())

    def test_hooks_do_not_write_telemetry(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            raw = {"hook_event_name": "Stop", "cwd": str(repo), "session_id": "s1", "turn_id": "t1"}
            stdout = io.StringIO()
            with patch("sys.stdin", io.StringIO(json.dumps(raw))), redirect_stdout(stdout):
                self.assertEqual(0, hooks.main())
            self.assertEqual({}, json.loads(stdout.getvalue()))
            self.assertIsNone(telemetry_status(repo)["lastError"])
            self.assertFalse(hasattr(hooks, "record_hook_event"))

    def test_malformed_telemetry_task_binding_cannot_block_inactive_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            malformed = repo / "task.json"; malformed.write_text("{broken", encoding="utf-8")
            bind_run(repo, repo, task_id="TASK-17", task_path=str(malformed))
            stdout = io.StringIO()
            raw = {"hook_event_name": "Stop", "cwd": str(repo), "session_id": "s1", "turn_id": "t1"}
            with patch("sys.stdin", io.StringIO(json.dumps(raw))), redirect_stdout(stdout):
                self.assertEqual(0, hooks.main())
            self.assertEqual({}, json.loads(stdout.getvalue()))
            self.assertIsNone(telemetry_status(repo)["lastError"])

    def test_analysis_requires_five_integrated_comparable_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            record_hook_event(repo, {
                "hook_event_name": "SessionStart", "cwd": str(repo), "session_id": "bootstrap",
            })
            record_hook_event(repo, {
                "hook_event_name": "PreToolUse", "cwd": str(repo), "session_id": "bootstrap",
                "tool_use_id": "stage-tool", "tool_name": "shell_command",
                "tool_input": {"command": "python .agents/skills/lemmings/scripts/run.py metrics stage discover --task TASK-0"},
            })
            for index in range(5):
                current = task("Integrated")
                current["taskId"] = f"TASK-{index}"
                bind_run(repo, repo, task_id=current["taskId"], cohort="same")
                for stage in ("discover", "plan", "refine", "implement", "verify"):
                    enter_stage(repo, repo, stage, task=current)
                finish_run(repo, repo, "completed", task=current, event_type="task.integrated")
                observation = quality(); observation["taskId"] = current["taskId"]
                import_quality(repo, repo, observation, current["taskId"], current)
            record_hook_event(repo, {
                "hook_event_name": "PostToolUse", "cwd": str(repo), "session_id": "bootstrap",
                "tool_use_id": "stage-tool", "tool_name": "shell_command",
                "tool_response": {"output": "binding created"},
            })
            report = build_report(repo)
            self.assertEqual(3, report["completeness"]["unboundEvents"])
            self.assertFalse(report["completeness"]["complete"])
            self.assertEqual("eligible_for_review", report["analysis"]["status"])
            record_hook_event(repo, {
                "hook_event_name": "SubagentStart", "cwd": str(repo), "session_id": "lost-binding",
                "agent_id": "worker-without-binding", "agent_type": "worker",
            })
            self.assertEqual("descriptive_only", build_report(repo)["analysis"]["status"])

    def test_quality_import_does_not_reuse_a_different_tasks_finished_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            first = task("Integrated"); first["taskId"] = "TASK-A"
            first_binding = bind_run(repo, repo, task_id=first["taskId"])
            enter_stage(repo, repo, "verify", task=first)
            finish_run(repo, repo, "completed", task=first, event_type="task.integrated")
            second = task("Integrated"); second["taskId"] = "TASK-B"
            observation = quality(); observation["taskId"] = second["taskId"]
            import_quality(repo, repo, observation, second["taskId"], second)
            imported = [event for event in iter_events(repo) if event.get("type") == "quality.imported"]
            self.assertEqual(1, len(imported))
            self.assertEqual("TASK-B", imported[0]["taskId"])
            self.assertNotEqual(first_binding["runId"], imported[0]["runId"])

    def test_report_filters_do_not_export_paths_or_secret_like_values(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            report = build_report(repo, task_id=str(repo / "private"), phase_id="password=secret")
            self.assertIsNone(report["filters"]["taskId"])
            self.assertIsNone(report["filters"]["phaseId"])

    def test_analysis_requires_quality_for_the_final_integrated_head(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            for index in range(5):
                current = task("Integrated"); current["taskId"] = f"TASK-{index}"
                if index == 0:
                    current["close"]["mergeCommit"] = "merge-head"
                bind_run(repo, repo, task_id=current["taskId"], cohort="same")
                for stage in ("discover", "plan", "refine", "implement", "verify"):
                    enter_stage(repo, repo, stage, task=current)
                finish_run(repo, repo, "completed", task=current, event_type="task.integrated")
                observation = quality(); observation["taskId"] = current["taskId"]
                import_quality(repo, repo, observation, current["taskId"], current)
            report = build_report(repo)
            self.assertEqual("descriptive_only", report["analysis"]["status"])
            self.assertIn("quality evidence for each Integrated task", report["completeness"]["missing"])

    def test_analysis_requires_a_cohort_on_every_integrated_task(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            for index in range(5):
                current = task("Integrated", None if index == 4 else "same")
                current["taskId"] = f"TASK-{index}"
                bind_run(repo, repo, task_id=current["taskId"], cohort=current.get("telemetryCohort"))
                enter_stage(repo, repo, "verify", task=current)
                finish_run(repo, repo, "completed", task=current, event_type="task.integrated")
                observation = quality(); observation["taskId"] = current["taskId"]
                import_quality(repo, repo, observation, current["taskId"], current)
            self.assertEqual("descriptive_only", build_report(repo)["analysis"]["status"])

    def test_unknown_only_quality_is_not_eligible_for_review(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            for index in range(5):
                current = task("Integrated"); current["taskId"] = f"TASK-{index}"
                bind_run(repo, repo, task_id=current["taskId"], cohort="same")
                enter_stage(repo, repo, "verify", task=current)
                finish_run(repo, repo, "completed", task=current, event_type="task.integrated")
                observation = quality(); observation["taskId"] = current["taskId"]; observation["signals"][0]["status"] = "unknown"
                import_quality(repo, repo, observation, current["taskId"], current)
            self.assertEqual("descriptive_only", build_report(repo)["analysis"]["status"])

    def test_regression_resolution_is_scoped_to_task_and_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            for task_id in ("TASK-A", "TASK-B"):
                current = task("Integrated"); current["taskId"] = task_id
                bind_run(repo, repo, task_id=task_id)
                enter_stage(repo, repo, "verify", task=current)
                finish_run(repo, repo, "completed", task=current, event_type="task.integrated")
                annotate_regression(repo, repo, task_id=task_id, kind="escaped-defect", severity="P2", relation="confirmed", reference="BUG-1")
            annotate_regression(repo, repo, task_id="TASK-B", kind="regression-resolved", severity="P2", relation="confirmed", reference="BUG-1", fix_commit="fix")
            by_task = {item["taskId"]: item for item in build_report(repo)["regressions"]["confirmed"]}
            self.assertNotIn("resolvedAt", by_task["TASK-A"])
            self.assertIn("resolvedAt", by_task["TASK-B"])

    def test_regression_fix_commit_rejects_paths_and_secret_like_values(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "full")
            current = task("Integrated")
            bind_run(repo, repo, task_id=current["taskId"])
            enter_stage(repo, repo, "verify", task=current)
            finish_run(repo, repo, "completed", task=current, event_type="task.integrated")
            with self.assertRaisesRegex(ValueError, "safe identifier"):
                annotate_regression(
                    repo, repo, task_id=current["taskId"], kind="escaped-defect",
                    severity="P1", relation="confirmed", reference="BUG-1", fix_commit=str(repo / "fix"),
                )
            with self.assertRaisesRegex(ValueError, "safe identifier"):
                annotate_regression(
                    repo, repo, task_id=current["taskId"], kind="escaped-defect",
                    severity="P1", relation="confirmed", reference="BUG-2", fix_commit="password=secret",
                )

    def test_task_summary_drops_unknown_enum_values(self):
        current = task()
        current["state"] = "C:/private/source"
        current["mode"] = "custom"
        review = {"status": "made-up", "cycle": 1}
        summary = summarize_task(current, review)
        self.assertIsNone(summary["state"])
        self.assertIsNone(summary["mode"])
        self.assertIsNone(summary["reviewStatus"])

    def test_parallel_implement_span_is_wall_clock_not_summed_agent_time(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp); init_repo(repo); set_telemetry_mode(repo, "basic")
            left = repo / "left"; right = repo / "right"; left.mkdir(); right.mkdir(); init_repo(left); init_repo(right)
            first = bind_run(repo, left, task_id="TASK-A")
            second = bind_run(repo, right, task_id="TASK-B")
            record_event(repo, "stage.exited", source="cli", cwd=left, binding=first, timestamp="2026-01-01T00:00:10Z", stage="implement", data={"durationSeconds": 10})
            record_event(repo, "stage.exited", source="cli", cwd=right, binding=second, timestamp="2026-01-01T00:00:15Z", stage="implement", data={"durationSeconds": 10})
            performance = build_report(repo)["performance"]
            self.assertEqual(20, performance["stageSeconds"]["implement"])
            self.assertEqual(15, performance["parallelImplementSpanSeconds"])


if __name__ == "__main__":
    unittest.main()
