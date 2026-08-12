"""Contract validation, quality reporting, workspaces, and opt-in timing telemetry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .contracts import ValidationResult, as_list, check_repository, read_object, resolve_path, runtime_marker, task_worktree, validate_wave
from .quality import build_quality_report, finalize_task_quality
from .telemetry import (
    ANNOTATION_KINDS,
    FINISH_OUTCOMES,
    LIFECYCLE_STAGES,
    TELEMETRY_MODES,
    annotate_regression,
    build_report,
    cleanup_events,
    enter_stage,
    find_task_binding,
    finish_run,
    import_quality,
    render_markdown,
    set_telemetry_mode,
    telemetry_status,
)
from .workspace import estimate_workspace, inspect_workspaces


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def load_optional(repo: Path, value: str | None, fallback: str | None = None) -> dict[str, Any] | None:
    path = resolve_path(repo, value or fallback)
    return read_object(path) if path and path.is_file() else None


def load_artifacts(args: argparse.Namespace) -> tuple[Path, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    repo = Path(args.repo).resolve()
    profile = load_optional(repo, getattr(args, "profile", None), ".codex/lemmings.json")
    marker_data: dict[str, Any] | None = None
    try:
        marker = runtime_marker(repo)
        marker_data = read_object(marker) if marker.is_file() else None
    except ValueError:
        pass
    task_argument = getattr(args, "task", None)
    task_value = task_argument[0] if isinstance(task_argument, list) and task_argument else task_argument
    task = load_optional(repo, task_value, (marker_data or {}).get("taskPath"))
    phase = load_optional(repo, getattr(args, "phase", None), (marker_data or {}).get("phasePath"))
    review_reference = getattr(args, "review", None) or (marker_data or {}).get("reviewPath")
    review = load_optional(repo, review_reference)
    if review is not None and review_reference:
        review["_evidencePath"] = str(review_reference)
    return repo, profile, task, phase, review, marker_data


def runtime_findings(repo: Path, marker: dict[str, Any] | None) -> ValidationResult:
    result = ValidationResult()
    if marker and marker.get("taskPath"):
        path = resolve_path(repo, str(marker["taskPath"]))
        if not path or not path.is_file():
            result.error("runtime.task_missing", f"active runtime task does not exist: {marker['taskPath']}")
    return result


def command_check(args: argparse.Namespace) -> int:
    repo, profile, task, phase, review, marker = load_artifacts(args)
    tasks: dict[str, dict[str, Any]] = {}
    for reference in as_list(args.task):
        loaded = load_optional(repo, str(reference))
        if loaded and loaded.get("taskId"):
            tasks[str(loaded["taskId"])] = loaded
    if task and task.get("taskId"):
        tasks.setdefault(str(task["taskId"]), task)
    if args.all and phase:
        for pattern in as_list((profile or {}).get("taskGlobs") or "docs/tasks/**/*.json"):
            if Path(str(pattern)).is_absolute():
                continue
            for path in repo.glob(str(pattern)):
                if not path.is_file():
                    continue
                try:
                    loaded = read_object(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if loaded.get("taskId") and "state" in loaded and "ownership" in loaded:
                    tasks.setdefault(str(loaded["taskId"]), loaded)
    if not tasks:
        result = check_repository(repo, profile, None, phase, review, args.all)
        if args.all and phase:
            result.extend(validate_wave(repo, [], phase, profile, complete=True))
    else:
        result = ValidationResult(data={"mode": "strict" if phase else None, "idle": False, "tasks": sorted(tasks)})
        primary_id = str((task or {}).get("taskId") or "")
        for task_id, current in tasks.items():
            current_review = review if task_id == primary_id and review else None
            if current_review is None and current.get("reviewRef"):
                current_review = load_optional(repo, str(current["reviewRef"]))
                if current_review is not None:
                    current_review["_evidencePath"] = str(current["reviewRef"])
            result.extend(check_repository(repo, profile, current, phase, current_review, False))
        if args.all and phase:
            result.extend(validate_wave(repo, tasks.values(), phase, profile, complete=True))
    result.extend(runtime_findings(repo, marker))
    emit(result.as_dict())
    return 0 if result.ok else 1


def command_status(args: argparse.Namespace) -> int:
    repo, profile, task, phase, review, marker = load_artifacts(args)
    result = check_repository(repo, profile, task, phase, review)
    result.extend(runtime_findings(repo, marker))
    marker_path = runtime_marker(repo)
    result.data.update({
        "active": marker_path.is_file(),
        "marker": str(marker_path),
        "taskId": (task or {}).get("taskId"),
        "state": (task or {}).get("state"),
    })
    emit(result.as_dict())
    return 0 if result.ok else 1


def command_workspace(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    profile = load_optional(repo, args.profile, ".codex/lemmings.json")
    if args.workspace_command == "estimate":
        emit({"ok": True, **estimate_workspace(repo, profile, args.backend, args.package)})
    else:
        emit(inspect_workspaces(repo, profile))
    return 0


def _task_arg(repo: Path, value: str | None) -> tuple[str | None, dict[str, Any] | None, str | None]:
    if not value:
        try:
            marker = runtime_marker(repo)
            if marker.is_file():
                reference = read_object(marker).get("taskPath")
                path = resolve_path(repo, reference)
                if path and path.is_file():
                    task = read_object(path)
                    return task.get("taskId"), task, str(reference)
        except ValueError:
            pass
        return None, None, None
    path = resolve_path(repo, value)
    if path and path.is_file():
        task = read_object(path)
        return str(task.get("taskId") or value), task, value
    if value.lower().endswith(".json") or "/" in value or "\\" in value:
        raise ValueError(f"task packet path does not exist: {value}")
    binding = find_task_binding(repo, value)
    if binding and binding.get("taskPath"):
        task_path = Path(str(binding["taskPath"]))
        if task_path.is_file():
            task = read_object(task_path)
            return str(task.get("taskId") or value), task, str(task_path)
    return value, None, None


def _phase_arg(repo: Path, value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    path = resolve_path(repo, value)
    return read_object(path) if path and path.is_file() else {"phaseId": value}


def _add_benchmark(report: dict[str, Any]) -> None:
    analysis = report.get("analysis") or {}
    report["benchmark"] = {
        "eligible": analysis.get("status") == "eligible_for_review",
        "status": analysis.get("status", "descriptive_only"),
        "reason": analysis.get("reason", "Insufficient comparable integrated tasks"),
        "observations": len(report.get("observations") or []),
    }


def command_metrics(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    action = args.metrics_command
    if action in TELEMETRY_MODES:
        settings = set_telemetry_mode(repo, action)
        emit({"ok": True, "mode": settings["mode"], "retentionDays": settings["retentionDays"], "maxLocalMiB": settings["maxLocalMiB"]})
        return 0
    if action == "status":
        emit(telemetry_status(repo))
        return 0
    task_id, task, task_reference = _task_arg(repo, getattr(args, "task", None))
    phase = _phase_arg(repo, getattr(args, "phase", None))
    working = resolve_path(repo, task_worktree(task or {})) or repo
    profile = load_optional(repo, getattr(args, "profile", None), ".codex/lemmings.json") or {}
    if action == "stage":
        emit(enter_stage(repo, working, args.stage, task=task or ({"taskId": task_id, "role": "orchestrator"} if task_id else None), phase=phase, task_path=task_reference))
    elif action == "finish":
        quality = None
        if task_reference and task:
            task, quality = finalize_task_quality(repo, task_reference, args.outcome)
        local = finish_run(repo, working, args.outcome, task=task or ({"taskId": task_id, "role": "orchestrator"} if task_id else None))
        emit({
            **local,
            "taskQuality": quality,
            "qualityReport": build_quality_report(repo, profile),
        })
    elif action == "import":
        observation = read_object(resolve_path(repo, args.file))
        expected = task_id or str(observation.get("taskId") or "")
        if not expected:
            raise ValueError("metrics import requires --task or observation.taskId")
        emit(import_quality(repo, working, observation, expected, task))
    elif action == "annotate":
        if not task_id:
            raise ValueError("metrics annotate requires --task")
        emit(annotate_regression(repo, working, task_id=task_id, kind=args.kind, severity=args.severity, relation=args.relation, reference=args.reference, detected_at=args.detected_at, resolved_at=args.resolved_at, fix_commit=args.fix_commit))
    elif action == "report":
        report = build_report(repo, task_id=task_id, phase_id=(phase or {}).get("phaseId"), since=args.since)
        report["taskQuality"] = build_quality_report(repo, profile, task_id=task_id)
        if args.benchmark:
            _add_benchmark(report)
        rendered = render_markdown(report) if args.format == "markdown" else json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            output = resolve_path(repo, args.output)
            assert output is not None
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            emit({"ok": True, "created": True, "path": str(output), "format": args.format})
        elif args.format == "markdown":
            print(rendered, end="")
        else:
            emit(report)
    elif action == "cleanup":
        emit(cleanup_events(repo, args.older_than, args.execute))
    return 0


def add_common(parser: argparse.ArgumentParser, artifacts: bool = False) -> None:
    parser.add_argument("--repo", default=".")
    parser.add_argument("--profile")
    if artifacts:
        parser.add_argument("--task")
        parser.add_argument("--phase")
        parser.add_argument("--review")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lemmings", description="Optional tooling for the Lemmings smart skill")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate applicable contracts"); add_common(check); check.add_argument("--task", action="append"); check.add_argument("--phase"); check.add_argument("--review"); check.add_argument("--all", action="store_true"); check.set_defaults(run=command_check)
    status = sub.add_parser("status", help="inspect runtime and contract status"); add_common(status, True); status.set_defaults(run=command_status)
    workspace = sub.add_parser("workspace", help="estimate or inspect workspaces"); workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    estimate = workspace_sub.add_parser("estimate"); add_common(estimate); estimate.add_argument("--backend", default="auto", choices=["auto", "current", "code-worktree", "package-worktree", "unity-clone"]); estimate.add_argument("--package", help="repo-relative target package path for package-worktree sizing"); estimate.set_defaults(run=command_workspace)
    inspect = workspace_sub.add_parser("inspect"); add_common(inspect); inspect.set_defaults(run=command_workspace)
    metrics = sub.add_parser("metrics", help="finalize tracked quality metrics and manage optional timing telemetry"); metrics_sub = metrics.add_subparsers(dest="metrics_command", required=True)
    for name in ("off", "basic", "full", "status"):
        item = metrics_sub.add_parser(name); add_common(item); item.set_defaults(run=command_metrics)
    stage = metrics_sub.add_parser("stage"); add_common(stage); stage.add_argument("stage", choices=LIFECYCLE_STAGES); stage.add_argument("--task"); stage.add_argument("--phase"); stage.set_defaults(run=command_metrics)
    finish = metrics_sub.add_parser("finish"); add_common(finish); finish.add_argument("--outcome", required=True, choices=sorted(FINISH_OUTCOMES)); finish.add_argument("--task"); finish.set_defaults(run=command_metrics)
    importing = metrics_sub.add_parser("import"); add_common(importing); importing.add_argument("--task"); importing.add_argument("--file", required=True); importing.set_defaults(run=command_metrics)
    annotate = metrics_sub.add_parser("annotate"); add_common(annotate); annotate.add_argument("--task", required=True); annotate.add_argument("--kind", required=True, choices=sorted(ANNOTATION_KINDS)); annotate.add_argument("--severity", required=True, choices=["P0", "P1", "P2", "P3"]); annotate.add_argument("--relation", default="confirmed", choices=["confirmed", "suspected"]); annotate.add_argument("--reference", required=True); annotate.add_argument("--detected-at"); annotate.add_argument("--resolved-at"); annotate.add_argument("--fix-commit"); annotate.set_defaults(run=command_metrics)
    report = metrics_sub.add_parser("report"); add_common(report); report.add_argument("--task"); report.add_argument("--phase"); report.add_argument("--since"); report.add_argument("--benchmark", action="store_true"); report.add_argument("--format", choices=["json", "markdown"], default="json"); report.add_argument("--output"); report.set_defaults(run=command_metrics)
    cleanup = metrics_sub.add_parser("cleanup"); add_common(cleanup); cleanup.add_argument("--older-than", default="90d"); cleanup.add_argument("--execute", action="store_true"); cleanup.set_defaults(run=command_metrics)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.run(args))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        emit({"ok": False, "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
