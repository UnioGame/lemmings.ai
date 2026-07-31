"""Codex hook policy for an active Lemmings runtime."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lemmings.core import as_list, candidate_head, path_matches, read_object, runtime_marker, validate_models
else:
    from .core import as_list, candidate_head, path_matches, read_object, runtime_marker, validate_models

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
        match = re.search(r"(?:role\s*[:=]\s*|as\s+|lemmings[-_])(reviewer|explorer|validator|summarizer|orchestrator|complex-worker|worker)\b", message)
        if match:
            candidates.append(match.group(1))
    for explicit in candidates:
        if not explicit:
            continue
        value = str(explicit).lower().replace(" ", "-")
        for role in ("reviewer", "explorer", "validator", "summarizer", "orchestrator", "complex-worker", "worker"):
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
    return value if ":" in value or not effort else f"{value}:{effort}"


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
            if character == "`" or (character == "$" and following == "("):
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
        elif character in "(){}<>" or character == "`":
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
        if any(path_matches(path, str(rule)) for rule in shared) and role not in {"orchestrator", "shared-contract-owner"}:
            return f"shared path requires its owner: {path}"
        if owned and not any(path_matches(path, str(rule)) for rule in owned + shared):
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
            role = requested_role(payload)
            if not role:
                if mode == "strict":
                    return decision("block", "Strict spawn requires an explicit writer, reviewer, explorer, or validator role")
                role = str(task.get("role", "worker"))
            tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
            requested = requested_model(payload)
            assigned = (task.get("models") or {}).get("assigned")
            writer = role not in {"reviewer", "explorer", "validator", "summarizer"}
            if role == "reviewer":
                if task.get("state") != "Candidate":
                    return decision("block", "reviewer requires a Candidate task")
                if requested != "gpt-5.6-sol:high":
                    return decision("block", "reviewer must use gpt-5.6-sol:high")
                head = candidate_head(task)
                review_head = (tool_input.get("head") if isinstance(tool_input, dict) else None) or payload.get("reviewHead")
                if not (task.get("models") or {}).get("actual") or not (task.get("execution") or {}).get("handoff"):
                    return decision("block", "reviewer requires Candidate actual-model and embedded handoff evidence")
                if not head or (review_head and str(review_head) != head):
                    return decision("block", "reviewer must inspect the current candidate/fix head")
                return decision("allow", "reviewer dispatch invariants satisfied")
            if role in {"explorer", "validator", "summarizer"}:
                if task.get("state") not in {"Ready", "Active", "Candidate"}:
                    return decision("block", f"{role} requires Ready, Active, or Candidate task")
                return decision("allow", f"bounded {role} dispatch accepted")
            if task.get("state") != "Ready":
                return decision("block", "writer requires a Ready task")
            model_result = validate_models(task, payload.get("profile") or {})
            if not model_result.ok:
                return decision("block", model_result.findings[0].message)
            if requested and requested != assigned:
                return decision("block", "writer spawn model differs from models.assigned")
            if mode == "strict" and not as_list((task.get("ownership") or {}).get("owned")):
                return decision("block", "Strict writer requires non-empty ownership.owned")
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
            identity_role = requested_role(payload)
            effective_role = identity_role or str(task.get("role", "worker"))
            if tool == "apply_patch" and effective_role in {"reviewer", "explorer", "summarizer", "validator"}:
                return decision("block", f"{effective_role} identity cannot apply patches")
            if tool != "apply_patch" and effective_role == "validator":
                command = shell_command(payload).strip()
                declared = [str(item).strip() for item in as_list((task.get("validation") or {}).get("commands"))]
                return decision("allow", "declared validator command") if command in declared else decision("block", "validator shell command is not declared in task.validation.commands")
            if tool != "apply_patch" and is_read_only_shell(shell_command(payload)):
                return decision("allow", "known read-only shell command")
            if effective_role in {"reviewer", "explorer", "summarizer"}:
                return decision("block", f"{effective_role} identity cannot run mutating shell commands")
            if mode == "strict" and effective_role not in {"reviewer", "explorer", "validator", "summarizer"} and not as_list((task.get("ownership") or {}).get("owned")):
                return decision("block", "Strict writer cannot write without ownership.owned")
            paths = changed_paths(payload)
            violation = ownership_violation(task, paths, payload.get("_repoRoot") or payload.get("cwd"))
            if violation:
                return decision("block", violation)
            if tool != "apply_patch" and not paths:
                return decision("block" if mode == "strict" else "warn", "shell write-set cannot be proven")
            return decision("allow", "write invariants satisfied")
    if event == "SubagentStart":
        context = payload.get("contextPacket") or {}
        if not context and task:
            phase = payload.get("phase") or {}
            profile = payload.get("profile") or {}
            context = {
                "taskPacket": {
                    key: task.get(key)
                    for key in ("taskId", "state", "role", "plan", "ownership", "models")
                    if task.get(key) is not None
                },
                "agentsInstructions": {"mode": payload.get("mode") or task.get("mode") or profile.get("mode", "auto")},
            }
            if phase.get("contractsFrozen") and phase.get("contracts"):
                context["frozenContracts"] = phase.get("contracts")
            execution = task.get("execution") or {}
            for key in ("interfaces", "tests", "dependencyHandoffs"):
                if execution.get(key):
                    context[key] = execution[key]
        allowed = {"taskPacket", "agentsInstructions", "frozenContracts", "interfaces", "tests", "dependencyHandoffs"}
        extras = sorted(set(context) - allowed)
        if extras:
            return decision("warn", "unbounded context: " + ", ".join(extras))
        expansion = payload.get("contextExpansion") or {}
        if expansion and (int(payload.get("expansionsUsed", 0)) >= 1 or expansion.get("broad") or not expansion.get("symbolOrDecision")):
            return decision("warn", "focused context expansion budget is exhausted or unbounded")
        return decision("allow", "bounded context accepted", contextPacket=context)
    if event == "SubagentStop":
        role = requested_role(payload)
        if not role:
            if mode == "strict":
                return decision("warn", "Strict subagent stop has no safely inferred role; evidence was not accepted")
            role = str(task.get("role", "worker"))
        if role == "reviewer":
            head = candidate_head(task)
            evidence = payload.get("reviewEvidence") or payload.get("verdict")
            if not evidence or (payload.get("reviewHead") and str(payload.get("reviewHead")) != str(head)):
                return decision("block", "reviewer stop requires verdict/range evidence for current candidate head")
            return decision("allow", "review evidence complete")
        if role == "validator":
            return decision("allow", "validation evidence complete") if payload.get("validationEvidence") else decision("block", "validator stop requires validation evidence")
        if role in {"explorer", "summarizer"}:
            return decision("allow", "bounded read-only output complete") if payload.get("boundedOutput") or payload.get("output") else decision("block", f"{role} stop requires bounded output")
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
            cwd = Path(str(payload.get("_repoRoot") or payload.get("cwd") or os.getcwd())).resolve()
            discovered: set[str] = set()
            for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only"), ("ls-files", "--others", "--exclude-standard")):
                process = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)
                if process.returncode:
                    return decision("warn", "actual diff could not be inspected")
                discovered.update(line.strip() for line in process.stdout.splitlines() if line.strip())
            paths = sorted(discovered)
        identity_role = requested_role(payload) or str(task.get("role", "worker"))
        if identity_role == "validator" and paths:
            allowed_outputs = as_list((task.get("validation") or {}).get("allowedOutputs"))
            unexpected = [path for path in paths if not any(path_matches(path, str(rule)) for rule in allowed_outputs)]
            if unexpected:
                return decision("warn", "validator changed undeclared outputs: " + ", ".join(unexpected))
        violation = ownership_violation(task, paths, payload.get("_repoRoot") or payload.get("cwd"))
        return decision("warn", violation + "; candidate is unsuitable until corrected") if violation else decision("allow", "observed diff respects ownership")
    return decision("allow", "event is not enforced")


def hydrate(payload: dict[str, Any]) -> dict[str, Any]:
    cwd = Path(str(payload.get("cwd") or os.getcwd())).resolve()
    repo = repository_root(cwd)
    try:
        marker = runtime_marker(repo)
    except ValueError:
        return {**payload, "_lemmingsActive": False}
    if not marker.is_file():
        return {**payload, "_lemmingsActive": False}
    state = read_object(marker)
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
    if action == "allow":
        if event == "SubagentStart":
            context = result.get("contextPacket") or payload.get("contextPacket") or {}
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
