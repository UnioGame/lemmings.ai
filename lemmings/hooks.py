"""Codex hook policy for an active Lemmings runtime."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lemmings.core import as_list, candidate_head, paths_overlap, read_object, runtime_marker
else:
    from .core import as_list, candidate_head, paths_overlap, read_object, runtime_marker

READ_ONLY_COMMANDS = {
    "rg", "grep", "find", "ls", "dir", "pwd", "type", "cat", "head", "tail",
    "get-content", "get-childitem", "get-location", "select-string", "select-object",
}
READ_ONLY_GIT = {
    "status", "diff", "log", "show", "branch", "rev-parse", "merge-base", "ls-files",
    "worktree", "remote", "tag", "describe",
}


def decision(action: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"decision": action, "reason": reason, **extra}


def event_name(payload: Mapping[str, Any]) -> str:
    return str(payload.get("hook_event_name") or payload.get("event") or "")


def changed_paths(payload: Mapping[str, Any]) -> list[str]:
    explicit = payload.get("changedPaths") or payload.get("paths")
    if explicit:
        return [str(item) for item in as_list(explicit)]
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if isinstance(tool_input, dict):
        if tool_input.get("file_path"):
            return [str(tool_input["file_path"])]
        patch = tool_input.get("patch") or tool_input.get("command") or ""
    else:
        patch = str(tool_input)
    return re.findall(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", str(patch), re.MULTILINE)


def shell_command(payload: Mapping[str, Any]) -> str:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    return str(tool_input.get("command", "")) if isinstance(tool_input, dict) else str(tool_input)


def is_read_only_shell(command: str) -> bool:
    if not command.strip() or re.search(r"(?:^|\s)(?:>|>>|2>|&>|tee|set-content|add-content|out-file)(?:\s|$)", command, re.I):
        return False
    segments = re.split(r"\s*(?:\||&&|;|\r?\n)\s*", command)
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=False)
        except ValueError:
            return False
        if not tokens:
            continue
        name = Path(tokens[0].strip('"\'')).name.lower().replace("-", "")
        if name == "git":
            subcommand = next((token.lower() for token in tokens[1:] if not token.startswith("-")), "")
            if subcommand not in READ_ONLY_GIT:
                return False
            if subcommand == "worktree" and any(token.lower() in {"add", "remove", "move", "prune", "repair", "lock", "unlock"} for token in tokens):
                return False
        elif name not in {item.replace("-", "") for item in READ_ONLY_COMMANDS}:
            return False
    return True


def ownership_violation(task: Mapping[str, Any], paths: list[str]) -> str | None:
    role = str(task.get("role", "worker"))
    if role == "reviewer":
        return "reviewer is read-only"
    ownership = task.get("ownership") or {}
    owned = as_list(ownership.get("owned"))
    forbidden = as_list(ownership.get("forbidden"))
    shared = as_list(ownership.get("shared"))
    for path in paths:
        if any(paths_overlap(path, str(rule)) for rule in forbidden):
            return f"path is forbidden: {path}"
        if any(paths_overlap(path, str(rule)) for rule in shared) and role not in {"orchestrator", "shared-contract-owner"}:
            return f"shared path requires its owner: {path}"
        if owned and not any(paths_overlap(path, str(rule)) for rule in owned + shared):
            return f"path is outside task ownership: {path}"
    return None


def handle(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("_lemmingsActive") is False:
        return decision("allow", "Lemmings inactive")
    event = event_name(payload)
    task = payload.get("task") or {}
    mode = str(payload.get("mode") or task.get("mode") or "standard").lower()
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    if event == "PreToolUse":
        if tool in {"Agent", "spawn_agent"}:
            if task.get("state") != "Ready":
                return decision("block", "task must be Ready before dispatch")
            requested = payload.get("requestedModel") or (payload.get("tool_input") or {}).get("model")
            assigned = (task.get("models") or {}).get("assigned")
            if requested and requested != assigned:
                return decision("block", "spawn model differs from models.assigned")
            writer = str(task.get("role", "worker")) not in {"reviewer", "explorer", "validator"}
            isolated = mode == "strict" or payload.get("parallelWriters") or payload.get("dirtyPrimary")
            if writer and isolated:
                declared = task.get("worktree")
                if not declared:
                    return decision("block", "Strict, parallel, or dirty-primary writer requires an isolated worktree")
                cwd = Path(str(payload.get("cwd") or os.getcwd())).resolve()
                worktree = Path(str(declared))
                if not worktree.is_absolute():
                    worktree = cwd / worktree
                if cwd != worktree.resolve():
                    return decision("block", "writer cwd differs from its declared worktree")
            return decision("allow", "dispatch invariants satisfied")
        if tool in {"apply_patch", "Bash", "exec_command", "shell_command"}:
            if tool != "apply_patch" and is_read_only_shell(shell_command(payload)):
                return decision("allow", "known read-only shell command")
            paths = changed_paths(payload)
            violation = ownership_violation(task, paths)
            if violation:
                return decision("block", violation)
            if tool != "apply_patch" and not paths:
                return decision("block" if mode == "strict" else "warn", "shell write-set cannot be proven")
            return decision("allow", "write invariants satisfied")
    if event == "SubagentStart":
        context = payload.get("contextPacket") or {}
        allowed = {"taskPacket", "agentsInstructions", "frozenContracts", "interfaces", "tests", "dependencyHandoffs"}
        extras = sorted(set(context) - allowed)
        return decision("warn" if extras else "allow", "unbounded context: " + ", ".join(extras) if extras else "bounded context accepted")
    if event == "SubagentStop":
        execution = task.get("execution") or {}
        if task.get("implementationTask", True) and not candidate_head(task):
            return decision("block", "implementation task requires candidate/fix commit")
        if not (task.get("models") or {}).get("actual"):
            return decision("block", "execution requires models.actual")
        if not execution.get("handoff"):
            return decision("block", "task.execution.handoff is required")
        if not execution.get("validationEvidence") and not (task.get("validation") or {}).get("debt"):
            return decision("block", "validation evidence or owned debt is required")
        violations = as_list(payload.get("ownedPathViolations"))
        if violations:
            return decision("block", "changes outside ownership: " + ", ".join(map(str, violations)))
        return decision("allow", "candidate evidence complete")
    if event == "PostToolUse":
        paths = changed_paths(payload)
        if not paths:
            cwd = Path(str(payload.get("cwd") or os.getcwd())).resolve()
            discovered: set[str] = set()
            for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only"), ("ls-files", "--others", "--exclude-standard")):
                import subprocess
                process = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)
                if process.returncode:
                    return decision("warn", "actual diff could not be inspected")
                discovered.update(line.strip() for line in process.stdout.splitlines() if line.strip())
            paths = sorted(discovered)
        violation = ownership_violation(task, paths)
        return decision("warn", violation + "; candidate is unsuitable until corrected") if violation else decision("allow", "observed diff respects ownership")
    return decision("allow", "event is not enforced")


def hydrate(payload: dict[str, Any]) -> dict[str, Any]:
    repo = Path(str(payload.get("cwd") or os.getcwd())).resolve()
    try:
        marker = runtime_marker(repo)
    except ValueError:
        return {**payload, "_lemmingsActive": False}
    if not marker.is_file():
        return {**payload, "_lemmingsActive": False}
    state = read_object(marker)
    combined = {**state, **payload, "_lemmingsActive": True}
    for name in ("task", "phase", "review"):
        value = state.get(name + "Path")
        if value:
            path = Path(value)
            combined[name] = read_object(path if path.is_absolute() else repo / path)
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if isinstance(tool_input, dict) and tool_input.get("model"):
        combined["requestedModel"] = tool_input["model"]
    return combined


def host_output(result: Mapping[str, Any], event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    action, reason = result.get("decision"), str(result.get("reason", ""))
    if action == "allow":
        if event == "SubagentStart":
            context = payload.get("contextPacket") or {}
            lines = [f"{name}: {value}" for name, value in context.items()]
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "Lemmings context\n" + "\n".join(lines)}}
        return {}
    if event == "PreToolUse":
        if action == "block":
            return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny", "permissionDecisionReason": reason}}
        return {"systemMessage": reason}
    return {"systemMessage": reason}


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    try:
        raw = json.load(sys.stdin)
        if not isinstance(raw, dict):
            raise ValueError("hook payload must be an object")
        payload = hydrate(raw)
        output = host_output(handle(payload), event_name(raw), payload)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": f"invalid Lemmings hook input: {error}"}}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
