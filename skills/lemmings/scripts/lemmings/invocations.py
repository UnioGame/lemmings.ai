"""Persist dispatches and accept only results that match them."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .effective import capture_effective, checked_effective
from .models import normalize_route_failure
from .budget import consume_context_expansions, extend_budget, new_task_budget, reserve_tool_calls, settle_tool_calls

from .contracts import (
    DEFAULT_INVOCATION_LIMITS,
    SCHEMA_VERSION,
    as_list,
    candidate_head,
    current_recovery_route,
    git,
    path_matches,
    plan_digest,
    read_object,
    route_name,
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
    frozen = task.get("effectiveConfig")
    if frozen:
        checked_effective(frozen)
        for rule in frozen["rules"].get("ruleRefs", []):
            if reference_hash(repo, rule) != rule["contentHash"]:
                raise ValueError("selected rules changed after Task configuration was frozen")
            if rule["ref"] not in {r["ref"] for r in references}:
                references.append(dict(rule))
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
        "profileDigest": frozen["digest"] if frozen else profile_digest(profile),
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
    invocation_budget = task.get("budget") if isinstance(task.get("budget"), Mapping) else new_task_budget(profile)
    reservations = [item for item in invocation_budget.get("reservations", []) if item.get("invocationId") == invocation["invocationId"]]
    if len(reservations) > 1:
        raise ValueError("budgeted invocation has duplicate reservations")
    invocation["limits"]["maxToolCalls"] = int(
        reservations[0]["amount"] if reservations
        else invocation_budget["policy"]["toolCalls"][role]["initial"]
    )
    if frozen:
        invocation["effectiveConfigDigest"] = frozen["digest"]
        invocation["roleRoutes"] = frozen["profile"].get("roleRoutes", {}).get(role, [])
        chain = invocation["roleRoutes"]
        invocation["assignedModel"] = ((task.get("models") or {}).get("assigned") if role == task.get("role") else route_name(chain[0]) if chain else "current-host/default")
        invocation["assignedHost"] = ((task.get("models") or {}).get("hostId") if role == task.get("role") else chain[0]["hostId"] if chain else "native")
        recovered = current_recovery_route(task, role)
        if recovered:
            invocation["roleRoutes"] = task["routingRecovery"]["roleRoutes"][role]
            invocation["assignedModel"] = route_name(recovered)
            invocation["assignedHost"] = recovered["hostId"]
    invocation["contextDigest"] = invocation_digest(invocation)
    context_policy = invocation_budget["policy"]["context"]
    approved = {}
    for name, configured in context_policy.items():
        extra = sum(item.get("amount", 0) for item in invocation_budget.get("extensions", []) if item.get("kind") == name)
        approved[name] = min(configured["initial"] + extra, configured["ceiling"])
    encoded = json.dumps(invocation, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(invocation["contextRefs"]) > approved["maxWorkingSetItems"]:
        raise ValueError(f"AgentInvocation exceeds currently approved {approved['maxWorkingSetItems']} context references")
    if len(encoded) > approved["maxPacketBytes"]:
        raise ValueError(f"AgentInvocation exceeds currently approved {approved['maxPacketBytes']} bytes")
    checked = validate_invocation(invocation)
    if not checked.ok:
        raise ValueError(checked.findings[0].message)
    return invocation


def validate_dispatch(repo: Path, task: Mapping[str, Any], profile: Mapping[str, Any], invocation: Mapping[str, Any]) -> None:
    checked = validate_invocation(invocation)
    if not checked.ok:
        raise ValueError(checked.findings[0].message)
    states = {"worker":{"Ready","Active","Repair"}, "reviewer":{"Draft","Ready","Candidate"}, "explorer":{"Draft","Ready","Active","Candidate","Blocked"}}
    if task.get("state") not in states[invocation["role"]]:
        raise ValueError("Task lifecycle does not allow this role to start")
    if (task.get("routingRecovery") or {}).get("status") in {"pending-confirmation", "paused"}:
        raise ValueError("routing recovery requires a new approved selection before dispatch")
    frozen = task.get("effectiveConfig")
    if frozen:
        checked_effective(frozen)
    expected_profile = frozen["digest"] if frozen else profile_digest(profile)
    if invocation.get("taskRevision") != task.get("revision") or invocation.get("baseSha") != task.get("baseSha"):
        raise ValueError("saved invocation revision/base is stale")
    if invocation.get("taskDigest") != plan_digest(task) or invocation.get("contextDigest") != invocation_digest(invocation) or invocation.get("profileDigest") != expected_profile:
        raise ValueError("saved invocation plan/context/profile is stale")
    if task.get("budget"):
        policy = task["budget"]["policy"]["context"]
        approved = {}
        for name, configured in policy.items():
            extra = sum(item.get("amount", 0) for item in task["budget"].get("extensions", []) if item.get("kind") == name)
            approved[name] = min(configured["initial"] + extra, configured["ceiling"])
        encoded = json.dumps(invocation, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(invocation.get("contextRefs", [])) > approved["maxWorkingSetItems"] or len(encoded) > approved["maxPacketBytes"]:
            raise ValueError("saved invocation exceeds the currently approved Task context budget")
    for ref in invocation.get("contextRefs", []):
        if reference_hash(repo, ref) != ref.get("contentHash"):
            raise ValueError("saved invocation context file changed; create a fresh invocation")


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
    *, preset: str | None = None, freeze: bool = False,
) -> dict[str, Any]:
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        if freeze or task.get("effectiveConfig"):
            selected = capture_effective(repo, task, profile, preset=preset)
            models = task.get("models") or {}
            chain = selected["profile"].get("roleRoutes", {}).get(role, [])
            if models.get("assigned") == "current-host/default" and not models.get("requested") and chain and role == task.get("role"):
                # Execute the first already-ordered user selection; never rank alternatives.
                models.update(assigned=route_name(chain[0]), hostId=chain[0]["hostId"])
                task["models"] = models
        task["revision"] = expected_revision + 1
        if not task.get("budget"):
            task["budget"] = new_task_budget(profile)
        seed = f"{task.get('taskId')}:{task.get('revision')}:{role}:{attempt}:{task.get('baseSha')}"
        invocation_id = hashlib.sha256(seed.encode()).hexdigest()[:24]
        grant = reserve_tool_calls(task["budget"], role, invocation_id)
        if grant < 1:
            task["budget"]["stop"] = {"reason": "tool-call budget exhausted", "role": role, "invocationId": invocation_id}
            write_object(task_path, task)
            raise ValueError("tool-call budget exhausted; Task stop reason was recorded")
        invocation = build_invocation(repo, task, profile, role, attempt=attempt, objective=objective, invocation_id=invocation_id)
        task.setdefault("execution", {}).setdefault("invocations", []).append(invocation)
        write_object(task_path, task)
        return invocation


def result_findings(repo: Path, task: Mapping[str, Any], profile: Mapping[str, Any], result_value: Mapping[str, Any]):
    invocation = find_invocation(task, str(result_value.get("invocationId") or ""))
    if invocation is None:
        raise ValueError("AgentResult has no unique stored invocation")
    if task.get("effectiveConfig"):
        checked_effective(task["effectiveConfig"])
        for rule in task["effectiveConfig"]["rules"].get("ruleRefs", []):
            if reference_hash(repo, rule) != rule["contentHash"]:
                raise ValueError("selected rules changed after dispatch")
    checked = validate_agent_result(
        result_value,
        invocation,
        task,
        current_profile_digest=(task.get("effectiveConfig") or {}).get("digest") or profile_digest(profile),
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
                current = task_refs.get(key) or next((r for r in (task.get("effectiveConfig") or {}).get("rules", {}).get("ruleRefs", []) if (r.get("ref"),r.get("purpose")) == key), None)
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
        if task.get("budget"):
            settle_tool_calls(task["budget"], str(result_value.get("invocationId")), result_value.get("usage"))
        task.setdefault("execution", {}).setdefault("agentResults", []).append(dict(result_value))
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        return {"ok": True, "taskId": task.get("taskId"), "revision": task["revision"], "invocationId": result_value.get("invocationId")}


def record_route_failure(
    task_path: Path, *, failure_value: Mapping[str, Any], expected_revision: int,
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a normalized RouteFailure and settle its outstanding grant atomically."""
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        normalized = normalize_route_failure(failure_value)
        invocation_id = normalized["invocationId"]
        if find_invocation(task, invocation_id) is None:
            raise ValueError("RouteFailure has no unique stored invocation")
        failures = task.setdefault("execution", {}).setdefault("routeFailures", [])
        if any(isinstance(item, Mapping) and item.get("invocationId") == invocation_id for item in failures):
            raise ValueError("RouteFailure invocationId was already settled")
        consumed = settle_tool_calls(task["budget"], invocation_id, usage)
        failures.append({
            **normalized,
            "usage": {
                "trusted": bool(isinstance(usage, Mapping) and usage.get("trusted") is True),
                "toolCalls": consumed,
            },
        })
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        return {
            "ok": True,
            "taskId": task.get("taskId"),
            "revision": task["revision"],
            "invocationId": invocation_id,
            "consumedToolCalls": consumed,
        }


def record_context_usage(task_path: Path, *, expected_revision: int, amount: int = 1) -> dict[str, Any]:
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        if not task.get("budget"):
            raise ValueError("Task budget must be frozen before recording context usage")
        try:
            total = consume_context_expansions(task["budget"], amount)
        except ValueError:
            task["revision"] = expected_revision + 1
            task["budget"]["stop"] = {"reason": "context expansion budget exhausted"}
            write_object(task_path, task)
            raise
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        return {"ok": True, "taskId": task.get("taskId"), "revision": task["revision"], "contextExpansions": total}


def extend_task_budget(
    task_path: Path, *, expected_revision: int, kind: str, amount: int,
    unresolved_question: str, progress: str, role: str | None = None,
) -> dict[str, Any]:
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        if not task.get("budget"):
            raise ValueError("Task budget must be frozen by the first invocation before extension")
        entry = extend_budget(task["budget"], kind=kind, amount=amount, unresolved_question=unresolved_question, progress=progress, role=role)
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        return {"ok": True, "taskId": task.get("taskId"), "revision": task["revision"], "extension": entry}
