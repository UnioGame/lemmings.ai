"""Contract validation, manager-directed workspaces, routing, and offline metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from .contracts import (
    DISTRIBUTION_VERSION,
    PLUGIN_VERSION,
    SCHEMA_VERSION,
    ValidationResult,
    as_list,
    check_task_repository,
    check_repository,
    detect_mode,
    dispatchable_tasks,
    read_object,
    resolve_path,
    runtime_marker,
    task_worktree,
    validate_batch,
    validate_phase,
    validate_profile,
    validate_review,
    validate_task,
    validate_wave,
    write_object,
)
from .models import (
    advance_recovery_route,
    apply_proposal,
    apply_recovery_proposal,
    build_proposal,
    build_recovery_proposal,
)
from .quality import build_quality_report
from .telemetry import (
    ANNOTATION_KINDS,
    FINISH_OUTCOMES,
    LIFECYCLE_STAGES,
    TELEMETRY_MODES,
    annotate_regression,
    build_report,
    cleanup_events,
    find_task_binding,
    import_quality,
    record_event,
    render_markdown,
    set_telemetry_mode,
    telemetry_status,
)
from .usage import normalize_usage_export
from .workspace import (
    claim_workspace,
    estimate_workspace,
    inspect_workspaces,
    register_workspace,
    release_workspace,
    remove_workspace,
)


PROFILE_PATH = ".agents/lemmings.json"


def load_profile(repo: Path, value: str | None = None) -> dict[str, Any] | None:
    if value:
        return load_optional(repo, value)
    return load_optional(repo, None, PROFILE_PATH)


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def load_optional(repo: Path, value: str | None, fallback: str | None = None) -> dict[str, Any] | None:
    path = resolve_path(repo, value or fallback)
    return read_object(path) if path and path.is_file() else None


def load_artifacts(args: argparse.Namespace) -> tuple[Path, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    repo = Path(args.repo).resolve()
    profile = load_profile(repo, getattr(args, "profile", None))
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
    if marker and marker.get("schemaVersion") != SCHEMA_VERSION:
        message = "schemaVersion 2 is unsupported by Lemmings 3.3; replace the legacy bundle" if marker.get("schemaVersion") == 2 else f"unsupported schemaVersion: {marker.get('schemaVersion')!r}; expected 3"
        result.error("runtime.schema", message)
    if marker and marker.get("taskPath"):
        path = resolve_path(repo, str(marker["taskPath"]))
        if not path or not path.is_file():
            result.error("runtime.task_missing", f"active runtime task does not exist: {marker['taskPath']}")
    return result


def _tree_fingerprint(root: Path) -> str | None:
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def distribution_findings(repo: Path, profile: dict[str, Any] | None) -> ValidationResult:
    result = ValidationResult()
    if profile is None:
        result.error("distribution.profile", f"installed {PROFILE_PATH} is missing")
        return result
    tooling = (profile or {}).get("tooling") or {}
    package = resolve_path(repo, tooling.get("root")) if isinstance(tooling, dict) else None
    if not package or not (package / "package.json").is_file():
        result.error("distribution.package", f"profile tooling.root does not resolve to a Lemmings {DISTRIBUTION_VERSION} package")
        return result
    versions = {
        "package.json": read_object(package / "package.json").get("version"),
        "pyproject.toml": next((line.split('=', 1)[1].strip().strip('"') for line in (package / "pyproject.toml").read_text(encoding="utf-8").splitlines() if line.startswith("version = ")), None),
        "lemmings.__version__": next((line.split('=', 1)[1].strip().strip('"') for line in (package / "lemmings" / "__init__.py").read_text(encoding="utf-8").splitlines() if line.startswith("__version__ = ")), None),
    }
    if set(versions.values()) != {DISTRIBUTION_VERSION}:
        result.error("distribution.version", f"package versions differ from {DISTRIBUTION_VERSION}: {versions}")
    plugin_version = read_object(package / ".codex-plugin" / "plugin.json").get("version")
    if plugin_version != PLUGIN_VERSION:
        result.error("distribution.plugin_version", f"plugin version differs from {PLUGIN_VERSION}: {plugin_version}")
    source_skill = package / "skills" / "lemmings"
    installed_skill = repo / ".agents" / "skills" / "lemmings"
    if _tree_fingerprint(source_skill) != _tree_fingerprint(installed_skill):
        result.error("distribution.skill", "installed Lemmings skill differs from package")
    source_agents = package / "agents"
    installed_agents = repo / ".codex" / "agents"
    expected = {path.name: path.read_bytes() for path in source_agents.glob("lemmings-*.toml")}
    actual = {name: (installed_agents / name).read_bytes() for name in expected if (installed_agents / name).is_file()}
    if expected != actual:
        result.error("distribution.agents", "installed Lemmings agent profiles differ from package")
    obsolete = [name for name in ("lemmings-orchestrator.toml", "lemmings-validator.toml", "lemmings-summarizer.toml") if (installed_agents / name).is_file()]
    if obsolete:
        result.error("distribution.legacy_agents", "obsolete Lemmings agent profiles remain: " + ", ".join(obsolete))
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
        if profile is None:
            result.error("profile.missing", f"{PROFILE_PATH} is required")
    else:
        result = ValidationResult(data={"mode": "strict" if phase else None, "idle": False, "tasks": sorted(tasks)})
        if profile is None:
            result.error("profile.missing", f"{PROFILE_PATH} is required")
        else:
            result.extend(validate_profile(profile))
        primary_id = str((task or {}).get("taskId") or "")
        for task_id, current in tasks.items():
            current_review = review if task_id == primary_id and review else None
            if current_review is None and current.get("reviewRef"):
                current_review = load_optional(repo, str(current["reviewRef"]))
                if current_review is not None:
                    current_review["_evidencePath"] = str(current["reviewRef"])
            result.extend(check_task_repository(repo, profile, current, phase, current_review, args.all))
        needs_wave = bool(phase) and (args.all or any(detect_mode(profile, current, phase) == "strict" for current in tasks.values()))
        if needs_wave:
            result.extend(validate_wave(repo, tasks.values(), phase, profile, complete=args.all, validate_tasks=False))
    if args.dispatchable:
        if not phase:
            result.error("dispatch.phase", "--dispatchable requires --phase")
        else:
            result.data["dispatchable"] = dispatchable_tasks(tasks.values(), phase)
    if args.batch:
        if not phase:
            result.error("batch.phase", "--batch requires --phase")
        else:
            result.extend(validate_batch(repo, tasks.values(), phase, args.batch, profile))
    result.extend(runtime_findings(repo, marker))
    profile_argument = getattr(args, "profile", None)
    installed_profile = (repo / PROFILE_PATH).resolve()
    if args.distribution and (not profile_argument or resolve_path(repo, str(profile_argument)) == installed_profile):
        result.extend(distribution_findings(repo, profile))
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


def _artifact_reference(repo: Path, value: str, kind: str) -> tuple[str, dict[str, Any]]:
    path = resolve_path(repo, value)
    if not path or not path.is_file():
        raise ValueError(f"runtime {kind} does not exist: {value}")
    try:
        relative = path.relative_to(repo).as_posix()
    except ValueError as error:
        raise ValueError(f"runtime {kind} must be inside the repository") from error
    return relative, read_object(path)


def command_runtime(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    marker = runtime_marker(repo)
    if args.runtime_command == "status":
        state = read_object(marker) if marker.is_file() else None
        findings = runtime_findings(repo, state)
        findings.data.update({"active": bool(state), "marker": str(marker), "runtime": state})
        emit(findings.as_dict())
        return 0 if findings.ok else 1
    if args.runtime_command == "deactivate":
        marker.unlink(missing_ok=True)
        emit({"ok": True, "active": False, "marker": str(marker)})
        return 0
    profile = load_profile(repo)
    if profile is None:
        raise ValueError(f"runtime activate requires {PROFILE_PATH}")
    profile_result = validate_profile(profile)
    if not profile_result.ok:
        raise ValueError(profile_result.findings[0].message)
    task_reference, task = _artifact_reference(repo, args.task, "task")
    task_result = validate_task(task, profile)
    if not task_result.ok:
        raise ValueError(task_result.findings[0].message)
    if task.get("state") not in {"Ready", "Active", "Repair", "Candidate", "Accepted", "Blocked"}:
        raise ValueError("runtime activate requires a Task in a working lifecycle state")
    mode = detect_mode(profile, task)
    if mode == "simple":
        marker.unlink(missing_ok=True)
        emit({"ok": True, "active": False, "reason": "Simple mode does not use a runtime marker"})
        return 0
    state: dict[str, Any] = {"schemaVersion": SCHEMA_VERSION, "profilePath": PROFILE_PATH, "taskPath": task_reference}
    if args.phase:
        phase_reference, phase = _artifact_reference(repo, args.phase, "phase")
        checked = validate_phase(phase)
        if not checked.ok:
            raise ValueError(checked.findings[0].message)
        state["phasePath"] = phase_reference
    elif mode == "strict":
        raise ValueError("Strict runtime activate requires --phase")
    if args.review:
        review_reference, review = _artifact_reference(repo, args.review, "review")
        checked = validate_review(review, task, phase if args.phase else None, profile)
        if not checked.ok:
            raise ValueError(checked.findings[0].message)
        state["reviewPath"] = review_reference
    write_object(marker, state)
    emit({"ok": True, "active": True, "marker": str(marker), "runtime": state})
    return 0


def command_workspace(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    profile = load_profile(repo, args.profile)
    if args.workspace_command == "estimate":
        emit({"ok": True, **estimate_workspace(repo, profile, args.backend, args.package)})
    elif args.workspace_command == "inspect":
        emit(inspect_workspaces(repo, profile))
    elif args.workspace_command == "register":
        path = resolve_path(repo, args.path)
        if path is None:
            raise ValueError("workspace path is required")
        emit(register_workspace(repo, workspace_id=args.workspace_id, path=path, backend=args.backend, managed_by=args.managed_by, lifetime=args.lifetime, expected_revision=args.expected_revision, task_id=args.task_id, phase_id=args.phase_id, estimated_gib=args.estimated_gib, approval=args.approval, kind=args.kind, allowed_caches=args.allowed_cache))
    elif args.workspace_command == "claim":
        emit(claim_workspace(repo, workspace_id=args.workspace_id, task_id=args.task_id, base_sha=args.base_sha, integration_head=args.integration_head, branch=args.branch, expected_revision=args.expected_revision, phase_id=args.phase_id))
    elif args.workspace_command == "release":
        emit(release_workspace(repo, workspace_id=args.workspace_id, expected_revision=args.expected_revision, task_state=args.task_state, integration_evidence=args.integration_evidence, action=args.action, retention_approved=args.retention_approved, profile=profile))
    elif args.workspace_command == "remove":
        emit(remove_workspace(repo, workspace_id=args.workspace_id, expected_revision=args.expected_revision))
    return 0


def command_models(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    config_path = resolve_path(repo, args.profile or PROFILE_PATH)
    if not config_path or not config_path.is_file():
        raise ValueError(f"model configuration does not exist: {args.profile or PROFILE_PATH}")
    config = read_object(config_path)
    checked = validate_profile(config)
    if not checked.ok:
        raise ValueError(checked.findings[0].message)
    if args.models_command == "inspect":
        emit({"ok": True, "modelRoutes": config.get("modelRoutes", {})})
        return 0
    if args.models_command == "recover":
        task_path = resolve_path(repo, args.task)
        failure_path = resolve_path(repo, args.failure)
        if not task_path or not task_path.is_file() or not failure_path or not failure_path.is_file():
            raise ValueError("model recovery requires existing --task and --failure JSON files")
        failure = read_object(failure_path)
        if args.recover_command == "advance":
            emit(advance_recovery_route(
                task_path,
                args.role,
                failure,
                args.expected_revision,
                transient_retries=args.transient_retries,
                context_reductions=args.context_reductions,
            ))
            return 0
        plan_path = resolve_path(repo, args.plan)
        if not plan_path or not plan_path.is_file():
            raise ValueError("model recovery requires an existing --plan JSON file")
        catalogs = []
        for reference in args.catalog:
            path = resolve_path(repo, reference)
            if not path or not path.is_file():
                raise ValueError(f"model catalog does not exist: {reference}")
            catalogs.append(read_object(path))
        task = read_object(task_path)
        plan = read_object(plan_path)
        if args.recover_command == "propose":
            emit({"ok": True, **build_recovery_proposal(config, task, catalogs, failure, plan)})
        else:
            emit(apply_recovery_proposal(task_path, config, catalogs, failure, plan, args.option, args.confirm))
        return 0
    catalog = read_object(resolve_path(repo, args.catalog))
    routes = read_object(resolve_path(repo, args.routes))
    if args.models_command == "propose":
        emit({"ok": True, **build_proposal(config, catalog, routes)})
    else:
        emit(apply_proposal(config_path, catalog, routes, args.confirm))
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


def _add_benchmark(report: dict[str, Any], repo: Path) -> None:
    analysis = report.get("analysis") or {}
    registry = inspect_workspaces(repo).get("registry") or {}
    entries = registry.get("entries") or []
    idle = [entry for entry in entries if entry.get("state") == "idle"]
    report["benchmark"] = {
        "eligible": analysis.get("status") == "eligible_for_review",
        "status": analysis.get("status", "descriptive_only"),
        "reason": analysis.get("reason", "Insufficient comparable integrated tasks"),
        "observations": len(report.get("observations") or []),
        "provisioningTimeMs": None,
        "poolHitRate": None,
        "reuseFailures": sum(entry.get("quarantineReason") is not None for entry in entries),
        "averageIdleDiskGiB": (sum(float(entry.get("observedGiB") or entry.get("estimatedGiB") or 0) for entry in idle) / len(idle)) if idle else 0,
        "cleanupLatencyMs": None,
        "quarantinedWorkspaces": sum(entry.get("state") == "quarantined" for entry in entries),
        "wallClockMs": (report.get("durations") or {}).get("wallClockMs"),
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
    profile = load_profile(repo, getattr(args, "profile", None)) or {}
    if action == "stage":
        if task and task.get("schemaVersion") != 3:
            raise ValueError("schemaVersion 2 is unsupported by Lemmings 3.3; replace the legacy bundle" if task.get("schemaVersion") == 2 else "metrics stage requires a schema-v3 Task")
        event = record_event(repo, "run_started", source="cli", task_id=task_id, phase_id=(phase or {}).get("phaseId"), data={"mode": (task or {}).get("resolvedMode")}) if args.stage == "discover" else None
        emit({"ok": True, "recorded": bool(event), "event": event, "reason": None if event else "only run_started at discover is recorded"})
    elif action == "finish":
        if task and task.get("schemaVersion") != 3:
            raise ValueError("schemaVersion 2 is unsupported by Lemmings 3.3; replace the legacy bundle" if task.get("schemaVersion") == 2 else "metrics finish requires a schema-v3 Task")
        event = record_event(repo, "run_finished", source="cli", task_id=task_id, data={"outcome": args.outcome}, allow_finished_binding=True)
        emit({
            "ok": True,
            "recorded": bool(event),
            "event": event,
            "qualityReport": build_quality_report(repo, profile),
        })
    elif action == "import":
        observation = read_object(resolve_path(repo, args.file))
        expected = task_id or str(observation.get("taskId") or "")
        if not expected:
            raise ValueError("metrics import requires --task or observation.taskId")
        emit(import_quality(repo, working, observation, expected, task))
    elif action == "usage":
        value = read_object(resolve_path(repo, args.file))
        usage = normalize_usage_export(args.host, value)
        event = record_event(repo, "invocation_finished", source=f"{args.host}-import", task_id=task_id, data={"usage": usage}) if task_id else None
        emit({"ok": True, "usage": usage, "recorded": bool(event)})
    elif action == "annotate":
        if not task_id:
            raise ValueError("metrics annotate requires --task")
        emit(annotate_regression(repo, working, task_id=task_id, kind=args.kind, severity=args.severity, relation=args.relation, reference=args.reference, detected_at=args.detected_at, resolved_at=args.resolved_at, fix_commit=args.fix_commit))
    elif action == "report":
        report = build_report(repo, task_id=task_id, phase_id=(phase or {}).get("phaseId"), since=args.since)
        report["taskQuality"] = build_quality_report(repo, profile, task_id=task_id)
        if args.benchmark:
            _add_benchmark(report, repo)
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
    check = sub.add_parser("check", help="validate lifecycle contracts once per artifact"); add_common(check); check.add_argument("--task", action="append"); check.add_argument("--phase"); check.add_argument("--review"); check.add_argument("--all", action="store_true"); check.add_argument("--distribution", action="store_true", help="also compare the installed bundle with the package"); check.add_argument("--dispatchable", action="store_true"); check.add_argument("--batch", action="append"); check.set_defaults(run=command_check)
    status = sub.add_parser("status", help="inspect runtime and contract status"); add_common(status, True); status.set_defaults(run=command_status)
    runtime = sub.add_parser("runtime", help="activate, inspect, or deactivate schema-v3 enforcement"); runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_activate = runtime_sub.add_parser("activate"); runtime_activate.add_argument("--repo", default="."); runtime_activate.add_argument("--task", required=True); runtime_activate.add_argument("--phase"); runtime_activate.add_argument("--review"); runtime_activate.set_defaults(run=command_runtime)
    for name in ("status", "deactivate"):
        item = runtime_sub.add_parser(name); item.add_argument("--repo", default="."); item.set_defaults(run=command_runtime)
    workspace = sub.add_parser("workspace", help="estimate or inspect workspaces"); workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    estimate = workspace_sub.add_parser("estimate"); add_common(estimate); estimate.add_argument("--backend", default="auto", choices=["auto", "current", "code-worktree", "package-worktree", "unity-clone"]); estimate.add_argument("--package", help="repo-relative target package path for package-worktree sizing"); estimate.set_defaults(run=command_workspace)
    inspect = workspace_sub.add_parser("inspect"); add_common(inspect); inspect.set_defaults(run=command_workspace)
    register = workspace_sub.add_parser("register"); add_common(register); register.add_argument("--workspace-id", required=True); register.add_argument("--path", required=True); register.add_argument("--backend", required=True, choices=["code-worktree", "package-worktree", "unity-clone"]); register.add_argument("--managed-by", default="lemmings", choices=["lemmings", "user"]); register.add_argument("--lifetime", default="task", choices=["task", "phase", "project"]); register.add_argument("--expected-revision", type=int, required=True); register.add_argument("--task-id"); register.add_argument("--phase-id"); register.add_argument("--estimated-gib", type=float, default=0); register.add_argument("--approval", default="not-required"); register.add_argument("--kind", default="writer", choices=["writer", "validation"]); register.add_argument("--allowed-cache", action="append", default=[]); register.set_defaults(run=command_workspace)
    claim = workspace_sub.add_parser("claim"); add_common(claim); claim.add_argument("--workspace-id", required=True); claim.add_argument("--task-id", required=True); claim.add_argument("--base-sha", required=True); claim.add_argument("--integration-head", required=True); claim.add_argument("--branch", required=True); claim.add_argument("--expected-revision", type=int, required=True); claim.add_argument("--phase-id"); claim.set_defaults(run=command_workspace)
    release = workspace_sub.add_parser("release"); add_common(release); release.add_argument("--workspace-id", required=True); release.add_argument("--expected-revision", type=int, required=True); release.add_argument("--task-state", required=True); release.add_argument("--integration-evidence", action="store_true"); release.add_argument("--action", default="pool", choices=["pool", "remove", "retain"]); release.add_argument("--retention-approved", action="store_true"); release.set_defaults(run=command_workspace)
    remove = workspace_sub.add_parser("remove"); add_common(remove); remove.add_argument("--workspace-id", required=True); remove.add_argument("--expected-revision", type=int, required=True); remove.set_defaults(run=command_workspace)
    models = sub.add_parser("models", help="inspect or confirmation-gate per-host model routes"); models_sub = models.add_subparsers(dest="models_command", required=True)
    models_inspect = models_sub.add_parser("inspect"); add_common(models_inspect); models_inspect.set_defaults(run=command_models)
    for name in ("propose", "apply"):
        item = models_sub.add_parser(name); add_common(item); item.add_argument("--catalog", required=True); item.add_argument("--routes", required=True)
        if name == "apply": item.add_argument("--confirm", required=True)
        item.set_defaults(run=command_models)
    recover = models_sub.add_parser("recover", help="confirmation-gate a task-local route plan"); recover_sub = recover.add_subparsers(dest="recover_command", required=True)
    for name in ("propose", "apply"):
        item = recover_sub.add_parser(name); add_common(item); item.add_argument("--task", required=True); item.add_argument("--failure", required=True); item.add_argument("--plan", required=True); item.add_argument("--catalog", action="append", required=True)
        if name == "apply": item.add_argument("--option", required=True); item.add_argument("--confirm", required=True)
        item.set_defaults(run=command_models)
    advance = recover_sub.add_parser("advance"); add_common(advance); advance.add_argument("--task", required=True); advance.add_argument("--failure", required=True); advance.add_argument("--role", required=True, choices=["worker", "reviewer", "explorer"]); advance.add_argument("--expected-revision", type=int, required=True); advance.add_argument("--transient-retries", type=int, default=0); advance.add_argument("--context-reductions", type=int, default=0); advance.set_defaults(run=command_models)
    metrics = sub.add_parser("metrics", help="manage optional offline telemetry and quality imports"); metrics_sub = metrics.add_subparsers(dest="metrics_command", required=True)
    for name in ("off", "basic", "full", "status"):
        item = metrics_sub.add_parser(name); add_common(item); item.set_defaults(run=command_metrics)
    stage = metrics_sub.add_parser("stage"); add_common(stage); stage.add_argument("stage", choices=LIFECYCLE_STAGES); stage.add_argument("--task"); stage.add_argument("--phase"); stage.set_defaults(run=command_metrics)
    finish = metrics_sub.add_parser("finish"); add_common(finish); finish.add_argument("--outcome", required=True, choices=sorted(FINISH_OUTCOMES)); finish.add_argument("--task"); finish.set_defaults(run=command_metrics)
    importing = metrics_sub.add_parser("import"); add_common(importing); importing.add_argument("--task"); importing.add_argument("--file", required=True); importing.set_defaults(run=command_metrics)
    usage = metrics_sub.add_parser("usage"); add_common(usage); usage.add_argument("--host", required=True, choices=["codex", "opencode", "kilo"]); usage.add_argument("--file", required=True); usage.add_argument("--task"); usage.set_defaults(run=command_metrics)
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
