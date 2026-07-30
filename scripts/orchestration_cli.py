#!/usr/bin/env python3
"""Cross-platform CLI for orchestration artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from orchestration_core import (
    ValidationResult,
    build_dispatch,
    cleanup_inventory,
    git_worktrees,
    load_artifact,
    routing_scorecard,
    run_git,
    validate_dispatch,
    validate_cross_artifacts,
    validate_phase,
    validate_profile,
    validate_task,
    validate_task_set,
)


def emit(value: Any, json_output: bool = True) -> None:
    if json_output:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(value)


def result_exit(result: ValidationResult, extra: dict[str, Any] | None = None) -> int:
    payload = result.as_dict()
    if extra:
        payload["data"].update(extra)
    emit(payload)
    return 0 if result.ok else 1


def load_profile(path: str) -> dict[str, Any]:
    return load_artifact(path)


def adapter_from(profile: dict[str, Any] | None) -> str:
    return str((profile or {}).get("taskAdapter", "generic-markdown-v1"))


def load_many(paths: Sequence[str], adapter: str) -> list[dict[str, Any]]:
    return [load_artifact(path, adapter) for path in paths]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestration_cli")
    parser.add_argument("--repo", default=".", help="Repository root")
    sub = parser.add_subparsers(dest="group", required=True)

    profile = sub.add_parser("profile")
    profile_sub = profile.add_subparsers(dest="action", required=True)
    profile_validate = profile_sub.add_parser("validate")
    profile_validate.add_argument("profile", nargs="?")
    profile_validate.add_argument("--profile", dest="profile_option")

    phase = sub.add_parser("phase")
    phase_sub = phase.add_subparsers(dest="action", required=True)
    phase_validate = phase_sub.add_parser("validate")
    phase_validate.add_argument("phase")
    phase_validate.add_argument("--adapter", default="generic-markdown-v1")

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="action", required=True)
    task_validate = task_sub.add_parser("validate")
    task_validate.add_argument("task")
    task_validate.add_argument("--profile")
    task_validate.add_argument("--phase")
    task_validate.add_argument("--all-task", action="append", default=[])

    wave = sub.add_parser("wave")
    wave_sub = wave.add_subparsers(dest="action", required=True)
    wave_plan = wave_sub.add_parser("plan")
    wave_plan.add_argument("phase")
    wave_plan.add_argument("wave")
    wave_plan.add_argument("--profile", required=True)
    wave_plan.add_argument("--task", action="append", required=True)
    wave_plan.add_argument("--output")

    dispatch = sub.add_parser("dispatch")
    dispatch_sub = dispatch.add_subparsers(dest="action", required=True)
    dispatch_validate = dispatch_sub.add_parser("validate")
    dispatch_validate.add_argument("manifest")
    dispatch_validate.add_argument("--profile", required=True)
    dispatch_validate.add_argument("--phase")
    dispatch_validate.add_argument("--task", action="append", required=True)

    status = sub.add_parser("status")
    status.add_argument("--profile", required=True)
    status.add_argument("--phase")
    status.add_argument("--task", action="append", default=[])
    status.add_argument("--manifest", action="append", default=[])
    status.add_argument("--handoff", action="append", default=[])
    status.add_argument("--review", action="append", default=[])
    status.add_argument("--json", action="store_true")

    worktree = sub.add_parser("worktree")
    worktree_sub = worktree.add_subparsers(dest="action", required=True)
    wt_allocate = worktree_sub.add_parser("allocate")
    wt_allocate.add_argument("path")
    wt_allocate.add_argument("branch")
    wt_allocate.add_argument("--base", default="HEAD")
    wt_allocate.add_argument("--create-branch", action="store_true")
    worktree_sub.add_parser("status")
    wt_release = worktree_sub.add_parser("release")
    wt_release.add_argument("path")
    wt_release.add_argument("--execute", action="store_true")
    wt_release.add_argument("--force", action="store_true")

    cleanup = sub.add_parser("cleanup")
    cleanup_sub = cleanup.add_subparsers(dest="action", required=True)
    cleanup_inspect = cleanup_sub.add_parser("inspect")
    cleanup_inspect.add_argument("--profile")
    cleanup_inspect.add_argument("--task", action="append", default=[])

    routing = sub.add_parser("routing")
    routing_sub = routing.add_subparsers(dest="action", required=True)
    scorecard = routing_sub.add_parser("scorecard")
    scorecard.add_argument("phase")
    scorecard.add_argument("--profile")
    scorecard.add_argument("--task", action="append", required=True)

    runtime = sub.add_parser("runtime")
    runtime_sub = runtime.add_subparsers(dest="action", required=True)
    runtime_activate = runtime_sub.add_parser("activate")
    runtime_activate.add_argument("--state", required=True)
    runtime_sub.add_parser("status")
    runtime_sub.add_parser("deactivate")
    return parser


def runtime_marker(repo: Path) -> Path:
    process = run_git(repo, "rev-parse", "--git-path", "codex-orchestration/active.json")
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or "cannot resolve orchestration runtime marker")
    marker = Path(process.stdout.strip())
    if not marker.is_absolute():
        marker = repo / marker
    return marker.resolve()


def main(argv: Sequence[str] | None = None) -> int:
    normalized = list(argv if argv is not None else sys.argv[1:])
    # Consumer scripts historically put --repo after the leaf command.  Keep
    # accepting that form while argparse retains one canonical global option.
    repo_override: str | None = None
    if "--repo" in normalized:
        index = normalized.index("--repo")
        if index + 1 >= len(normalized):
            print(json.dumps({"ok": False, "error": "--repo requires a value"}))
            return 2
        repo_override = normalized[index + 1]
        del normalized[index : index + 2]
    args = build_parser().parse_args(normalized)
    if repo_override is not None:
        args.repo = repo_override
    repo = Path(args.repo).resolve()
    try:
        if args.group == "profile":
            profile_path = args.profile_option or args.profile
            if not profile_path:
                emit({"ok": False, "error": "profile validate requires PROFILE or --profile"})
                return 2
            return result_exit(validate_profile(load_profile(profile_path)))
        if args.group == "phase":
            return result_exit(validate_phase(load_artifact(args.phase, args.adapter)))
        if args.group == "task":
            profile = load_profile(args.profile) if args.profile else None
            adapter = adapter_from(profile)
            task = load_artifact(args.task, adapter)
            phase = load_artifact(args.phase, adapter) if args.phase else None
            result = validate_task(task, phase)
            if args.all_task:
                result.extend(validate_task_set(load_many(args.all_task, adapter)))
            return result_exit(result)
        if args.group == "wave":
            profile = load_profile(args.profile)
            adapter = adapter_from(profile)
            phase = load_artifact(args.phase, adapter)
            tasks = load_many(args.task, adapter)
            manifest, result = build_dispatch(profile, phase, tasks, args.wave)
            if args.output and result.ok:
                Path(args.output).write_text(
                    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )
            return result_exit(result, {"manifest": manifest, "output": args.output})
        if args.group == "dispatch":
            profile = load_profile(args.profile)
            adapter = adapter_from(profile)
            manifest = load_artifact(args.manifest, adapter)
            phase = load_artifact(args.phase, adapter) if args.phase else None
            tasks = load_many(args.task, adapter)
            return result_exit(validate_dispatch(manifest, tasks, phase))
        if args.group == "status":
            profile = load_profile(args.profile)
            adapter = adapter_from(profile)
            phase = load_artifact(args.phase, adapter) if args.phase else None
            tasks = load_many(args.task, adapter)
            manifests = load_many(args.manifest, adapter)
            handoffs = load_many(args.handoff, adapter)
            reviews = load_many(args.review, adapter)
            result = validate_profile(profile)
            if phase:
                result.extend(validate_phase(phase))
            for task in tasks:
                result.extend(validate_task(task, phase))
            result.extend(validate_task_set(tasks))
            roadmap_path = Path(str(profile["roadmap"]))
            if not roadmap_path.is_absolute():
                roadmap_path = repo / roadmap_path
            roadmap_text: str | None = None
            if roadmap_path.exists():
                roadmap_text = roadmap_path.read_text(encoding="utf-8-sig")
            else:
                result.error("roadmap.missing", f"Profile roadmap does not exist: {roadmap_path}")
            result.extend(
                validate_cross_artifacts(
                    tasks,
                    manifests=manifests,
                    handoffs=handoffs,
                    reviews=reviews,
                    roadmap_text=roadmap_text,
                    repo=repo,
                )
            )
            payload = result.as_dict()
            payload["data"].update(
                {
                    "phase": phase,
                    "roadmap": str(roadmap_path),
                    "manifests": manifests,
                    "handoffs": handoffs,
                    "reviews": reviews,
                    "tasks": [
                        {
                            "taskId": task.get("taskId"),
                            "state": task.get("state"),
                            "selectedModel": task.get("selectedModel"),
                            "actualModel": task.get("actualModel"),
                            "validationDebt": task.get("validationDebt", []),
                        }
                        for task in tasks
                    ],
                    "worktrees": git_worktrees(repo),
                }
            )
            emit(payload, True if args.json else True)
            return 0 if result.ok else 1
        if args.group == "worktree":
            if args.action == "status":
                emit({"ok": True, "worktrees": git_worktrees(repo)})
                return 0
            target = Path(args.path).resolve()
            if args.action == "allocate":
                if target.exists():
                    emit({"ok": False, "error": f"target already exists: {target}"})
                    return 1
                command = ["worktree", "add"]
                command.extend(["-b", args.branch] if args.create_branch else [])
                command.extend([str(target), args.base if args.create_branch else args.branch])
                process = run_git(repo, *command)
                emit(
                    {
                        "ok": process.returncode == 0,
                        "path": str(target),
                        "branch": args.branch,
                        "stdout": process.stdout.strip(),
                        "stderr": process.stderr.strip(),
                    }
                )
                return process.returncode
            status = run_git(target, "status", "--porcelain")
            if status.returncode:
                emit({"ok": False, "error": status.stderr.strip()})
                return 1
            if status.stdout.strip() and not args.force:
                emit({"ok": False, "error": "worktree is dirty; release refused"})
                return 1
            recommendation = ["git", "-C", str(repo), "worktree", "remove", "--", str(target)]
            if not args.execute:
                emit({"ok": True, "executed": False, "recommendation": recommendation})
                return 0
            process = run_git(repo, "worktree", "remove", *(["--force"] if args.force else []), "--", str(target))
            emit({"ok": process.returncode == 0, "executed": True, "stderr": process.stderr.strip()})
            return process.returncode
        if args.group == "cleanup":
            profile = load_profile(args.profile) if args.profile else None
            tasks = load_many(args.task, adapter_from(profile))
            emit({"ok": True, "inventory": cleanup_inventory(repo, tasks)})
            return 0
        if args.group == "routing":
            profile = load_profile(args.profile) if args.profile else None
            tasks = load_many(args.task, adapter_from(profile))
            selected = [task for task in tasks if str(task.get("phaseId")) == str(args.phase)]
            emit({"ok": True, "phaseId": args.phase, **routing_scorecard(selected)})
            return 0
        if args.group == "runtime":
            marker = runtime_marker(repo)
            if args.action == "status":
                if not marker.exists():
                    emit({"ok": True, "active": False, "marker": str(marker)})
                    return 0
                state = load_artifact(marker)
                emit(
                    {
                        "ok": True,
                        "active": state.get("active", True) is True,
                        "marker": str(marker),
                        "state": state,
                    }
                )
                return 0
            if args.action == "activate":
                state = load_artifact(args.state)
                state.setdefault("schemaVersion", 1)
                state["active"] = True
                state.setdefault("runtimeState", {})
                marker.parent.mkdir(parents=True, exist_ok=True)
                temporary = marker.with_name(marker.name + ".tmp")
                temporary.write_text(
                    json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(marker)
                emit({"ok": True, "active": True, "marker": str(marker)})
                return 0
            if not marker.exists():
                emit({"ok": True, "active": False, "marker": str(marker), "changed": False})
                return 0
            marker.unlink()
            emit({"ok": True, "active": False, "marker": str(marker), "changed": True})
            return 0
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as error:
        emit({"ok": False, "error": str(error)})
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
