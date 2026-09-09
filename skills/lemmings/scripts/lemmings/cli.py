"""Contract validation, manager-directed workspaces, routing, and offline metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    git,
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
from .invocations import accept_result, record_invocation, task_lock
from .bundle import skill_root
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
    return load_optional(repo, None, PROFILE_PATH) or read_object(skill_root(repo) / "defaults.json")


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
    task = load_optional(repo, task_value, ((as_list((marker_data or {}).get("taskPaths")) or [None])[0]))
    phase = load_optional(repo, getattr(args, "phase", None), (marker_data or {}).get("phasePath"))
    review_reference = getattr(args, "review", None) or (marker_data or {}).get("reviewPath")
    review = load_optional(repo, review_reference)
    if review is not None and review_reference:
        review["_evidencePath"] = str(review_reference)
    return repo, profile, task, phase, review, marker_data


def runtime_findings(repo: Path, marker: dict[str, Any] | None) -> ValidationResult:
    result = ValidationResult()
    if marker and marker.get("schemaVersion") != SCHEMA_VERSION:
        message = "schemaVersion 2 is unsupported by Lemmings 4.0; replace the legacy bundle" if marker.get("schemaVersion") == 2 else f"unsupported schemaVersion: {marker.get('schemaVersion')!r}; expected 4"
        result.error("runtime.schema", message)
    if marker:
        task_paths = as_list(marker.get("taskPaths"))
        if not task_paths:
            result.error("runtime.tasks_missing", "active runtime requires taskPaths")
        for reference in task_paths:
            path = resolve_path(repo, str(reference))
            if not path or not path.is_file():
                result.error("runtime.task_missing", f"active runtime task does not exist: {reference}")
    return result


def _tree_fingerprint(root: Path) -> str | None:
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc"):
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
        "lemmings.__version__": next((line.split('=', 1)[1].strip().strip('"') for line in (package / "skills" / "lemmings" / "scripts" / "lemmings" / "__init__.py").read_text(encoding="utf-8").splitlines() if line.startswith("__version__ = ")), None),
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
    def instructions_only(data: bytes) -> list[str]:
        return [line for line in data.decode("utf-8-sig").splitlines() if line.strip() and line.partition("=")[0].strip() not in {"model", "model_reasoning_effort"}]
    if set(expected) != set(actual) or any(instructions_only(expected[name]) != instructions_only(actual[name]) for name in actual):
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
            result.extend(validate_batch(repo, tasks.values(), phase, args.batch, profile, available_slots=args.available_slots, active_writers=args.active_writers, active_readers=args.active_readers))
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
    task_values = [_artifact_reference(repo, value, "task") for value in args.task]
    for _, task in task_values:
        task_result = validate_task(task, profile)
        if not task_result.ok:
            raise ValueError(task_result.findings[0].message)
        if task.get("state") not in {"Ready", "Active", "Repair", "Candidate", "Accepted", "Blocked"}:
            raise ValueError("runtime activate requires Tasks in a working lifecycle state")
    modes = {detect_mode(profile, task) for _, task in task_values}
    if modes == {"simple"}:
        marker.unlink(missing_ok=True)
        emit({"ok": True, "active": False, "reason": "Simple mode does not use a runtime marker"})
        return 0
    state: dict[str, Any] = {"schemaVersion": SCHEMA_VERSION, "profilePath": PROFILE_PATH, "taskPaths": [reference for reference, _ in task_values]}
    task = task_values[0][1]
    mode = "strict" if "strict" in modes else "standard"
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
    elif args.workspace_command == "prepare":
        from .workspace import prepare_workspace
        emit(prepare_workspace(repo, task_path=resolve_path(repo, args.task), destination=resolve_path(repo, args.destination), branch=args.branch, expected_revision=args.expected_revision, approval=args.approval))
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
        emit(release_workspace(repo, workspace_id=args.workspace_id, expected_revision=args.expected_revision, task_state=args.task_state, integration_evidence=args.integration_evidence, action=args.action, retention_approved=args.retention_approved, profile=profile, task_path=resolve_path(repo, args.task), task_revision=args.task_revision))
    elif args.workspace_command == "remove":
        emit(remove_workspace(repo, workspace_id=args.workspace_id, expected_revision=args.expected_revision, task_path=resolve_path(repo, args.task), task_revision=args.task_revision))
    return 0


def command_models(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if args.models_command in {"scan", "probe"}:
        from .discovery import scan_providers, probe_route
        if args.models_command == "scan":
            catalog = load_optional(repo, args.host_catalog)
            value = scan_providers(repo, offline=args.offline, host_catalog=catalog)
            if args.output:
                write_object(resolve_path(repo, args.output), value)
            if not args.details:
                value = {"schemaVersion":4, "scannedAt":value.get("scannedAt"), "providerCount":len(value.get("providers",[])),
                         "providers":value.get("providers",[])[:12], "routeCount":len(value.get("routes",[])),
                         "diagnostics":value.get("diagnostics",[])[:12], "diagnosticCount":len(value.get("diagnostics",[])),
                         "inventory":"~/.lemmings/state.json", "next":"models inspect --inventory --provider ID"}
        else:
            value = probe_route(read_object(resolve_path(repo, args.route)), repo=repo)
        emit(value)
        return 0
    if args.models_command == "inspect" and args.inventory:
        state_path = Path.home() / ".lemmings/state.json"
        state = read_object(state_path) if state_path.is_file() else {}
        inventory = state.get("inventory") or {}
        routes = inventory.get("routes", [])
        if args.provider:
            routes = [r for r in routes if r.get("providerId") == args.provider]
        limit = min(100, max(1, args.limit))
        emit({"routes":routes[:limit], "total":len(routes), "omitted":max(0,len(routes)-limit), "inventory":"~/.lemmings/state.json"})
        return 0
    if getattr(args, "name", None) or getattr(args, "proposal", None):
        from .profiles import build_profile_proposal, apply_profile_proposal
        if args.models_command == "propose":
            if not args.routes:
                raise ValueError("named proposal requires --routes JSON")
            value = build_profile_proposal(repo, args.name, read_object(resolve_path(repo, args.routes)))
        else:
            if not args.proposal:
                raise ValueError("named apply requires --proposal JSON")
            value = apply_profile_proposal(repo, read_object(resolve_path(repo, args.proposal)), args.confirm)
        if args.output:
            write_object(resolve_path(repo, args.output), value)
        emit(value)
        return 0
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
    if not args.catalog or not args.routes:
        raise ValueError("legacy model routing requires --catalog and --routes; named profiles use --name/--proposal")
    catalog = read_object(resolve_path(repo, args.catalog))
    routes = read_object(resolve_path(repo, args.routes))
    if args.models_command == "propose":
        emit({"ok": True, **build_proposal(config, catalog, routes)})
    else:
        emit(apply_proposal(config_path, catalog, routes, args.confirm))
    return 0


def command_profiles(args: argparse.Namespace) -> int:
    from .profiles import inspect_profiles, use_profile, resolve_profile
    repo = Path(args.repo).resolve()
    if args.profiles_command == "list":
        value = inspect_profiles(repo)
    elif args.profiles_command == "use":
        value = use_profile(repo, args.name)
    else:
        value = resolve_profile(repo, args.name)
    emit(value)
    return 0


def command_rules(args: argparse.Namespace) -> int:
    from .rules import resolve_rules
    emit(resolve_rules(Path(args.repo).resolve(), paths=args.path, technologies=args.technology, platforms=args.platform))
    return 0


def command_run(args: argparse.Namespace) -> int:
    from .runners import build_launch, run_invocation
    from .invocations import find_invocation, validate_dispatch
    repo = Path(args.repo).resolve()
    task = read_object(resolve_path(repo, args.task))
    invocation = find_invocation(task, args.invocation_id)
    if not invocation or invocation.get("taskRevision") != task.get("revision"):
        raise ValueError("run requires a current saved invocation")
    validate_dispatch(repo, task, load_profile(repo, args.profile), invocation)
    if invocation["role"] == "worker":
        workspace = task.get("workspace") or {}
        if workspace.get("destination") and Path(workspace["destination"]).resolve() != repo:
            raise ValueError("run repository differs from the recorded worker destination")
        commits = task.get("commits") or {}
        fixes = commits.get("fix") or []
        expected_head = (fixes[-1] if fixes else commits.get("candidate")) if task.get("state") == "Repair" else task.get("baseSha")
        expected_head = expected_head or task.get("baseSha")
        head = git(repo, "rev-parse", "HEAD")
        if head.returncode or head.stdout.strip() != expected_head:
            raise ValueError("run worker HEAD differs from its recorded base or repair candidate")
    route = read_object(resolve_path(repo, args.route))
    choices = invocation.get("roleRoutes", [])
    from .contracts import route_name, current_recovery_route
    recovery = current_recovery_route(task, invocation["role"])
    assigned = invocation.get("assignedModel") or (task.get("models") or {}).get("assigned")
    assigned_host = invocation.get("assignedHost") or (task.get("models") or {}).get("hostId")
    native = route.get("executor") == "native" and assigned == "current-host/default"
    if not native and (route_name(route) != assigned or route.get("hostId") != assigned_host):
        raise ValueError("run route differs from the manager-assigned model")
    explicit_pin = invocation["role"] == task.get("role") and (task.get("models") or {}).get("requested") == assigned and route_name(route) == assigned
    def matches(candidate):
        return candidate and route_name(candidate) == route_name(route) and candidate.get("hostId") == route.get("hostId") and all(not candidate.get(k) or candidate[k] == route.get(k) for k in ("executor", "profileName", "protocol"))
    if choices and not any(matches(c) for c in choices) and not matches(recovery) and not explicit_pin:
        raise ValueError("run route is outside the frozen approved chain")
    value = build_launch(repo, invocation, route) if args.dry_run else run_invocation(repo, invocation, route)
    # Launch env may contain local credentials; never emit it, even in dry-run.
    if args.dry_run:
        value = {k:v for k,v in value.items() if k not in {"env", "stdin"}}
    if args.output:
        write_object(resolve_path(repo, args.output), value)
    emit(value)
    return 0 if value.get("status", "succeeded") == "succeeded" else 1


def _task_arg(repo: Path, value: str | None) -> tuple[str | None, dict[str, Any] | None, str | None]:
    if not value:
        try:
            marker = runtime_marker(repo)
            if marker.is_file():
                references = as_list(read_object(marker).get("taskPaths"))
                reference = references[0] if len(references) == 1 else None
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
        if task and task.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("schemaVersion 2 is unsupported by Lemmings 4.0; replace the legacy bundle" if task.get("schemaVersion") == 2 else "metrics stage requires a schema-v4 Task")
        event = record_event(repo, "run_started", source="cli", task_id=task_id, phase_id=(phase or {}).get("phaseId"), data={"mode": (task or {}).get("resolvedMode")}) if args.stage == "discover" else None
        emit({"ok": True, "recorded": bool(event), "event": event, "reason": None if event else "only run_started at discover is recorded"})
    elif action == "finish":
        if task and task.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("schemaVersion 2 is unsupported by Lemmings 4.0; replace the legacy bundle" if task.get("schemaVersion") == 2 else "metrics finish requires a schema-v4 Task")
        integrated = bool(task and task.get("state") == "Integrated" and (task.get("close") or {}).get("integrationEvidence"))
        event = record_event(repo, "task.integrated" if integrated else "run_finished", source="cli", task_id=task_id, data={"outcome": args.outcome, "task": task if integrated else None}, allow_finished_binding=True)
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


def command_doctor(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    profile = load_profile(repo)
    result = ValidationResult(data={
        "python": platform.python_version(),
        "runtimeVersion": DISTRIBUTION_VERSION,
        "runtimePath": str(Path(__file__).resolve().parent),
    })
    if sys.version_info < (3, 10):
        result.error("doctor.python", "Python 3.10 or newer is required")
    if profile is None:
        result.error("doctor.profile", f"{PROFILE_PATH} is missing")
    else:
        result.extend(validate_profile(profile))
    runtime = Path(__file__).resolve().parent
    skill = skill_root(repo)
    required = [
        *(runtime / name for name in ("contracts.py", "hooks.py", "invocations.py", "workspace.py")),
        skill / "SKILL.md", skill / "defaults.json", skill / "scripts/run.py",
        *(skill / "templates" / name for name in ("task.json", "phase.json", "review.json")),
    ]
    if any(not path.is_file() for path in required):
        result.error("doctor.bundle", "installed runtime or templates are incomplete")
    if profile is not None and (profile.get("game") or {}).get("engine") == "unity":
        project = repo / str((profile.get("game") or {}).get("projectPath") or "")
        if not (project / "Assets").is_dir() or not (project / "Packages/manifest.json").is_file() or not (project / "ProjectSettings/ProjectVersion.txt").is_file():
            result.error("doctor.project", "configured game.projectPath is not a Unity project")
    emit(result.as_dict())
    return 0 if result.ok else 1


def command_invocation(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    profile = load_profile(repo, args.profile)
    task_path = resolve_path(repo, args.task)
    if profile is None or task_path is None or not task_path.is_file():
        raise ValueError("invocation requires an existing profile and Task")
    if args.invocation_command == "create":
        emit(record_invocation(repo, task_path, profile, args.role, args.attempt, args.expected_revision, args.objective, preset=args.preset, freeze=True))
    else:
        result_path = resolve_path(repo, args.result)
        if result_path is None or not result_path.is_file():
            raise ValueError("invocation accept requires an existing AgentResult")
        emit(accept_result(repo, task_path, profile, read_object(result_path), args.expected_revision))
    return 0


def _integration_tree_findings(repo: Path, task_path: Path, task: Mapping[str, Any], head: str) -> list[str]:
    from .contracts import path_matches
    current = git(repo, "rev-parse", "HEAD")
    if current.returncode or current.stdout.strip() != head:
        return ["HEAD changed during integration validation"]
    tracked = git(repo, "diff", "--name-only", "HEAD", "--")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard")
    if tracked.returncode or untracked.returncode:
        return ["cannot verify integration tree"]
    try:
        task_ref = task_path.resolve().relative_to(repo).as_posix()
    except ValueError:
        task_ref = None
    allowed = (task.get("validation") or {}).get("allowedOutputs") or []
    paths = set(tracked.stdout.splitlines() + untracked.stdout.splitlines())
    dirty = [path for path in sorted(paths) if path not in {task_ref, task_ref + ".lock" if task_ref else None} and not any(path_matches(path, str(rule)) for rule in allowed)]
    return ["integration tree has changes outside canonical Task/allowed outputs: " + ", ".join(dirty[:8])] if dirty else []


def command_integration(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    task_path = resolve_path(repo, args.task)
    if task_path is None or not task_path.is_file():
        raise ValueError("integration validate requires an existing Task")
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != args.expected_revision:
            raise ValueError(f"stale Task revision: expected {args.expected_revision}, actual {task.get('revision')}")
        head_process = git(repo, "rev-parse", "HEAD")
        head = head_process.stdout.strip() if not head_process.returncode else ""
        close = task.get("close") if isinstance(task.get("close"), dict) else {}
        if not head or close.get("mergeCommit") != head:
            raise ValueError("integration validation requires HEAD to equal close.mergeCommit")
        commands = [str(value).strip() for value in as_list((task.get("validation") or {}).get("commands")) if str(value).strip()]
        if not commands:
            raise ValueError("integration validation requires declared validation.commands")
        tree_findings = _integration_tree_findings(repo, task_path, task, head)
        if tree_findings:
            raise ValueError(tree_findings[0])
        evidence = []
        for command in commands:
            common_value = git(repo, "rev-parse", "--git-common-dir").stdout.strip()
            common = Path(common_value) if Path(common_value).is_absolute() else repo / common_value
            artifact = common.resolve() / "lemmings" / "validation" / (head + "-" + hashlib.sha256(command.encode()).hexdigest()[:12] + ".log")
            artifact.parent.mkdir(parents=True, exist_ok=True)
            with artifact.open("wb") as output:
                process = subprocess.run(command, cwd=repo, shell=True, stdout=output, stderr=subprocess.STDOUT, check=False)
            size = artifact.stat().st_size
            with artifact.open("rb") as output:
                output.seek(max(0, size - 4096))
                excerpt = output.read(4096).decode("utf-8", errors="replace")
            evidence.append({"headSha": head, "command": command, "passed": process.returncode == 0, "exitCode": process.returncode,
                             "diagnostics": {"tail": excerpt, "totalBytes": size, "omittedBytes": max(0, size - 4096), "artifact": "git-common-dir:lemmings/validation/" + artifact.name}})
        tree_findings = _integration_tree_findings(repo, task_path, task, head)
        if tree_findings:
            for item in evidence:
                item["passed"] = False
                item["treeFindings"] = tree_findings
        close["integrationEvidence"] = evidence
        task["close"] = close
        task["revision"] = args.expected_revision + 1
        write_object(task_path, task)
    output = {"ok": all(item["passed"] for item in evidence), "taskId": task.get("taskId"), "revision": task["revision"], "integrationEvidence": evidence}
    emit(output)
    return 0 if output["ok"] else 1


def add_common(parser: argparse.ArgumentParser, artifacts: bool = False) -> None:
    parser.add_argument("--repo", default=".")
    parser.add_argument("--profile", help="existing JSON configuration path")
    if artifacts:
        parser.add_argument("--task")
        parser.add_argument("--phase")
        parser.add_argument("--review")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lemmings", description="Optional tooling for the Lemmings smart skill")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate lifecycle contracts once per artifact"); add_common(check); check.add_argument("--task", action="append"); check.add_argument("--phase"); check.add_argument("--review"); check.add_argument("--all", action="store_true"); check.add_argument("--distribution", action="store_true", help="also compare the installed bundle with the package"); check.add_argument("--dispatchable", action="store_true"); check.add_argument("--batch", action="append"); check.add_argument("--available-slots", type=int); check.add_argument("--active-writers", type=int, default=0); check.add_argument("--active-readers", type=int, default=0); check.set_defaults(run=command_check)
    doctor = sub.add_parser("doctor", help="verify the installed self-contained runtime"); doctor.add_argument("--repo", default="."); doctor.set_defaults(run=command_doctor)
    invocation = sub.add_parser("invocation", help="persist dispatch and accept matching AgentResult"); invocation_sub = invocation.add_subparsers(dest="invocation_command", required=True)
    invocation_create = invocation_sub.add_parser("create"); add_common(invocation_create); invocation_create.add_argument("--task", required=True); invocation_create.add_argument("--role", required=True, choices=["worker", "reviewer", "explorer"]); invocation_create.add_argument("--attempt", type=int, required=True); invocation_create.add_argument("--expected-revision", type=int, required=True); invocation_create.add_argument("--objective"); invocation_create.add_argument("--preset", help="named role preset for this new Task"); invocation_create.set_defaults(run=command_invocation)
    invocation_accept = invocation_sub.add_parser("accept"); add_common(invocation_accept); invocation_accept.add_argument("--task", required=True); invocation_accept.add_argument("--result", required=True); invocation_accept.add_argument("--expected-revision", type=int, required=True); invocation_accept.set_defaults(run=command_invocation)
    integration = sub.add_parser("integration", help="run declared checks on the exact merged tree"); integration_sub = integration.add_subparsers(dest="integration_command", required=True)
    integration_validate = integration_sub.add_parser("validate"); integration_validate.add_argument("--repo", default="."); integration_validate.add_argument("--task", required=True); integration_validate.add_argument("--expected-revision", type=int, required=True); integration_validate.set_defaults(run=command_integration)
    status = sub.add_parser("status", help="inspect runtime and contract status"); add_common(status, True); status.set_defaults(run=command_status)
    runtime = sub.add_parser("runtime", help="activate, inspect, or deactivate schema-v4 enforcement"); runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_activate = runtime_sub.add_parser("activate"); runtime_activate.add_argument("--repo", default="."); runtime_activate.add_argument("--task", action="append", required=True); runtime_activate.add_argument("--phase"); runtime_activate.add_argument("--review"); runtime_activate.set_defaults(run=command_runtime)
    for name in ("status", "deactivate"):
        item = runtime_sub.add_parser(name); item.add_argument("--repo", default="."); item.set_defaults(run=command_runtime)
    workspace = sub.add_parser("workspace", help="estimate or inspect workspaces"); workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    estimate = workspace_sub.add_parser("estimate"); add_common(estimate); estimate.add_argument("--backend", default="auto", choices=["auto", "current", "code-worktree", "package-worktree", "unity-clone"]); estimate.add_argument("--package", help="repo-relative target package path for package-worktree sizing"); estimate.set_defaults(run=command_workspace)
    prepare = workspace_sub.add_parser("prepare"); add_common(prepare); prepare.add_argument("--task", required=True); prepare.add_argument("--destination", required=True); prepare.add_argument("--branch", required=True); prepare.add_argument("--expected-revision", type=int, required=True); prepare.add_argument("--approval", default="not-required"); prepare.set_defaults(run=command_workspace)
    inspect = workspace_sub.add_parser("inspect"); add_common(inspect); inspect.set_defaults(run=command_workspace)
    register = workspace_sub.add_parser("register"); add_common(register); register.add_argument("--workspace-id", required=True); register.add_argument("--path", required=True); register.add_argument("--backend", required=True, choices=["code-worktree", "package-worktree", "unity-clone"]); register.add_argument("--managed-by", default="lemmings", choices=["lemmings", "user"]); register.add_argument("--lifetime", default="task", choices=["task", "phase", "project"]); register.add_argument("--expected-revision", type=int, required=True); register.add_argument("--task-id"); register.add_argument("--phase-id"); register.add_argument("--estimated-gib", type=float, default=0); register.add_argument("--approval", default="not-required"); register.add_argument("--kind", default="writer", choices=["writer", "validation"]); register.add_argument("--allowed-cache", action="append", default=[]); register.set_defaults(run=command_workspace)
    claim = workspace_sub.add_parser("claim"); add_common(claim); claim.add_argument("--workspace-id", required=True); claim.add_argument("--task-id", required=True); claim.add_argument("--base-sha", required=True); claim.add_argument("--integration-head", required=True); claim.add_argument("--branch", required=True); claim.add_argument("--expected-revision", type=int, required=True); claim.add_argument("--phase-id"); claim.set_defaults(run=command_workspace)
    release = workspace_sub.add_parser("release"); add_common(release); release.add_argument("--workspace-id", required=True); release.add_argument("--expected-revision", type=int, required=True); release.add_argument("--task-state", help="legacy hint; canonical Task evidence is required"); release.add_argument("--task", required=True); release.add_argument("--task-revision", type=int, required=True); release.add_argument("--integration-evidence", action="store_true"); release.add_argument("--action", default="pool", choices=["pool", "remove", "retain"]); release.add_argument("--retention-approved", action="store_true"); release.set_defaults(run=command_workspace)
    remove = workspace_sub.add_parser("remove"); add_common(remove); remove.add_argument("--workspace-id", required=True); remove.add_argument("--expected-revision", type=int, required=True); remove.add_argument("--task", required=True); remove.add_argument("--task-revision", type=int, required=True); remove.set_defaults(run=command_workspace)
    models = sub.add_parser("models", help="inspect or confirmation-gate per-host model routes"); models_sub = models.add_subparsers(dest="models_command", required=True)
    models_inspect = models_sub.add_parser("inspect"); add_common(models_inspect); models_inspect.add_argument("--inventory", action="store_true"); models_inspect.add_argument("--provider"); models_inspect.add_argument("--limit", type=int, default=20); models_inspect.set_defaults(run=command_models)
    for name in ("propose", "apply"):
        item = models_sub.add_parser(name); add_common(item); item.add_argument("--catalog"); item.add_argument("--routes"); item.add_argument("--name"); item.add_argument("--proposal"); item.add_argument("--output")
        if name == "apply": item.add_argument("--confirm", required=True)
        item.set_defaults(run=command_models)
    scan = models_sub.add_parser("scan", help="discover providers without inference"); add_common(scan); scan.add_argument("--offline", action="store_true"); scan.add_argument("--host-catalog"); scan.add_argument("--output"); scan.add_argument("--details", action="store_true", help="emit the complete sanitized inventory"); scan.set_defaults(run=command_models)
    probe = models_sub.add_parser("probe", help="explicit targeted inference/access probe"); add_common(probe); probe.add_argument("--route", required=True); probe.set_defaults(run=command_models)
    profiles = sub.add_parser("profiles", help="list, explain and select named role profiles"); profiles_sub = profiles.add_subparsers(dest="profiles_command", required=True)
    for name in ("list", "inspect", "use"):
        item = profiles_sub.add_parser(name); add_common(item)
        if name != "list": item.add_argument("name")
        item.set_defaults(run=command_profiles)
    rules = sub.add_parser("rules", help="explain task-scoped optional rule selection"); rules_sub = rules.add_subparsers(dest="rules_command", required=True)
    explain = rules_sub.add_parser("explain"); add_common(explain); explain.add_argument("--path", action="append"); explain.add_argument("--technology", action="append"); explain.add_argument("--platform", action="append"); explain.set_defaults(run=command_rules)
    run = sub.add_parser("run", help="execute one manager-assigned saved invocation"); add_common(run); run.add_argument("--task", required=True); run.add_argument("--invocation-id", required=True); run.add_argument("--route", required=True); run.add_argument("--dry-run", action="store_true"); run.add_argument("--output"); run.set_defaults(run=command_run)
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
