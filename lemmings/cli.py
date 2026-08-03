"""Public command line interface for Lemmings."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from .core import (
    DEFAULT_MODELS, SCHEMA_VERSION, ValidationResult, check_repository, detect_mode,
    git, inspect_worktree, read_object, resolve_path, runtime_marker, validate_wave,
    write_object,
)
from .telemetry import (
    ANNOTATION_KINDS, FINISH_OUTCOMES, LIFECYCLE_STAGES, TELEMETRY_MODES,
    annotate_regression, bind_run, build_report, cleanup_events, enter_stage,
    find_task_binding, finish_run, import_quality, render_markdown, set_telemetry_mode,
    telemetry_enabled, telemetry_status, record_task_observation, record_telemetry_error,
)


def telemetry_side_effect(repo: Path, callback: Any, *args: Any, **kwargs: Any) -> Any:
    """Keep optional telemetry from changing an orchestration command outcome."""
    try:
        return callback(*args, **kwargs)
    except Exception as error:
        record_telemetry_error(repo, error)
        return None


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def load_optional(repo: Path, value: str | None, fallback: str | None = None) -> dict[str, Any] | None:
    path = resolve_path(repo, value or fallback)
    return read_object(path) if path and path.is_file() else None


def paths_from_args(args: argparse.Namespace) -> tuple[Path, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    repo = Path(args.repo).resolve()
    profile = load_optional(repo, getattr(args, "profile", None), ".codex/lemmings.json")
    marker_data = None
    try:
        marker_path = runtime_marker(repo)
        marker_data = read_object(marker_path) if marker_path.is_file() else None
    except ValueError:
        pass
    task = load_optional(repo, getattr(args, "task", None), (marker_data or {}).get("taskPath"))
    phase = load_optional(repo, getattr(args, "phase", None), (marker_data or {}).get("phasePath"))
    review_reference = getattr(args, "review", None) or (marker_data or {}).get("reviewPath")
    review = load_optional(repo, review_reference)
    if review is not None and review_reference:
        review["_evidencePath"] = str(review_reference)
    return repo, profile, task, phase, review, marker_data


def runtime_reference_findings(repo: Path, marker: dict[str, Any] | None) -> ValidationResult:
    result = ValidationResult()
    if not marker:
        return result
    task_reference = marker.get("taskPath")
    if task_reference:
        task_path = resolve_path(repo, str(task_reference))
        if not task_path or not task_path.is_file():
            result.error("runtime.task_missing", f"active runtime task does not exist: {task_reference}")
    return result


def command_check(args: argparse.Namespace) -> int:
    repo, profile, task, phase, review, marker = paths_from_args(args)
    result = check_repository(repo, profile, task, phase, review, args.all)
    result.extend(runtime_reference_findings(repo, marker))
    if task and telemetry_side_effect(repo, telemetry_enabled, repo):
        working = resolve_path(repo, task.get("worktree")) or repo
        telemetry_side_effect(repo, record_task_observation, repo, working, task)
    emit(result.as_dict())
    return 0 if result.ok else 1


def command_status(args: argparse.Namespace) -> int:
    repo, profile, task, phase, review, marker_data = paths_from_args(args)
    result = check_repository(repo, profile, task, phase, review)
    result.extend(runtime_reference_findings(repo, marker_data))
    marker = runtime_marker(repo)
    result.data.update({
        "active": marker.is_file(),
        "marker": str(marker),
        "taskId": (task or {}).get("taskId"),
        "state": (task or {}).get("state"),
    })
    emit(result.as_dict())
    return 0 if result.ok else 1


def command_activation(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    marker = runtime_marker(repo)
    if args.command == "on":
        profile = load_optional(repo, args.profile, ".codex/lemmings.json") or {"schemaVersion": 1, "mode": "auto"}
        task = load_optional(repo, args.task)
        phase = load_optional(repo, args.phase)
        value = {
            "schemaVersion": SCHEMA_VERSION,
            "enabled": True,
            "mode": detect_mode(profile, task, phase),
            "profilePath": args.profile or ".codex/lemmings.json",
        }
        for name in ("task", "phase", "review"):
            path = getattr(args, name)
            if path:
                value[name + "Path"] = path
        write_object(marker, value)
        if telemetry_side_effect(repo, telemetry_enabled, repo):
            working = repo
            task_path = resolve_path(repo, args.task)
            declared_worktree = (task or {}).get("worktree")
            if declared_worktree:
                working = resolve_path(repo, str(declared_worktree)) or repo
            telemetry_side_effect(repo, bind_run,
                repo, working, task_id=(task or {}).get("taskId"), phase_id=(phase or {}).get("phaseId"),
                role=(task or {}).get("role"), model=((task or {}).get("models") or {}).get("assigned"),
                mode=value["mode"], cohort=(task or {}).get("telemetryCohort"),
                branch=(task or {}).get("branch"), task_path=str(task_path) if task_path else None,
            )
        emit({"ok": True, "active": True, "marker": str(marker), "mode": value["mode"]})
        return 0
    if args.command == "off":
        existed = marker.is_file()
        if existed:
            marker.unlink()
        if marker.parent.is_dir() and not any(marker.parent.iterdir()):
            marker.parent.rmdir()
        emit({"ok": True, "active": False, "removed": existed, "marker": str(marker)})
        return 0
    raise ValueError(f"unsupported activation command: {args.command}")


def _worktree_root(repo: Path, profile: dict[str, Any] | None) -> Path:
    value = (profile or {}).get("worktreeRoot", "../lemmings-worktrees")
    return resolve_path(repo, value) or repo.parent / "lemmings-worktrees"


def command_worktree(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    profile = load_optional(repo, args.profile, ".codex/lemmings.json")
    if args.worktree_command == "inspect":
        path = resolve_path(repo, args.path) if args.path else _worktree_root(repo, profile)
        if path and path.is_dir() and not (path / ".git").exists():
            rows = [inspect_worktree(repo, item) for item in path.iterdir() if item.is_dir()]
            emit({"ok": True, "worktrees": rows})
        else:
            emit({"ok": True, "worktree": inspect_worktree(repo, path or repo)})
        return 0
    path = resolve_path(repo, args.path) if args.path else _worktree_root(repo, profile) / args.task.lower()
    assert path is not None
    root = _worktree_root(repo, profile).resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        emit({"ok": False, "error": f"worktree must be inside {root}"})
        return 1
    if args.worktree_command == "allocate":
        if path.exists():
            emit({"ok": False, "error": f"worktree already exists: {path}"})
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        base = args.base or "HEAD"
        process = git(repo, "worktree", "add", "-b", args.branch, str(path), base)
        if process.returncode:
            emit({"ok": False, "error": process.stderr.strip()})
            return 1
        emit({"ok": True, "taskId": args.task, "worktree": inspect_worktree(repo, path)})
        return 0
    info = inspect_worktree(repo, path)
    if not info["exists"]:
        emit({"ok": False, "error": f"worktree does not exist: {path}"})
        return 1
    if not args.execute:
        emit({"ok": True, "executed": False, "command": ["git", "worktree", "remove", str(path)], "worktree": info})
        return 0
    if not info["clean"]:
        emit({"ok": False, "error": "worktree is dirty"})
        return 1
    process = git(repo, "worktree", "remove", str(path))
    emit({"ok": not process.returncode, "executed": not process.returncode, "error": process.stderr.strip() or None})
    return 0 if not process.returncode else 1


def command_phase(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    output = resolve_path(repo, args.output) or repo / "docs/tasks/phase.json"
    if args.baseline:
        baseline = args.baseline
    else:
        head = git(repo, "rev-parse", "--verify", "HEAD^{commit}")
        if head.returncode:
            emit({"ok": False, "error": "phase prepare requires an existing baseline commit"})
            return 1
        baseline = head.stdout.strip()
    baseline_review = {"status": "Planned", "reviewerModel": None, "evidence": args.baseline_review_evidence}
    evidence_path = resolve_path(repo, args.baseline_review_evidence)
    if evidence_path and evidence_path.is_file():
        try:
            evidence = read_object(evidence_path)
        except (OSError, ValueError, json.JSONDecodeError):
            evidence = {}
        if evidence.get("schemaVersion") == SCHEMA_VERSION and evidence.get("phaseId") == args.phase_id and evidence.get("status") == "Accepted" and evidence.get("reviewerModel") == "gpt-5.6-sol:high" and evidence.get("baselineSha") == baseline:
            baseline_review = {"status": "Accepted", "reviewerModel": "gpt-5.6-sol:high", "evidence": args.baseline_review_evidence}
    phase = {
        "schemaVersion": SCHEMA_VERSION,
        "phaseId": args.phase_id,
        "baselineSha": baseline,
        "integrationBranch": args.integration_branch,
        "contractsFrozen": True,
        "contracts": args.contract,
        "baselineReview": baseline_review,
        "close": {"mergeCommits": [], "phaseValidation": []},
    }
    write_object(output, phase)
    emit({"ok": True, "path": str(output), "phase": phase})
    return 0


def command_wave(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    profile = load_optional(repo, args.profile, ".codex/lemmings.json")
    phase = read_object(resolve_path(repo, args.phase))
    tasks = [read_object(resolve_path(repo, value)) for value in args.task]
    result = validate_wave(repo, tasks, phase, profile)
    result.data["dispatch"] = [
        {"taskId": task.get("taskId"), "branch": task.get("branch"), "worktree": task.get("worktree"), "model": (task.get("models") or {}).get("assigned")}
        for task in tasks
    ]
    if result.ok and telemetry_side_effect(repo, telemetry_enabled, repo):
        for path_value, task in zip(args.task, tasks):
            worktree = resolve_path(repo, task.get("worktree"))
            if not worktree:
                continue
            telemetry_side_effect(repo, bind_run,
                repo, worktree, task_id=task.get("taskId"), phase_id=phase.get("phaseId"),
                role=task.get("role"), model=(task.get("models") or {}).get("assigned"),
                mode=task.get("mode") or "strict", cohort=task.get("telemetryCohort"),
                branch=task.get("branch"), task_path=str(resolve_path(repo, path_value)),
            )
    emit(result.as_dict())
    return 0 if result.ok else 1


def command_close(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    task_path = resolve_path(repo, args.task)
    task = read_object(task_path)
    if task.get("state") != "Accepted":
        emit({"ok": False, "error": "only an Accepted task can be integrated"})
        return 1
    if telemetry_side_effect(repo, telemetry_enabled, repo):
        working = resolve_path(repo, task.get("worktree")) or repo
        telemetry_side_effect(repo, record_task_observation, repo, working, task)
    task["previousState"] = "Accepted"
    task["state"] = "Integrated"
    task["close"] = {"mergeCommit": args.merge_commit, "integrationValidationPassed": args.validation_passed}
    result = check_repository(repo, load_optional(repo, args.profile, ".codex/lemmings.json"), task, load_optional(repo, args.phase), load_optional(repo, args.review))
    if result.ok:
        write_object(task_path, task)
        working = resolve_path(repo, task.get("worktree")) or repo
        telemetry_side_effect(repo, finish_run, repo, working, "completed", task=task, event_type="task.integrated")
    emit(result.as_dict())
    return 0 if result.ok else 1


def command_scorecard(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    observations: list[dict[str, Any]] = []
    for path in args.observation:
        value = read_object(resolve_path(repo, path))
        nested = value.get("observations")
        if isinstance(nested, list):
            observations.extend(item for item in nested if isinstance(item, dict))
        else:
            observations.append(value)
    if not args.benchmark and len(observations) < 2:
        emit({"ok": True, "created": False, "reason": "scorecard requires a benchmark or at least two comparable observations"})
        return 0
    if not args.benchmark:
        cohorts = {item.get("cohort") for item in observations}
        if None in cohorts or len(cohorts) != 1:
            emit({"ok": False, "created": False, "reason": "scorecard observations require one shared telemetry cohort"})
            return 1
    output = resolve_path(repo, args.output) or repo / "docs/tasks/routing-scorecard.json"
    value = {"schemaVersion": 1, "benchmark": args.benchmark, "observations": observations}
    write_object(output, value)
    emit({"ok": True, "created": True, "path": str(output)})
    return 0


def command_models(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    path = resolve_path(repo, args.profile) or repo / ".codex/lemmings.json"
    profile = read_object(path) if path.is_file() else {"schemaVersion": 1, "mode": "auto"}
    if args.models_command == "status":
        emit({"ok": True, "models": profile.get("models", DEFAULT_MODELS), "requestedModels": profile.get("requestedModels", {}), "taskModels": profile.get("taskModels", {})})
        return 0
    if args.models_command == "reset":
        profile["models"] = dict(DEFAULT_MODELS)
        profile.pop("requestedModels", None)
        profile.pop("taskModels", None)
    else:
        role, separator, model = args.assignment.partition("=")
        if not separator or not role or not model:
            emit({"ok": False, "error": "assignment must be role=model:effort"})
            return 1
        if args.models_command == "task":
            profile.setdefault("taskModels", {}).setdefault(args.task_id, {})[role] = model
        else:
            profile.setdefault("requestedModels", {})[role] = model
    write_object(path, profile)
    emit({"ok": True, "path": str(path), "profile": profile})
    return 0


def command_mode(args: argparse.Namespace) -> int:
    repo, profile, task, phase, _, marker_data = paths_from_args(args)
    path = resolve_path(repo, args.profile) or repo / ".codex/lemmings.json"
    exists = path.is_file()
    profile = profile or {"schemaVersion": SCHEMA_VERSION, "mode": "auto"}

    if args.mode_command != "status":
        profile["mode"] = args.mode_command
        write_object(path, profile)
        exists = True

    configured = str(profile.get("mode", "auto")).lower()
    effective = detect_mode(profile, task, phase)
    marker = runtime_marker(repo) if marker_data is not None else None
    if marker is not None and args.mode_command != "status":
        marker_profile = resolve_path(repo, str(marker_data.get("profilePath", ".codex/lemmings.json")))
        if marker_profile == path:
            marker_data["mode"] = effective
            write_object(marker, marker_data)

    emit({
        "ok": True,
        "profile": str(path),
        "exists": exists,
        "configured": configured,
        "effective": effective,
        "active": marker_data is not None,
        "runtimeMode": (marker_data or {}).get("mode"),
    })
    return 0


def _task_from_metrics_arg(repo: Path, value: str | None) -> tuple[str | None, dict[str, Any] | None, str | None]:
    if not value:
        try:
            marker = runtime_marker(repo)
            if marker.is_file():
                state = read_object(marker)
                reference = state.get("taskPath")
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
        raise ValueError(f"task packet path does not exist: {value}; use a stable task ID until it exists")
    binding = find_task_binding(repo, value)
    if binding and binding.get("taskPath"):
        task_path = Path(str(binding["taskPath"]))
        if task_path.is_file():
            task = read_object(task_path)
            return str(task.get("taskId") or value), task, str(task_path)
    return value, None, None


def _phase_from_metrics_arg(repo: Path, value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    path = resolve_path(repo, value)
    if path and path.is_file():
        return read_object(path)
    return {"phaseId": value}


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
    task_id, task, task_reference = _task_from_metrics_arg(repo, getattr(args, "task", None))
    phase = _phase_from_metrics_arg(repo, getattr(args, "phase", None))
    working = resolve_path(repo, (task or {}).get("worktree")) or repo
    if action == "stage":
        if task_id and task is None:
            task = {"taskId": task_id, "role": "orchestrator"}
        result = enter_stage(repo, working, args.stage, task=task, phase=phase, task_path=task_reference)
        emit(result)
        return 0
    if action == "finish":
        if task_id and task is None:
            task = {"taskId": task_id, "role": "orchestrator"}
        emit(finish_run(repo, working, args.outcome, task=task))
        return 0
    if action == "import":
        observation = read_object(resolve_path(repo, args.file))
        expected = task_id or str(observation.get("taskId") or "")
        if not expected:
            raise ValueError("metrics import requires --task or observation.taskId")
        emit(import_quality(repo, working, observation, expected, task))
        return 0
    if action == "annotate":
        if not task_id:
            raise ValueError("metrics annotate requires --task")
        emit(annotate_regression(
            repo, working, task_id=task_id, kind=args.kind, severity=args.severity,
            relation=args.relation, reference=args.reference, detected_at=args.detected_at,
            resolved_at=args.resolved_at, fix_commit=args.fix_commit,
        ))
        return 0
    if action == "report":
        report = build_report(repo, task_id=task_id, phase_id=(phase or {}).get("phaseId"), since=args.since)
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
        return 0
    if action == "cleanup":
        emit(cleanup_events(repo, args.older_than, args.execute))
        return 0
    raise ValueError(f"unsupported metrics command: {action}")


def add_common(parser: argparse.ArgumentParser, artifacts: bool = False) -> None:
    parser.add_argument("--repo", default=".")
    parser.add_argument("--profile")
    if artifacts:
        parser.add_argument("--task")
        parser.add_argument("--phase")
        parser.add_argument("--review")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lemmings", description="Lemmings repository orchestration")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate the applicable lifecycle contracts")
    add_common(check, True); check.add_argument("--all", action="store_true"); check.set_defaults(run=command_check)
    status = sub.add_parser("status", help="show runtime and contract status")
    add_common(status, True); status.set_defaults(run=command_status)
    on = sub.add_parser("on", help="enable repo-scoped hook enforcement")
    add_common(on); on.add_argument("--task"); on.add_argument("--phase"); on.add_argument("--review"); on.set_defaults(run=command_activation)
    off = sub.add_parser("off", help="disable repo-scoped hook enforcement")
    add_common(off); off.set_defaults(run=command_activation)
    mode = sub.add_parser("mode", help="manage the repository orchestration mode")
    mode_sub = mode.add_subparsers(dest="mode_command", required=True)
    for name in ("auto", "simple", "standard", "strict", "status"):
        item = mode_sub.add_parser(name)
        add_common(item)
        item.set_defaults(run=command_mode)
    worktree = sub.add_parser("worktree", help="manage isolated writer worktrees")
    add_common(worktree); worktree_sub = worktree.add_subparsers(dest="worktree_command", required=True)
    allocate = worktree_sub.add_parser("allocate"); allocate.add_argument("--task", required=True); allocate.add_argument("--branch", required=True); allocate.add_argument("--base"); allocate.add_argument("--path"); allocate.set_defaults(run=command_worktree)
    inspect = worktree_sub.add_parser("inspect"); inspect.add_argument("--path"); inspect.set_defaults(run=command_worktree)
    release = worktree_sub.add_parser("release"); release.add_argument("--task", required=True); release.add_argument("--path"); release.add_argument("--execute", action="store_true"); release.set_defaults(run=command_worktree)
    phase = sub.add_parser("phase", help="prepare a Strict phase")
    add_common(phase); phase_sub = phase.add_subparsers(dest="phase_command", required=True)
    prepare = phase_sub.add_parser("prepare"); prepare.add_argument("--phase-id", required=True); prepare.add_argument("--integration-branch", required=True); prepare.add_argument("--baseline"); prepare.add_argument("--contract", action="append", default=[]); prepare.add_argument("--baseline-review-evidence"); prepare.add_argument("--output"); prepare.set_defaults(run=command_phase)
    wave = sub.add_parser("wave", help="derive and validate a Strict dispatch wave")
    add_common(wave); wave_sub = wave.add_subparsers(dest="wave_command", required=True)
    plan = wave_sub.add_parser("plan"); plan.add_argument("--phase", required=True); plan.add_argument("--task", action="append", required=True); plan.set_defaults(run=command_wave)
    close = sub.add_parser("close", help="record integration close evidence")
    add_common(close); close.add_argument("--task", required=True); close.add_argument("--phase"); close.add_argument("--review", required=True); close.add_argument("--merge-commit", required=True); close.add_argument("--validation-passed", action="store_true"); close.set_defaults(run=command_close)
    score = sub.add_parser("scorecard", help="conditionally create a routing scorecard")
    add_common(score); score.add_argument("--observation", action="append", default=[]); score.add_argument("--benchmark", action="store_true"); score.add_argument("--output"); score.set_defaults(run=command_scorecard)
    models = sub.add_parser("models", help="manage model pins")
    add_common(models); models_sub = models.add_subparsers(dest="models_command", required=True)
    set_model = models_sub.add_parser("set"); set_model.add_argument("assignment"); set_model.set_defaults(run=command_models)
    task_model = models_sub.add_parser("task"); task_model.add_argument("task_id"); task_model.add_argument("assignment"); task_model.set_defaults(run=command_models)
    for name in ("status", "reset"):
        models_sub.add_parser(name).set_defaults(run=command_models)
    metrics = sub.add_parser("metrics", help="manage optional local pipeline telemetry")
    metrics_sub = metrics.add_subparsers(dest="metrics_command", required=True)
    for name in ("off", "basic", "full", "status"):
        item = metrics_sub.add_parser(name)
        add_common(item)
        item.set_defaults(run=command_metrics)
    stage = metrics_sub.add_parser("stage", help="enter a lifecycle stage and close the previous one")
    add_common(stage); stage.add_argument("stage", choices=LIFECYCLE_STAGES); stage.add_argument("--task"); stage.add_argument("--phase"); stage.set_defaults(run=command_metrics)
    finish = metrics_sub.add_parser("finish", help="finish the current telemetry run")
    add_common(finish); finish.add_argument("--outcome", required=True, choices=sorted(FINISH_OUTCOMES)); finish.add_argument("--task"); finish.set_defaults(run=command_metrics)
    importing = metrics_sub.add_parser("import", help="import normalized CI or validation quality signals")
    add_common(importing); importing.add_argument("--task"); importing.add_argument("--file", required=True); importing.set_defaults(run=command_metrics)
    annotate = metrics_sub.add_parser("annotate", help="record a post-integration regression or resolution")
    add_common(annotate); annotate.add_argument("--task", required=True); annotate.add_argument("--kind", required=True, choices=sorted(ANNOTATION_KINDS)); annotate.add_argument("--severity", required=True, choices=["P0", "P1", "P2", "P3"]); annotate.add_argument("--relation", default="confirmed", choices=["confirmed", "suspected"]); annotate.add_argument("--reference", required=True); annotate.add_argument("--detected-at"); annotate.add_argument("--resolved-at"); annotate.add_argument("--fix-commit"); annotate.set_defaults(run=command_metrics)
    report = metrics_sub.add_parser("report", help="build a privacy-bounded aggregate report")
    add_common(report); report.add_argument("--task"); report.add_argument("--phase"); report.add_argument("--since"); report.add_argument("--format", choices=["json", "markdown"], default="json"); report.add_argument("--output"); report.set_defaults(run=command_metrics)
    cleanup = metrics_sub.add_parser("cleanup", help="inspect or remove expired local event files")
    add_common(cleanup); cleanup.add_argument("--older-than", default="90d"); cleanup.add_argument("--execute", action="store_true"); cleanup.set_defaults(run=command_metrics)
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
