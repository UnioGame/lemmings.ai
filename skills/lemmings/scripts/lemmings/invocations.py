"""Persist dispatches and accept only results that match them."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .contracts import (
    DEFAULT_INVOCATION_LIMITS,
    SCHEMA_VERSION,
    as_list,
    candidate_head,
    git,
    path_matches,
    plan_digest,
    read_object,
    validate_agent_result,
    validate_invocation,
    write_object,
)


def stable_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def profile_digest(profile: Mapping[str, Any]) -> str:
    return stable_digest(profile)


def invocation_digest(invocation: Mapping[str, Any]) -> str:
    return stable_digest({key: value for key, value in invocation.items() if key != "contextDigest"})


def reference_hash(repo: Path, entry: Mapping[str, Any]) -> str:
    reference = str(entry.get("ref") or "")
    path = (repo / reference.split("#", 1)[0]).resolve()
    try:
        path.relative_to(repo.resolve())
    except ValueError as error:
        raise ValueError(f"context reference escapes repository: {reference}") from error
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    supplied = entry.get("contentHash")
    if supplied:
        return str(supplied)
    raise ValueError(f"context reference needs an existing file or pinned contentHash: {reference}")


def build_invocation(
    repo: Path,
    task: Mapping[str, Any],
    profile: Mapping[str, Any],
    role: str,
    *,
    attempt: int,
    objective: str | None = None,
    invocation_id: str | None = None,
) -> dict[str, Any]:
    references = [
        {"ref": entry.get("ref"), "purpose": entry.get("purpose"), "contentHash": reference_hash(repo, entry)}
        for entry in as_list(task.get("workingSet"))
        if isinstance(entry, Mapping)
    ]
    ownership = task.get("ownership") if isinstance(task.get("ownership"), Mapping) else {}
    validation = task.get("validation") if isinstance(task.get("validation"), Mapping) else {}
    seed = f"{task.get('taskId')}:{task.get('revision')}:{role}:{attempt}:{task.get('baseSha')}"
    invocation = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": str(task.get("runId") or task.get("taskId")),
        "taskId": task.get("taskId"),
        "taskRevision": task.get("revision"),
        "invocationId": invocation_id or hashlib.sha256(seed.encode()).hexdigest()[:24],
        "attempt": attempt,
        "role": role,
        "baseSha": task.get("baseSha") or "uncommitted",
        "profileDigest": profile_digest(profile),
        "taskDigest": plan_digest(task),
        "contextDigest": "",
        "objective": objective or task.get("goal"),
        "acceptanceCriteria": as_list(task.get("acceptance")),
        "ownedPaths": as_list(ownership.get("owned")) if role == "worker" else [],
        "forbiddenPaths": as_list(ownership.get("forbidden")),
        "contextRefs": references,
        "validationCommands": as_list(validation.get("commands")),
        "candidateHead": candidate_head(task) if role == "reviewer" else None,
        "limits": dict(DEFAULT_INVOCATION_LIMITS.get(role) or {}),
        "outputSchemaVersion": SCHEMA_VERSION,
    }
    invocation["contextDigest"] = invocation_digest(invocation)
    checked = validate_invocation(invocation)
    if not checked.ok:
        raise ValueError(checked.findings[0].message)
    return invocation


def find_invocation(task: Mapping[str, Any], invocation_id: str) -> Mapping[str, Any] | None:
    matches = [
        value for value in as_list((task.get("execution") or {}).get("invocations"))
        if isinstance(value, Mapping) and value.get("invocationId") == invocation_id
    ]
    return matches[0] if len(matches) == 1 else None


@contextmanager
def task_lock(task_path: Path) -> Iterator[None]:
    lock = task_path.with_suffix(task_path.suffix + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise ValueError(f"Task is locked: {task_path}") from error
    os.close(descriptor)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def record_invocation(
    repo: Path,
    task_path: Path,
    profile: Mapping[str, Any],
    role: str,
    attempt: int,
    expected_revision: int,
    objective: str | None = None,
) -> dict[str, Any]:
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        task["revision"] = expected_revision + 1
        invocation = build_invocation(repo, task, profile, role, attempt=attempt, objective=objective)
        task.setdefault("execution", {}).setdefault("invocations", []).append(invocation)
        write_object(task_path, task)
        return invocation


def result_findings(repo: Path, task: Mapping[str, Any], profile: Mapping[str, Any], result_value: Mapping[str, Any]):
    invocation = find_invocation(task, str(result_value.get("invocationId") or ""))
    if invocation is None:
        raise ValueError("AgentResult has no unique stored invocation")
    checked = validate_agent_result(
        result_value,
        invocation,
        task,
        current_profile_digest=profile_digest(profile),
        current_context_digest=invocation_digest(invocation),
        current_task_digest=plan_digest(task),
    )
    if checked.ok and invocation.get("role") == "worker" and result_value.get("status") == "succeeded":
        process = git(repo, "diff", "--name-only", f"{task.get('baseSha')}..{result_value.get('candidateHead')}")
        if process.returncode:
            checked.error("result.diff", "cannot inspect AgentResult candidate diff")
        else:
            actual = sorted(line.strip().replace("\\", "/") for line in process.stdout.splitlines() if line.strip())
            reported = sorted(set(str(path).replace("\\", "/") for path in as_list(result_value.get("changedPaths"))))
            if actual != reported:
                checked.error("result.paths", "AgentResult.changedPaths does not match the actual candidate diff")
            ownership = task.get("ownership") or {}
            owned, shared, forbidden = as_list(ownership.get("owned")), as_list(ownership.get("shared")), as_list(ownership.get("forbidden"))
            for path in actual:
                if any(path_matches(path, str(rule)) for rule in forbidden + shared) or not any(path_matches(path, str(rule)) for rule in owned):
                    checked.error("result.ownership", f"candidate path is outside worker ownership: {path}")
            refreshed = []
            task_refs = {(str(item.get("ref")), str(item.get("purpose"))): item for item in as_list(task.get("workingSet")) if isinstance(item, Mapping)}
            for saved in as_list(invocation.get("contextRefs")):
                key = (str(saved.get("ref")), str(saved.get("purpose")))
                current = task_refs.get(key)
                if current is None:
                    checked.error("result.context", "working set changed after dispatch")
                    break
                path = key[0].split("#", 1)[0].replace("\\", "/")
                worker_changed = path in actual and any(path_matches(path, str(rule)) for rule in owned)
                refreshed.append({**saved, "contentHash": saved.get("contentHash") if worker_changed else reference_hash(repo, current)})
            else:
                candidate = {**invocation, "contextRefs": refreshed}
                if invocation_digest(candidate) != invocation.get("contextDigest"):
                    checked.error("result.context", "working set content changed after dispatch")
    return checked


def accept_result(
    repo: Path,
    task_path: Path,
    profile: Mapping[str, Any],
    result_value: Mapping[str, Any],
    expected_revision: int,
) -> dict[str, Any]:
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        checked = result_findings(repo, task, profile, result_value)
        if not checked.ok:
            raise ValueError(checked.findings[0].message)
        task.setdefault("execution", {}).setdefault("agentResults", []).append(dict(result_value))
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        return {"ok": True, "taskId": task.get("taskId"), "revision": task["revision"], "invocationId": result_value.get("invocationId")}
