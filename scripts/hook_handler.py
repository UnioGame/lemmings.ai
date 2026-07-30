#!/usr/bin/env python3
"""JSON stdin/stdout hook enforcement for the orchestration plugin.

Hooks are inactive unless a worktree-specific marker exists in Git metadata.
The only mutation is persisting the one-shot Stop continuation flag in that
untracked marker, preventing infinite continuation loops across hook processes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from orchestration_core import as_list, paths_overlap


def decision(action: str, reason: str, *, updates: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"decision": action, "reason": reason}
    if updates:
        result["stateUpdates"] = dict(updates)
    return result


def context_packet_text(payload: Mapping[str, Any]) -> str:
    context = payload.get("contextPacket") or {}
    if not isinstance(context, dict) or not context:
        return ""
    lines = ["Orchestration context packet (do not broaden without a focused expansion):"]
    for key in (
        "taskPacket",
        "agentsInstructions",
        "frozenContracts",
        "directKnowledge",
        "interfaces",
        "tests",
        "dependencyHandoffs",
    ):
        if key not in context:
            continue
        value = context[key]
        rendered = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        lines.append(f"- {key}: {rendered}")
    return "\n".join(lines)


def host_output(
    result: Mapping[str, Any],
    event: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate internal policy decisions to the released Codex hook wire format."""
    action = str(result.get("decision") or "allow")
    reason = str(result.get("reason") or "")
    if event == "PreToolUse":
        if action == "block":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        if action == "warn":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": reason,
                }
            }
        return {}
    if event == "PostToolUse":
        if action in {"warn", "block"}:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": reason,
                }
            }
        return {}
    if event == "SubagentStart":
        context = context_packet_text(payload)
        if action == "block":
            warning = f"Orchestration context policy violation: {reason}"
            return {
                "systemMessage": warning,
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": warning,
                },
            }
        if context:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": context,
                }
            }
        return {}
    if event in {"SubagentStop", "Stop"}:
        if action in {"block", "continue"}:
            return {"decision": "block", "reason": reason}
        if action == "warn":
            return {"systemMessage": reason}
        return {}
    if action in {"warn", "block"}:
        return {"systemMessage": reason}
    return {}


def changed_paths(payload: Mapping[str, Any]) -> list[str]:
    values = payload.get("changedPaths") or payload.get("paths") or []
    if payload.get("file_path"):
        values = [*as_list(values), payload["file_path"]]
    return [str(value) for value in as_list(values)]


def event_name(payload: Mapping[str, Any]) -> str:
    return str(
        payload.get("hook_event_name")
        or payload.get("hookEventName")
        or payload.get("event")
        or ""
    )


