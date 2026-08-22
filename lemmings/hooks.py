"""Codex hook policy for an active Lemmings runtime."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lemmings.contracts import DEFAULT_CONTEXT_POLICY, DEFAULT_INVOCATION_LIMITS, as_list, candidate_head, current_recovery_route, path_matches, read_object, route_name, runtime_marker, schema_error, task_worktree, validate_agent_result, validate_models, validate_profile, validate_task
    from lemmings.models import normalize_capacity_probe, normalize_route_failure, route_failure_action
    from lemmings.telemetry import contains_sensitive_text, looks_absolute_path
    from lemmings.workspace import load_registry
else:
    from .contracts import DEFAULT_CONTEXT_POLICY, DEFAULT_INVOCATION_LIMITS, as_list, candidate_head, current_recovery_route, path_matches, read_object, route_name, runtime_marker, schema_error, task_worktree, validate_agent_result, validate_models, validate_profile, validate_task
    from .models import normalize_capacity_probe, normalize_route_failure, route_failure_action
    from .telemetry import contains_sensitive_text, looks_absolute_path
    from .workspace import load_registry

READ_ONLY_COMMANDS = {
    "rg", "grep", "ls", "dir", "pwd", "type", "cat", "head", "tail",
    "get-content", "get-childitem", "get-location", "select-string", "select-object",
    "where-object", "sort-object", "group-object", "measure-object", "convertfrom-json",
    "format-table", "format-list", "format-wide", "format-custom",
}
READ_ONLY_GIT = {
    "status", "diff", "log", "show", "rev-parse", "merge-base", "ls-files", "describe",
}
GIT_LIST_FLAGS = {
    "--list", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at",
    "--format", "--sort", "--column", "--ignore-case", "--all", "--remotes", "-a", "-r", "-v", "-vv",
}
RG_EXECUTION_OPTIONS = ("--pre", "--pre-glob", "--hostname-bin")
SHELL_WRITE_PATTERN = re.compile(
    r"(?:^|\s)(?:>|>>|2>|&>|tee|set-content|add-content|out-file|remove-item|move-item|copy-item|new-item|rename-item)(?:\s|$)",
    re.I,
)
SHELL_EVALUATION_PATTERN = re.compile(
    r"\$\(|`|\[\s*scriptblock\s*\]|\binvoke-expression\b|(?:^|\s)iex(?:\s|$)|(?:^|\s)&\s*(?:\$|\(|\{)",
    re.I,
)


def decision(action: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"decision": action, "reason": reason, **extra}


def event_name(payload: Mapping[str, Any]) -> str:
    return str(payload.get("hook_event_name") or payload.get("event") or "")


def requested_role(payload: Mapping[str, Any]) -> str | None:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    candidates = [payload.get("requestedRole"), payload.get("agent_name"), payload.get("profile_name"), payload.get("task_name"), payload.get("agent_type")]
    if isinstance(tool_input, dict):
        candidates.extend(tool_input.get(key) for key in ("role", "agent_type", "subagent_type", "task_name", "profile"))
        message = str(tool_input.get("message") or "").lower()
        match = re.search(r"(?:role\s*[:=]\s*|as\s+|lemmings[-_])(reviewer|explorer|manager|worker)\b", message)
        if match:
            candidates.append(match.group(1))
    for explicit in candidates:
        if not explicit:
            continue
        value = str(explicit).lower().replace(" ", "-")
        for role in ("reviewer", "explorer", "manager", "worker"):
            token = role.replace("-", "[-_]")
            if re.search(rf"(?:^|[-_]){token}(?:$|[-_])", value):
                return role
        if re.search(r"(?:^|[-_])review(?:$|[-_])", value):
            return "reviewer"
    return None


def requested_model(payload: Mapping[str, Any]) -> str | None:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    model = payload.get("requestedModel")
    effort = payload.get("reasoning_effort")
    if isinstance(tool_input, dict):
        model = model or tool_input.get("model")
        effort = effort or tool_input.get("reasoning_effort")
    if not model:
        return None
    value = str(model)
    if ":" in value:
        embedded_effort = value.rpartition(":")[2]
        if effort and embedded_effort != str(effort):
            return f"{value}#conflicting-effort:{effort}"
        return value
    return value if not effort else f"{value}:{effort}"


def requested_host(payload: Mapping[str, Any]) -> str | None:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    value = payload.get("requestedHostId") or payload.get("hostId")
    if isinstance(tool_input, Mapping):
        value = value or tool_input.get("hostId") or tool_input.get("host_id")
    return str(value) if value else None


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


def candidate_diff_paths(task: Mapping[str, Any], cwd: str | os.PathLike[str]) -> list[str]:
    base = str(task.get("baseSha") or "").strip()
    head = str(candidate_head(task) or "").strip()
    if not base or not head:
        raise ValueError("candidate diff requires task.base and candidateHead/fixHead")
    process = subprocess.run(
        ["git", "-C", str(cwd), "diff", "--name-only", f"{base}..{head}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        detail = process.stderr.strip() or process.stdout.strip() or "git diff failed"
        raise ValueError(f"candidate diff could not be inspected: {detail}")
    return sorted({line.strip().replace("\\", "/") for line in process.stdout.splitlines() if line.strip()})


def _git_read_only(tokens: list[str]) -> bool:
    positional = [token.lower() for token in tokens[1:] if not token.startswith("-")]
    subcommand = positional[0] if positional else ""
    arguments = [token.lower() for token in tokens[2:]]
    if subcommand in READ_ONLY_GIT:
        return not any(token.startswith("--output") for token in arguments)
    if subcommand == "worktree":
        return not arguments or arguments[0] == "list"
    if subcommand == "remote":
        return not arguments or arguments[0] in {"-v", "--verbose", "show", "get-url"}
    if subcommand in {"branch", "tag"}:
        if not arguments:
            return True
        mutation_flags = {"-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy", "--edit-description", "--set-upstream-to", "--unset-upstream", "-f", "--force"}
        if any(token in mutation_flags for token in tokens[2:]):
            return False
        return any(token.split("=", 1)[0] in GIT_LIST_FLAGS or token == "--show-current" for token in arguments)
    return False


def _powershell_read_segments(command: str) -> list[str] | None:
    if not command.strip():
        return None
    segments: list[str] = []
    current: list[str] = []
    executable: list[str] = []
    state = "normal"
    index = 0
    while index < len(command):
        character = command[index]
        following = command[index + 1] if index + 1 < len(command) else ""
        if state == "single":
            current.append(character)
            executable.append(" ")
            if character == "'":
                if following == "'":
                    current.append(following)
                    executable.append(" ")
                    index += 1
                else:
                    if following and not following.isspace() and following not in ";|&\r\n":
                        return None
                    state = "normal"
            index += 1
            continue
        if state == "double":
            current.append(character)
            executable.append(" ")
            if character in "`$":
                return None
            if character == '"':
                if following and not following.isspace() and following not in ";|&\r\n":
                    return None
                state = "normal"
            index += 1
            continue
        if character == "'":
            if current and not current[-1].isspace():
                return None
            state = "single"
            current.append(character)
            executable.append(" ")
        elif character == '"':
            if current and not current[-1].isspace():
                return None
            state = "double"
            current.append(character)
            executable.append(" ")
        elif character in "(){}<>`$":
            return None
        elif character == "&" and following != "&":
            return None
        elif character in ";\r\n" or character == "|" or (character == "&" and following == "&"):
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            executable.append(" ")
            if (character == "|" and following == "|") or (character == "&" and following == "&") or (character == "\r" and following == "\n"):
                index += 1
        else:
            current.append(character)
            executable.append(character)
        index += 1
    if state != "normal":
        return None
    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    executable_text = "".join(executable)
    if SHELL_WRITE_PATTERN.search(executable_text) or SHELL_EVALUATION_PATTERN.search(executable_text):
        return None
    return segments or None


def _posix_read_segments(command: str) -> list[str] | None:
    if not command.strip():
        return None
    segments: list[str] = []
    current: list[str] = []
    state = "normal"
    index = 0
    while index < len(command):
        character = command[index]
        following = command[index + 1] if index + 1 < len(command) else ""
        if state == "single":
            current.append(character)
            if character == "'":
                state = "normal"
            index += 1
            continue
        if state == "double":
            current.append(character)
            if character == '"':
                state = "normal"
            elif character in "`$":
                return None
            elif character == "\\":
                if not following:
                    return None
                current.append(following)
                index += 1
            index += 1
            continue
        if character == "'":
            state = "single"
            current.append(character)
        elif character == '"':
            state = "double"
            current.append(character)
        elif character == "\\":
            if not following:
                return None
            current.extend((character, following))
            index += 1
        elif character in "`$<>(){}":
            return None
        elif character == "&" and following != "&":
            return None
        elif character in ";\r\n|" or (character == "&" and following == "&"):
            segment = "".join(current).strip()
            if not segment:
                return None
            segments.append(segment)
            current = []
            if (character == "|" and following == "|") or (character == "&" and following == "&") or (character == "\r" and following == "\n"):
                index += 1
        else:
            current.append(character)
        index += 1
    if state != "normal":
        return None
    segment = "".join(current).strip()
    if not segment:
        return None
    segments.append(segment)
    return segments


def _shell_dialect(dialect: str | None = None) -> str:
    return dialect or ("windows" if os.name == "nt" else "posix")


def is_read_only_shell(command: str, dialect: str | None = None) -> bool:
    selected_dialect = _shell_dialect(dialect)
    if selected_dialect == "windows":
        segments = _powershell_read_segments(command)
    elif selected_dialect == "posix":
        segments = _posix_read_segments(command)
    else:
        return False
    if not segments:
        return False
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=selected_dialect == "posix")
        except ValueError:
            return False
        if not tokens:
            continue
        name = Path(tokens[0].strip('"\'')).name.lower().replace("-", "")
        if name == "git":
            if not _git_read_only(tokens):
                return False
        else:
            if name not in {item.replace("-", "") for item in READ_ONLY_COMMANDS}:
                return False
            arguments = [token.strip('"\'').lower() for token in tokens[1:]]
            if name == "rg" and any(
                argument == option or argument.startswith(f"{option}=")
                for argument in arguments
                for option in RG_EXECUTION_OPTIONS
            ):
                return False
    return True


def repository_root(cwd: Path) -> Path:
    process = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    return Path(process.stdout.strip()).resolve() if not process.returncode else cwd.resolve()


def ownership_violation(task: Mapping[str, Any], paths: list[str], cwd: str | Path | None = None) -> str | None:
    role = str(task.get("role", "worker"))
    if role == "reviewer":
        return "reviewer is read-only"
    ownership = task.get("ownership") or {}
    owned = as_list(ownership.get("owned"))
    forbidden = as_list(ownership.get("forbidden"))
    shared = as_list(ownership.get("shared"))
    root = repository_root(Path(cwd or os.getcwd()).resolve())
    for raw_path in paths:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            try:
                path = candidate.resolve().relative_to(root).as_posix()
            except ValueError:
                return f"absolute path is outside repository: {raw_path}"
        else:
            path = candidate.as_posix()
        if any(path_matches(path, str(rule)) for rule in forbidden):
            return f"path is forbidden: {path}"
        if any(path_matches(path, str(rule)) for rule in shared) and role != "manager":
            return f"shared path requires its owner: {path}"
        if owned and not any(path_matches(path, str(rule)) for rule in owned + shared):
            return f"path is outside task ownership: {path}"
    return None


def sanitize_context(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): sanitize_context(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_context(item) for item in value]
    if isinstance(value, str) and (contains_sensitive_text(value) or looks_absolute_path(value)):
        return "[redacted]"
    return value


def derive_context_packet(
    task: Mapping[str, Any],
    phase: Mapping[str, Any] | None,
    role: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Project canonical artifacts into a bounded role-specific invocation."""
    ownership = task.get("ownership") if isinstance(task.get("ownership"), Mapping) else {}
    validation = task.get("validation") if isinstance(task.get("validation"), Mapping) else {}
    references = []
    for entry in as_list(task.get("workingSet")):
        if not isinstance(entry, Mapping):
            continue
        body = {"ref": entry.get("ref"), "purpose": entry.get("purpose")}
        content_hash = entry.get("contentHash") or hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        references.append({**body, "contentHash": content_hash})
    profile = payload.get("profile") if isinstance(payload.get("profile"), Mapping) else {}
    attempt = int(payload.get("attempt", 0) or 0)
    seed = f"{task.get('taskId')}:{task.get('revision')}:{role}:{attempt}:{task.get('baseSha')}"
    invocation = {
        "schemaVersion": 3,
        "runId": str(payload.get("runId") or task.get("runId") or task.get("taskId")),
        "taskId": task.get("taskId"),
        "taskRevision": task.get("revision"),
        "invocationId": str(payload.get("invocationId") or hashlib.sha256(seed.encode()).hexdigest()[:24]),
        "attempt": attempt,
        "role": role,
        "baseSha": task.get("baseSha") or payload.get("baseSha") or "uncommitted",
        "profileDigest": hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "contextDigest": "",
        "objective": payload.get("focus") or payload.get("question") or task.get("goal"),
        "acceptanceCriteria": as_list(task.get("acceptance")),
        "ownedPaths": as_list(ownership.get("owned")) if role == "worker" else [],
        "forbiddenPaths": as_list(ownership.get("forbidden")),
        "contextRefs": references,
        "validationCommands": as_list(validation.get("commands")),
        "candidateHead": candidate_head(task) if role == "reviewer" else None,
        "limits": dict(DEFAULT_INVOCATION_LIMITS.get(role) or {}),
        "outputSchemaVersion": 3,
    }
    digest_body = {key: value for key, value in invocation.items() if key != "contextDigest"}
    invocation["contextDigest"] = hashlib.sha256(json.dumps(digest_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return sanitize_context(invocation)


def context_warnings(packet: Mapping[str, Any], task: Mapping[str, Any], profile: Mapping[str, Any], payload: Mapping[str, Any]) -> list[str]:
    policy = profile.get("contextPolicy") if isinstance(profile.get("contextPolicy"), Mapping) else DEFAULT_CONTEXT_POLICY
    warnings: list[str] = []
    size = len(json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    working_count = len(task.get("workingSet") or [])
    expansions = int(payload.get("expansionsUsed", 0) or 0) + (1 if payload.get("contextExpansion") else 0)
    if size > int(policy.get("maxPacketBytes", 16384)):
        warnings.append(f"context packet is {size} bytes (limit {policy.get('maxPacketBytes', 16384)})")
    if working_count > int(policy.get("maxWorkingSetItems", 12)):
        warnings.append(f"working set has {working_count} items (limit {policy.get('maxWorkingSetItems', 12)})")
    if expansions > int(policy.get("maxExpansions", 1)):
        warnings.append(f"context expansions are {expansions} (limit {policy.get('maxExpansions', 1)})")
    expansion = payload.get("contextExpansion")
    if isinstance(expansion, Mapping) and (expansion.get("broad") or not expansion.get("symbolOrDecision")):
        warnings.append("context expansion must name one symbol or decision")
    return warnings


def handle(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("_lemmingsActive") is False:
        return decision("allow", "Lemmings inactive")
    event = event_name(payload)
    task = payload.get("task") or {}
    mode = str(payload.get("mode") or task.get("resolvedMode") or task.get("mode") or "standard").lower()
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    if event == "PreToolUse":
        if tool in {"Agent", "spawn_agent"}:
            role = requested_role(payload)
            if not role:
                if mode == "strict":
                    return decision("block", "Strict spawn requires an explicit worker, reviewer, or explorer role")
                role = str(task.get("role", "worker"))
            if role not in {"worker", "reviewer", "explorer"}:
                return decision("block", "v3 dispatch role must be worker, reviewer, or explorer")
            recovery_status = (task.get("routingRecovery") or {}).get("status")
            if recovery_status in {"pending-confirmation", "paused"}:
                return decision("block", f"model routing recovery is {recovery_status}; user confirmation is required")
            tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
            probe_value = payload.get("capacityProbe") or (tool_input.get("capacityProbe") if isinstance(tool_input, Mapping) else None)
            if isinstance(probe_value, Mapping):
                try:
                    probe = normalize_capacity_probe(probe_value)
                except ValueError as error:
                    return decision("block", str(error))
                if probe["status"] == "depleted":
                    return decision("block", "selected route capacity is depleted; create a recovery proposal", capacityProbe=probe)
            profile = payload.get("profile") or {}
            profile_result = validate_profile(profile)
            if not profile_result.ok:
                return decision("block", profile_result.findings[0].message)
            task_result = validate_task(task, profile)
            if not task_result.ok:
                return decision("block", task_result.findings[0].message)
            requested = requested_model(payload)
            assigned = (task.get("models") or {}).get("assigned")
            writer = role == "worker"
            if role == "reviewer":
                if task.get("state") != "Candidate":
                    return decision("block", "reviewer requires a Candidate task")
                recovery_route = current_recovery_route(task, "reviewer")
                host_id = requested_host(payload) or (recovery_route or {}).get("hostId") or (task.get("models") or {}).get("hostId")
                allowed = [route_name(item) for item in (((profile.get("modelRoutes") or {}).get(host_id) or {}).get("reviewer") or []) if isinstance(item, Mapping)]
                recovery_allowed = bool(recovery_route and recovery_route.get("hostId") == host_id and route_name(recovery_route) == requested)
                if requested not in allowed and not recovery_allowed:
                    return decision("block", "reviewer model must be an approved per-host or task-local recovery route")
                head = candidate_head(task)
                review_head = (tool_input.get("head") if isinstance(tool_input, dict) else None) or payload.get("reviewHead")
                if not (task.get("models") or {}).get("actual"):
                    return decision("block", "reviewer requires Candidate actual-model evidence")
                if not head or (review_head and str(review_head) != head):
                    return decision("block", "reviewer must inspect the current candidate/fix head")
                return decision("allow", "reviewer dispatch invariants satisfied")
            if role == "explorer":
                workspace_blocked = task.get("state") == "Blocked" and (task.get("workspace") or {}).get("approval") == "declined"
                if task.get("state") not in {"Ready", "Active", "Candidate"} and not workspace_blocked:
                    return decision("block", f"{role} requires Ready, Active, or Candidate task")
                return decision("allow", f"bounded {role} dispatch accepted")
            if task.get("state") != "Ready":
                return decision("block", "writer requires a Ready task")
            model_result = validate_models(task, profile)
            if not model_result.ok:
                return decision("block", model_result.findings[0].message)
            if requested != assigned:
                return decision("block", "writer spawn model must be explicit and equal models.assigned")
            requested_host_id = requested_host(payload)
            if requested_host_id and requested_host_id != (task.get("models") or {}).get("hostId"):
                return decision("block", "writer spawn host must equal models.hostId")
            if mode == "strict" and not as_list((task.get("ownership") or {}).get("owned")):
                return decision("block", "Strict writer requires non-empty ownership.owned")
            backend = ((task.get("workspace") or {}).get("backend"))
            isolated = backend in {"code-worktree", "package-worktree", "unity-clone"} or "parallelWriters" in as_list(task.get("modeReasons")) or payload.get("parallelWriters") or payload.get("dirtyPrimary")
            if writer and isolated:
                workspace = task.get("workspace") or {}
                estimated = workspace.get("estimatedGiB")
                if isinstance(estimated, (int, float)) and not isinstance(estimated, bool) and estimated > 10 and workspace.get("approval") != "approved":
                    return decision("block", "workspace estimates above 10 GiB require explicit user approval before writer dispatch")
                declared = task_worktree(task)
                if not declared:
                    workspace_id = workspace.get("workspaceId")
                    repo_root = Path(str(payload.get("_repoRoot") or payload.get("cwd") or os.getcwd()))
                    registry = load_registry(repo_root)
                    matches = [entry for entry in registry.get("entries", []) if entry.get("workspaceId") == workspace_id]
                    declared = str(matches[0].get("path")) if len(matches) == 1 else None
                if not declared:
                    return decision("block", "Declared isolation, parallel writing, or a dirty primary checkout requires an isolated worktree")
                cwd = Path(str(payload.get("cwd") or os.getcwd())).resolve()
                worktree = Path(str(declared))
                if not worktree.is_absolute():
                    worktree = cwd / worktree
                if cwd != worktree.resolve():
                    return decision("block", "writer cwd differs from its declared worktree")
            return decision("allow", "dispatch invariants satisfied")
        if tool in {"apply_patch", "Bash", "exec_command", "shell_command"}:
            identity_role = requested_role(payload)
            effective_role = identity_role or str(task.get("role", "worker"))
            if tool == "apply_patch" and effective_role in {"reviewer", "explorer"}:
                return decision("block", f"{effective_role} identity cannot apply patches")
            if tool != "apply_patch" and is_read_only_shell(shell_command(payload)):
                return decision("allow", "known read-only shell command")
            if effective_role in {"reviewer", "explorer"}:
                return decision("block", f"{effective_role} identity cannot run mutating shell commands")
            command = shell_command(payload).strip()
            declared = [str(item).strip() for item in as_list((task.get("validation") or {}).get("commands"))]
            if tool != "apply_patch" and command in declared:
                return decision("allow", "declared validation command")
            if mode == "strict" and effective_role not in {"reviewer", "explorer"} and not as_list((task.get("ownership") or {}).get("owned")):
                return decision("block", "Strict writer cannot write without ownership.owned")
            paths = changed_paths(payload)
            violation = ownership_violation(task, paths, payload.get("_repoRoot") or payload.get("cwd"))
            if violation:
                return decision("block", violation)
            if not paths:
                kind = "apply_patch path-set" if tool == "apply_patch" else "shell write-set"
                return decision("block" if mode == "strict" else "warn", f"{kind} cannot be proven")
            return decision("allow", "write invariants satisfied")
    if event == "SubagentStart":
        if not task:
            return decision("block", "SubagentStart requires a canonical Task")
        recovery_status = (task.get("routingRecovery") or {}).get("status")
        if recovery_status in {"pending-confirmation", "paused"}:
            return decision("block", f"model routing recovery is {recovery_status}; user confirmation is required")
        profile = payload.get("profile") or {}
        task_result = validate_task(task, profile)
        if not task_result.ok:
            return decision("block", task_result.findings[0].message)
        role = requested_role(payload) or str(task.get("role") or "worker")
        if role not in {"worker", "reviewer", "explorer"}:
            return decision("block", "v3 invocation role must be worker, reviewer, or explorer")
        if role == "explorer" and not (payload.get("focus") or payload.get("question")):
            return decision("block", "explorer ContextPacket requires one focus or question")
        context = derive_context_packet(task, payload.get("phase") or {}, role, payload)
        warnings = context_warnings(context, task, profile, payload)
        hard = bool(warnings)
        return decision(
            "block" if hard else ("warn" if warnings else "allow"),
            "; ".join(warnings) if warnings else "bounded context accepted",
            contextPacket=context,
            warningCount=len(warnings),
        )
    if event == "SubagentStop":
        role = requested_role(payload)
        if not role:
            if mode == "strict":
                return decision("warn", "Strict subagent stop has no safely inferred role; evidence was not accepted")
            role = str(task.get("role", "worker"))
        route_failure = payload.get("routeFailure") or payload.get("route_failure")
        if isinstance(route_failure, Mapping):
            try:
                normalized = normalize_route_failure(route_failure)
                action = route_failure_action(
                    normalized,
                    transient_retries=int(payload.get("transientRetries", 0) or 0),
                    context_reductions=int(payload.get("contextReductions", 0) or 0),
                )
            except (TypeError, ValueError) as error:
                return decision("block", str(error))
            return decision("allow", f"RouteFailure accepted; next action: {action}", routeFailure=normalized, recoveryAction=action)
        result_value = payload.get("agentResult") or payload.get("agent_result")
        invocation = payload.get("agentInvocation") or payload.get("agent_invocation") or derive_context_packet(task, payload.get("phase") or {}, role, payload)
        if not isinstance(result_value, Mapping):
            return decision("block", "v3 subagent stop requires structured AgentResult")
        checked = validate_agent_result(result_value, invocation, task)
        if not checked.ok:
            return decision("block", checked.findings[0].message)
        if role == "worker" and result_value.get("status") == "succeeded":
            try:
                actual_paths = candidate_diff_paths(task, payload.get("cwd") or payload.get("_repoRoot") or os.getcwd())
            except ValueError as error:
                return decision("block", str(error))
            reported_paths = sorted({str(path).replace("\\", "/") for path in as_list(result_value.get("changedPaths"))})
            if reported_paths != actual_paths:
                return decision("block", "AgentResult.changedPaths does not match the actual candidate diff")
        return decision("allow", "AgentResult accepted")
    return decision("allow", "event is not enforced")


def hydrate(payload: dict[str, Any]) -> dict[str, Any]:
    cwd = Path(str(payload.get("cwd") or os.getcwd())).resolve()
    repo = repository_root(cwd)
    try:
        marker = runtime_marker(repo)
    except ValueError:
        return {**payload, "_lemmingsActive": False}
    if not marker.is_file():
        return {**payload, "_lemmingsActive": False, "_repoRoot": str(repo)}
    state = read_object(marker)
    if state.get("schemaVersion") != 3:
        raise ValueError(schema_error("runtime marker", state))
    combined = {**state, **payload, "_lemmingsActive": True, "_repoRoot": str(repo)}
    for name in ("profile", "task", "phase", "review"):
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
    if event == "SubagentStart" and action in {"allow", "warn"}:
        context = result.get("contextPacket") or {}
        compact = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        output = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "Lemmings AgentInvocation\n" + compact}}
        if action == "warn":
            output["systemMessage"] = reason
        return output
    if action == "allow":
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
        result = handle(payload)
        output = host_output(result, event_name(raw), payload)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": f"invalid Lemmings hook input: {error}"}}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