def is_sol_model(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value).lower())
    return normalized == "sol" or "gpt56sol" in normalized


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def runtime_location(cwd: str | Path) -> tuple[Path, Path] | None:
    workdir = Path(cwd).resolve()
    root_process = _git(workdir, "rev-parse", "--show-toplevel")
    path_process = _git(workdir, "rev-parse", "--git-path", "codex-orchestration/active.json")
    if root_process.returncode or path_process.returncode:
        return None
    root = Path(root_process.stdout.strip()).resolve()
    marker = Path(path_process.stdout.strip())
    if not marker.is_absolute():
        marker = (root / marker).resolve()
    return root, marker


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_runtime(payload: Mapping[str, Any]) -> tuple[dict[str, Any], Path | None, str | None]:
    cwd = payload.get("cwd") or payload.get("working_directory") or os.getcwd()
    location = runtime_location(str(cwd))
    if location is None:
        return {**payload, "_orchestrationActive": False}, None, None
    root, marker = location
    if not marker.exists():
        return {**payload, "_orchestrationActive": False}, marker, None
    try:
        state = _load_json_object(marker)
        if state.get("active", True) is not True:
            return {**payload, "_orchestrationActive": False}, marker, None
        combined = dict(state)
        combined.update(payload)
        combined["_orchestrationActive"] = True
        combined["_runtimeMarker"] = str(marker)
        combined["_repoRoot"] = str(root)
        artifacts = state.get("artifacts") or {}
        if not isinstance(artifacts, dict):
            raise ValueError("runtime marker artifacts must be an object")
        for name in ("task", "phase", "manifest", "profile"):
            reference = state.get(f"{name}Path") or artifacts.get(name)
            if reference:
                artifact_path = Path(str(reference))
                if not artifact_path.is_absolute():
                    artifact_path = root / artifact_path
                combined[name] = _load_json_object(artifact_path.resolve())
            elif name in state:
                if not isinstance(state[name], dict):
                    raise ValueError(f"runtime marker field {name} must be an object")
                combined[name] = state[name]
        tool_input = payload.get("tool_input") or payload.get("toolInput")
        if isinstance(tool_input, dict):
            for key in ("file_path", "changedPaths", "paths"):
                if key in tool_input and key not in combined:
                    combined[key] = tool_input[key]
            if "requestedModel" not in combined:
                combined["requestedModel"] = tool_input.get("model")
            patch_command = tool_input.get("command")
            if (
                isinstance(patch_command, str)
                and str(payload.get("tool_name") or payload.get("toolName")) == "apply_patch"
            ):
                combined["changedPaths"] = re.findall(
                    r"^\*\*\* (?:Add|Update|Delete) File:\s+(.+?)\s*$",
                    patch_command,
                    flags=re.MULTILINE,
                )
        elif isinstance(tool_input, str) and (
            str(payload.get("tool_name") or payload.get("toolName")) == "apply_patch"
        ):
            combined["changedPaths"] = re.findall(
                r"^\*\*\* (?:Add|Update|Delete) File:\s+(.+?)\s*$",
                tool_input,
                flags=re.MULTILINE,
            )
        return combined, marker, None
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {**payload, "_orchestrationActive": True}, marker, str(error)


def persist_state_updates(marker: Path, updates: Mapping[str, Any]) -> None:
    state = _load_json_object(marker)
    runtime = state.setdefault("runtimeState", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtimeState must be an object")
    runtime.update(updates)
    temporary = marker.with_name(marker.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, marker)


def enforce_pre_tool(payload: Mapping[str, Any]) -> dict[str, Any]:
    tool = str(payload.get("toolName") or payload.get("tool_name") or "")
    task = payload.get("task") or {}
    phase = payload.get("phase") or {}
    manifest = payload.get("manifest") or {}
    profile = payload.get("profile") or {}
    if tool in {"Agent", "spawn_agent"}:
        context_result = enforce_start(payload)
        if context_result["decision"] == "block":
            return context_result
        task_id = task.get("taskId")
        if phase.get("baselineAccepted") is not True or (
            phase.get("contractsFrozen", phase.get("contractFreezeAccepted")) is not True
        ):
            return decision("block", "accepted phase baseline and frozen contracts are required")
        if task.get("state") != "Ready":
            return decision("block", "task must be Ready before spawn")
        entries = manifest.get("tasks") or []
        if not any(entry.get("taskId") == task_id for entry in entries):
            return decision("block", "task is absent from dispatch manifest")
        requested = payload.get("requestedModel")
        if requested and requested != task.get("selectedModel"):
            return decision("block", "requested model differs from selected runtime model")
        if task.get("role", "worker") not in {"reviewer", "explorer", "validator"} and not task.get("worktree"):
            return decision("block", "writer requires an isolated worktree")
        for gate in as_list(task.get("resourceGates")):
            if isinstance(gate, dict) and gate.get("open") is not True:
                return decision("block", f"resource gate is closed: {gate.get('name')}")
        if payload.get("recursiveDelegation") and not profile.get("allowRecursiveDelegation", False):
            return decision("block", "recursive delegation is not permitted")
        for other in payload.get("activeTasks") or []:
            if other.get("taskId") == task_id:
                continue
            for own in as_list(task.get("ownedPaths")):
                for theirs in as_list(other.get("ownedPaths")):
                    if paths_overlap(str(own), str(theirs)):
                        return decision("block", f"owned path overlaps active task {other.get('taskId')}")
        return decision("allow", "dispatch invariants satisfied")
    if tool in {"apply_patch", "Bash", "exec_command"}:
        role = str(task.get("role", "worker"))
        if role == "reviewer":
            return decision("block", "reviewer profile is read-only")
        paths = changed_paths(payload)
        for path in paths:
            if any(paths_overlap(path, str(item)) for item in as_list(task.get("forbiddenPaths"))):
                return decision("block", f"path is forbidden: {path}")
            is_shared = any(paths_overlap(path, str(item)) for item in as_list(task.get("sharedPaths")))
            if is_shared:
                if role not in {"shared-contract-owner", "orchestrator"}:
                    return decision("block", f"shared path requires shared-contract owner: {path}")
                if not is_sol_model(task.get("selectedModel")):
                    return decision("block", f"shared-contract owner must use an approved Sol model: {path}")
                continue
            owned = as_list(task.get("ownedPaths"))
            if owned and not any(paths_overlap(path, str(item)) for item in owned):
                return decision("block", f"path is outside task ownership: {path}")
        if tool in {"Bash", "exec_command"} and not paths:
            policy = str(profile.get("shellUnknownWritePolicy", "warn"))
            return decision("block" if policy == "block" else "warn", "shell write-set cannot be proven")
        return decision("allow", "write invariants satisfied")
    return decision("allow", "tool is outside enforced write/spawn set")


def git_changed_paths(payload: Mapping[str, Any]) -> tuple[list[str], str | None]:
    task = payload.get("task") or {}
    cwd = payload.get("cwd") or task.get("worktree") or payload.get("_repoRoot") or os.getcwd()
    worktree = Path(str(cwd)).resolve()
    commands = (
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    )
    paths: set[str] = set()
    for command in commands:
        process = _git(worktree, *command)
        if process.returncode:
            return [], process.stderr.strip() or f"git {' '.join(command)} failed"
        paths.update(line.strip() for line in process.stdout.splitlines() if line.strip())
    return sorted(paths), None


def enforce_post_tool(payload: Mapping[str, Any]) -> dict[str, Any]:
    task = payload.get("task") or {}
    role = str(task.get("role", "worker"))
    paths = changed_paths(payload)
    inspection_error: str | None = None
    if not paths:
        paths, inspection_error = git_changed_paths(payload)
    if inspection_error:
        return decision(
            "warn",
            f"candidate diff could not be inspected ({inspection_error}); candidate is unverified until corrected",
        )
    violations: dict[str, list[str]] = {
        "forbidden": [],
        "shared-without-owner": [],
        "shared-non-sol-owner": [],
        "outside-owned": [],
    }
    owned = as_list(task.get("ownedPaths"))
    shared = as_list(task.get("sharedPaths"))
    forbidden = as_list(task.get("forbiddenPaths"))
    for path in paths:
        if any(paths_overlap(path, str(item)) for item in forbidden):
            violations["forbidden"].append(path)
            continue
        is_shared = any(paths_overlap(path, str(item)) for item in shared)
        if is_shared:
            if role not in {"shared-contract-owner", "orchestrator"}:
                violations["shared-without-owner"].append(path)
            elif not is_sol_model(task.get("actualModel") or task.get("selectedModel")):
                violations["shared-non-sol-owner"].append(path)
            continue
        if owned and not any(paths_overlap(path, str(item)) for item in owned):
            violations["outside-owned"].append(path)
    details = [
        f"{kind}: {', '.join(items)}"
        for kind, items in violations.items()
        if items
    ]
    if details:
        return decision(
            "warn",
            "ownership violation; candidate is unsuitable until corrected — " + "; ".join(details),
        )
    return decision("allow", "actual candidate diff respects owned/shared/forbidden path sets")


def enforce_start(payload: Mapping[str, Any]) -> dict[str, Any]:
    context = payload.get("contextPacket") or {}
    allowed = {
        "taskPacket",
        "agentsInstructions",
        "frozenContracts",
        "directKnowledge",
        "interfaces",
        "tests",
        "dependencyHandoffs",
    }
    extras = sorted(set(context) - allowed)
    if extras:
        return decision("block", "unbounded context packet fields: " + ", ".join(extras))
    expansion = payload.get("contextExpansion") or {}
    if expansion:
        if int(payload.get("expansionsUsed", 0)) >= 1:
            return decision("block", "focused context expansion budget is exhausted")
        if not expansion.get("symbolOrDecision") or expansion.get("broad"):
            return decision("block", "context expansion must name one focused symbol or decision")
    return decision("allow", "bounded context packet accepted")


def enforce_stop(payload: Mapping[str, Any]) -> dict[str, Any]:
    task = payload.get("task") or {}
    if task.get("implementationTask", True):
        if not as_list(task.get("candidateCommits", task.get("candidateCommit"))):
            return decision("block", "implementation task requires a candidate commit")
    if not payload.get("handoffPresent"):
        return decision("block", "handoff is required")
    if not task.get("actualModel"):
        return decision("block", "handoff requires actual model")
    if task.get("actualModel") != task.get("selectedModel"):
        return decision("block", "actual model differs from selected model")
    if not payload.get("validationEvidence") and not task.get("validationDebt"):
        return decision("block", "validation evidence or owned validation debt is required")
    for debt in as_list(task.get("validationDebt")):
        if not isinstance(debt, dict) or not debt.get("owner") or not debt.get("futureGate"):
            return decision("block", "validation debt requires owner and future gate")
    if payload.get("dirtyWorktree"):
        return decision("block", "task worktree must be clean at handoff")
    violations = payload.get("ownedPathViolations") or []
    if violations:
        return decision("block", "changes outside owned paths: " + ", ".join(map(str, violations)))
    return decision("allow", "handoff invariants satisfied")


def enforce_workflow_stop(payload: Mapping[str, Any]) -> dict[str, Any]:
    task = payload.get("task") or {}
    if task.get("state") in {"Cancelled", "Superseded", "Replan Required"}:
        return decision("allow", "terminal workflow state")
    missing = [
        name
        for name, present in (
            ("review", payload.get("reviewEvidencePresent")),
            ("integration", payload.get("integrationEvidencePresent")),
        )
        if not present
    ]
    if not missing:
        return decision("allow", "workflow evidence complete")
    runtime = payload.get("runtimeState") or {}
    if payload.get("stop_hook_active") or runtime.get("stopContinuationUsed"):
        return decision("allow", "continuation already used; refusing infinite Stop loop")
    return decision(
        "continue",
        "missing required " + " and ".join(missing) + " evidence",
        updates={"stopContinuationUsed": True},
    )


def handle(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("_orchestrationActive") is False:
        return decision("allow", "orchestration inactive: no active runtime marker")
    event = event_name(payload)
    try:
        if event == "PreToolUse":
            return enforce_pre_tool(payload)
        if event == "PostToolUse":
            return enforce_post_tool(payload)
        if event == "SubagentStart":
            return enforce_start(payload)
        if event == "SubagentStop":
            return enforce_stop(payload)
        if event == "Stop":
            return enforce_workflow_stop(payload)
        return decision("warn", f"unknown hook event: {event}")
    except (TypeError, ValueError) as error:
        profile = payload.get("profile") or {}
        policy = str(profile.get("hookFailurePolicy") or (profile.get("hooks") or {}).get("policy", "hybrid"))
        action = "block" if policy == "closed" or (policy == "hybrid" and event == "PreToolUse") else "warn"
        return decision(action, f"hook evaluation failed: {error}")


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        hydrated, marker, marker_error = load_runtime(payload)
        event = event_name(payload)
        if marker_error:
            output = decision(
                "block" if event == "PreToolUse" else "warn",
                f"malformed active orchestration marker: {marker_error}",
            )
        else:
            output = handle(hydrated)
            if marker and output.get("stateUpdates"):
                try:
                    persist_state_updates(marker, output["stateUpdates"])
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    output = decision(
                        "block" if event == "PreToolUse" else "warn",
                        f"cannot persist orchestration runtime state: {error}",
                    )
    except (json.JSONDecodeError, OSError, ValueError) as error:
        hydrated = {}
        event = ""
        output = decision("block", f"invalid hook input: {error}")
    print(json.dumps(host_output(output, event, hydrated), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
