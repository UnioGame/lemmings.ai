"""Persist dispatches and accept only results that match them."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .effective import capture_effective, checked_effective
from .models import normalize_route_failure
from .budget import consume_context_expansions, extend_budget, new_task_budget, reserve_tool_calls, settle_tool_calls

from .contracts import (
    BLOCKING_PRIORITIES,
    DEFAULT_INVOCATION_LIMITS,
    SCHEMA_VERSION,
    as_list,
    canonical_evidence_path,
    candidate_head,
    current_recovery_route,
    git,
    path_matches,
    plan_digest,
    read_object,
    review_digest,
    route_name,
    assess_repair_progress,
    unresolved_finding_ids,
    validate_review,
    validate_agent_result,
    validate_budget_ledger,
    validate_invocation,
    write_object,
)
from .readiness import readiness_digest, validate_candidate_readiness, validation_digest


def stable_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def profile_digest(profile: Mapping[str, Any]) -> str:
    return stable_digest(profile)


def invocation_digest(invocation: Mapping[str, Any]) -> str:
    return stable_digest({key: value for key, value in invocation.items() if key != "contextDigest"})


def _review_lane(
    task: Mapping[str, Any],
    profile: Mapping[str, Any] | None = None,
    *,
    explicit: str | None = None,
    invocation: Mapping[str, Any] | None = None,
) -> str:
    """Return the immutable reviewer host/model lane identity.

    Candidate reviewer identity is independent from the task owner's
    ``models`` assignment.  A recovery route wins, then the frozen profile,
    then the host default.  ``explicit`` is an assertion once an authority is
    available; it never silently changes the saved route.
    """
    if invocation and invocation.get("assignedHost") and invocation.get("assignedModel"):
        lane = f"{invocation['assignedHost']}::{invocation['assignedModel']}"
        if explicit and str(explicit) != lane:
            raise ValueError("review-lane must match the saved reviewer identity")
        return lane
    if invocation and invocation.get("reviewLane"):
        lane = str(invocation["reviewLane"])
        if explicit and str(explicit) != lane:
            raise ValueError("review-lane must match the saved reviewer identity")
        return lane
    route = _review_route(task, profile)
    lane = f"{route.get('hostId', 'native')}::{route_name(route) or 'current-host/default'}"
    if explicit and str(explicit) != lane:
        allowed: list[Mapping[str, Any]] = []
        # Cross review has two co-equal frozen lanes.
        assignments = task.get("roleAssignments") if isinstance(task.get("roleAssignments"), Mapping) else {}
        for candidate in (assignments.get("reviewer"), task.get("reviewerRecovery"), current_recovery_route(task, "reviewer")):
            if isinstance(candidate, Mapping):
                allowed.append(candidate)
        if not allowed:
            effective = task.get("effectiveConfig") if isinstance(task.get("effectiveConfig"), Mapping) else {}
            allowed = list(((effective.get("profile") or {}).get("roleRoutes") or {}).get("reviewer") or [])
            if not allowed and isinstance(profile, Mapping):
                allowed = list((profile.get("roleRoutes") or {}).get("reviewer") or [])
                for host, roles in (profile.get("modelRoutes") or {}).items():
                    allowed.extend([{**candidate, "hostId": host} for candidate in (roles.get("reviewer") or []) if isinstance(candidate, Mapping)])
        identities = {f"{candidate.get('hostId', 'native')}::{route_name(candidate)}" for candidate in allowed if route_name(candidate)}
        if str(explicit) in identities:
            return str(explicit)
        raise ValueError("review-lane must match the configured reviewer identity")
    return lane


def _review_route(task: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve candidate reviewer authority without consulting task owner models."""
    recovery = current_recovery_route(task, "reviewer")
    if recovery:
        return dict(recovery)
    for value in (task.get("reviewerRecovery"),):
        if isinstance(value, Mapping) and value.get("hostId") and route_name(value):
            return dict(value)
    assignments = task.get("roleAssignments")
    if isinstance(assignments, Mapping):
        value = assignments.get("reviewer")
        if isinstance(value, Mapping) and value.get("hostId") and route_name(value):
            return dict(value)
    effective = task.get("effectiveConfig") if isinstance(task.get("effectiveConfig"), Mapping) else None
    route = None
    if effective:
        route = next(iter((effective.get("profile") or {}).get("roleRoutes", {}).get("reviewer", []) or []), None)
    if route is None and isinstance(profile, Mapping):
        route = next(iter((profile.get("roleRoutes") or {}).get("reviewer", []) or []), None)
    if isinstance(route, Mapping):
        return dict(route)
    return {"hostId": "native", "providerId": "native", "modelId": "current-host/default"}


def _candidate_review_spec(task: Mapping[str, Any], lane: str, invocation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    readiness = ((task.get("execution") or {}).get("candidateReadiness") if isinstance(task.get("execution"), Mapping) else None)
    if "::" in lane:
        host, model = lane.split("::", 1)
    else:
        host, model = None, lane
    spec: dict[str, Any] = {
        "mode": "full",
        "fullBaseSha": task.get("baseSha"),
        "candidateHead": candidate_head(task),
        "readinessDigest": readiness.get("digest") if isinstance(readiness, Mapping) else None,
        "planDigest": plan_digest(task),
        "validationDigest": validation_digest(task),
        "previousReviewRef": None,
        "previousReviewDigest": None,
        "previousHead": None,
        "findingIds": [],
        "reviewLane": lane,
        "reviewerHost": host,
        "reviewerModel": model,
    }
    current = None
    execution = task.get("execution") if isinstance(task.get("execution"), Mapping) else {}
    chains = execution.get("reviewChains") if isinstance(execution, Mapping) else None
    if isinstance(chains, Mapping) and isinstance(chains.get(lane), Mapping):
        current = chains[lane].get("reviewSpec") if isinstance(chains[lane].get("reviewSpec"), Mapping) else chains[lane]
    if current is None:
        current = task.get("reviewSpec")
    if isinstance(current, Mapping) and (not current.get("reviewLane") or current.get("reviewLane") == lane):
        spec.update(dict(current))
        spec.update({
            "fullBaseSha": task.get("baseSha"),
            "candidateHead": candidate_head(task),
            "readinessDigest": readiness.get("digest") if isinstance(readiness, Mapping) else None,
            "planDigest": plan_digest(task),
            "validationDigest": validation_digest(task),
            "reviewLane": lane,
            "reviewerHost": host,
            "reviewerModel": model,
        })
    return spec


def _review_basis(spec: Mapping[str, Any], head: str | None) -> str:
    return stable_digest({
        "candidateHead": head,
        "mode": spec.get("mode", "full"),
        "fullBaseSha": spec.get("fullBaseSha"),
        "readinessDigest": spec.get("readinessDigest"),
        "planDigest": spec.get("planDigest"),
        "validationDigest": spec.get("validationDigest"),
        "previousReviewRef": spec.get("previousReviewRef"),
        "previousReviewDigest": spec.get("previousReviewDigest"),
        "previousHead": spec.get("previousHead"),
        "findingIds": spec.get("findingIds") or [],
    })


def invocation_binding_digest(invocation: Mapping[str, Any]) -> str:
    """Digest a stored invocation without its self-referential review binding."""
    material = dict(invocation)
    spec = material.get("reviewSpec")
    if isinstance(spec, Mapping):
        bound = dict(spec)
        for key in ("invocationId", "invocationBasis", "invocationDigest"):
            bound.pop(key, None)
        material["reviewSpec"] = bound
    return invocation_digest(material)


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
    subject_kind: str | None = None,
    dispatch_kind: str | None = None,
    retry_of: str | None = None,
    repair_cycle: int | None = None,
    review_lane: str | None = None,
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
    kind = dispatch_kind or ("review" if role == "reviewer" else "repair" if task.get("state") == "Repair" else "initial")
    review_kind = subject_kind or ("candidate" if task.get("state") == "Candidate" else "task-plan" if role == "reviewer" else None)
    invocation = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": str(task.get("runId") or task.get("taskId")),
        "ownerKind": "task",
        "ownerId": task.get("taskId"),
        "ownerRevision": task.get("revision"),
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
        "usageAccounting": "invocation-v1",
        "dispatchKind": kind,
        "retryOf": retry_of,
        "repairCycle": repair_cycle,
        "reviewSubjectKind": review_kind,
        "reviewLane": review_lane if role == "reviewer" and review_kind == "candidate" else None,
        "limits": dict(DEFAULT_INVOCATION_LIMITS.get(role) or {}),
        "outputSchemaVersion": SCHEMA_VERSION,
    }
    if role == "reviewer" and review_kind == "candidate":
        readiness = ((task.get("execution") or {}).get("candidateReadiness") if isinstance(task.get("execution"), Mapping) else None)
        invocation["reviewSpec"] = {
            "mode": "full",
            "fullBaseSha": task.get("baseSha"),
            "candidateHead": candidate_head(task),
            "readinessDigest": readiness.get("digest") if isinstance(readiness, Mapping) else None,
            "planDigest": plan_digest(task),
            "validationDigest": validation_digest(task),
            "previousReviewRef": None,
            "previousReviewDigest": None,
            "previousHead": None,
            "findingIds": [],
        }
        current_spec = task.get("reviewSpec")
        if isinstance(current_spec, Mapping):
            invocation["reviewSpec"] = dict(current_spec)
    task_budget_frozen = isinstance(task.get("budget"), Mapping)
    invocation_budget = task.get("budget") if task_budget_frozen else new_task_budget(profile)
    invocation["usageAccounting"] = str((invocation_budget.get("policy") or {}).get("accountingMode") or "invocation-v1")
    if task_budget_frozen:
        invocation["budgetPolicyDigest"] = stable_digest(invocation_budget["policy"])
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
        if role == task.get("role"):
            invocation["assignedModel"] = (task.get("models") or {}).get("assigned")
            invocation["assignedHost"] = (task.get("models") or {}).get("hostId")
        elif role == "reviewer":
            if review_kind == "candidate" and review_lane and "::" in review_lane:
                assigned_host, assigned_model = review_lane.split("::", 1)
                invocation["assignedModel"] = assigned_model
                invocation["assignedHost"] = assigned_host
            else:
                reviewer_route = _review_route(task, profile)
                invocation["assignedModel"] = route_name(reviewer_route) or "current-host/default"
                invocation["assignedHost"] = reviewer_route.get("hostId", "native")
        else:
            invocation["assignedModel"] = route_name(chain[0]) if chain else "current-host/default"
            invocation["assignedHost"] = chain[0]["hostId"] if chain else "native"
        recovered = current_recovery_route(task, role)
        if recovered:
            invocation["roleRoutes"] = task["routingRecovery"]["roleRoutes"][role]
            invocation["assignedModel"] = route_name(recovered)
            invocation["assignedHost"] = recovered["hostId"]
    if role == "reviewer" and review_kind == "candidate":
        lane = _review_lane(task, profile, explicit=review_lane, invocation=invocation)
        invocation["reviewLane"] = lane
        invocation["reviewSpec"] = _candidate_review_spec(task, lane, invocation)
        invocation["reviewSpec"]["invocationId"] = invocation["invocationId"]
        invocation["reviewSpec"]["invocationBasis"] = _review_basis(invocation["reviewSpec"], invocation.get("candidateHead"))
        invocation["reviewSpec"]["invocationDigest"] = invocation_binding_digest(invocation)
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


def validate_dispatch(repo: Path, task: Mapping[str, Any], profile: Mapping[str, Any], invocation: Mapping[str, Any], *, task_path: Path | None = None) -> None:
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
    if invocation.get("role") == "reviewer" and invocation.get("reviewSubjectKind", "candidate") == "candidate":
        readiness = validate_candidate_readiness(repo, task, task_path=task_path)
        if not readiness.ok:
            raise ValueError(readiness.findings[0].message)
    if invocation.get("role") == "worker" and task.get("state") == "Repair":
        active = ((task.get("execution") or {}).get("activeRepair") if isinstance(task.get("execution"), Mapping) else None)
        if invocation.get("dispatchKind") != "repair" or not isinstance(active, Mapping) or invocation.get("repairCycle") != active.get("cycle"):
            raise ValueError("Repair worker dispatch requires an authorized open repair cycle")


def find_invocation(task: Mapping[str, Any], invocation_id: str) -> Mapping[str, Any] | None:
    matches = [
        value for value in as_list((task.get("execution") or {}).get("invocations"))
        if isinstance(value, Mapping) and value.get("invocationId") == invocation_id
    ]
    return matches[0] if len(matches) == 1 else None


def assert_budget_ledger(task: Mapping[str, Any]) -> None:
    checked = validate_budget_ledger(task)
    if not checked.ok:
        raise ValueError(checked.findings[0].message)


_TASK_LOCKS = threading.local()


@contextmanager
def task_lock(task_path: Path) -> Iterator[None]:
    key = str(task_path.resolve())
    held = getattr(_TASK_LOCKS, "held", set())
    if key in held:
        yield
        return
    lock = task_path.with_suffix(task_path.suffix + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise ValueError(f"Task is locked: {task_path}") from error
    os.close(descriptor)
    _TASK_LOCKS.held = held | {key}
    try:
        yield
    finally:
        _TASK_LOCKS.held = held
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
    subject_kind: str | None = None, dispatch_kind: str | None = None,
    retry_of: str | None = None, repair_cycle: int | None = None,
    review_lane: str | None = None, accounting_mode: str | None = None,
) -> dict[str, Any]:
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        assert_budget_ledger(task)
        prior_invocations = as_list((task.get("execution") or {}).get("invocations"))
        if not isinstance(task.get("budget"), Mapping) and any(
            isinstance(item, Mapping) and item.get("budgetPolicyDigest") for item in prior_invocations
        ):
            raise ValueError("frozen Task budget is missing after the first budgeted invocation")
        if freeze or task.get("effectiveConfig"):
            selected = capture_effective(repo, task, profile, preset=preset)
            models = task.get("models") or {}
            chain = selected["profile"].get("roleRoutes", {}).get(role, [])
            if models.get("assigned") == "current-host/default" and not models.get("requested") and chain and role == task.get("role"):
                # Execute the first already-ordered user selection; never rank alternatives.
                models.update(assigned=route_name(chain[0]), hostId=chain[0]["hostId"])
                task["models"] = models
        resolved_subject = subject_kind or ("candidate" if role == "reviewer" and task.get("state") == "Candidate" else "task-plan" if role == "reviewer" else None)
        resolved_kind = dispatch_kind or ("review" if role == "reviewer" else "repair" if task.get("state") == "Repair" else "initial")
        resolved_lane = _review_lane(task, profile, explicit=review_lane) if role == "reviewer" and resolved_subject == "candidate" else None
        if role == "reviewer" and resolved_subject == "candidate":
            readiness = validate_candidate_readiness(repo, task, task_path=task_path)
            if not readiness.ok:
                raise ValueError(readiness.findings[0].message)
            current_head = candidate_head(task)
            current_spec = _candidate_review_spec(task, resolved_lane or _review_lane(task, profile))
            current_basis = _review_basis(current_spec, current_head)
            for prior in as_list((task.get("execution") or {}).get("invocations")):
                if not isinstance(prior, Mapping) or prior.get("role") != "reviewer":
                    continue
                if prior.get("reviewSubjectKind", "candidate") != "candidate":
                    continue
                prior_spec = prior.get("reviewSpec") if isinstance(prior.get("reviewSpec"), Mapping) else {}
                prior_lane = prior.get("reviewLane") or prior_spec.get("reviewLane")
                if not prior_lane:
                    prior_lane = f"{prior.get('assignedHost') or 'native'}::{prior.get('assignedModel') or 'current-host/default'}"
                same_subject = prior.get("candidateHead") == current_head
                same_lane = prior_lane == (resolved_lane or _review_lane(task, profile))
                same_basis = (_review_basis(prior_spec, current_head) == current_basis
                              if prior_spec else prior.get("reviewSpec", {}).get("mode", "full") == current_spec.get("mode", "full"))
                if same_subject and same_lane and same_basis:
                    raise ValueError("reviewer subject is already dispatched; reuse its saved invocation or result, not a new candidate or review basis")
        if role == "worker" and task.get("state") == "Repair":
            active = ((task.get("execution") or {}).get("activeRepair") if isinstance(task.get("execution"), Mapping) else None)
            if resolved_kind != "repair" or not isinstance(active, Mapping):
                raise ValueError("Repair worker dispatch requires an authorized open repair cycle")
            resolved_cycle = repair_cycle or active.get("cycle")
            if resolved_cycle != active.get("cycle"):
                raise ValueError("repair cycle does not match the active repair")
            for prior in as_list((task.get("execution") or {}).get("invocations")):
                if not isinstance(prior, Mapping) or prior.get("dispatchKind") != "repair" or prior.get("repairCycle") != resolved_cycle:
                    continue
                settled = any(isinstance(item, Mapping) and item.get("invocationId") == prior.get("invocationId") for item in as_list((task.get("execution") or {}).get("agentResults")))
                settled = settled or any(isinstance(item, Mapping) and item.get("invocationId") == prior.get("invocationId") for item in as_list((task.get("execution") or {}).get("routeFailures")))
                if not settled:
                    raise ValueError("one worker dispatch is already open for this repair cycle")
        chosen_mode = accounting_mode or (((task.get("budget") or {}).get("policy") or {}).get("accountingMode")) or profile.get("accountingMode") or profile.get("budgetAccountingMode") or "invocation-v1"
        if chosen_mode == "host-v1":
            if role == "worker":
                dispatch_host = str((task.get("models") or {}).get("hostId") or "native")
            elif role == "reviewer" and resolved_lane and "::" in resolved_lane:
                dispatch_host = resolved_lane.split("::", 1)[0]
            else:
                assigned = ((task.get("roleAssignments") or {}).get(role) or {})
                dispatch_host = str(assigned.get("hostId") or "native")
            snapshot = task.get("accountingCapabilities") if isinstance(task.get("accountingCapabilities"), Mapping) else {}
            material = {"hosts": snapshot.get("hosts") if isinstance(snapshot.get("hosts"), Mapping) else {}}
            if snapshot.get("digest") != stable_digest(material):
                raise ValueError("host-v1 capability snapshot digest is missing or invalid")
            hosts = snapshot.get("hosts") if isinstance(snapshot.get("hosts"), Mapping) else {}
            if (hosts.get(dispatch_host) or {}).get("usageAccounting") is not True:
                raise ValueError(f"host-v1 requires a frozen trusted usageAccounting capability for executing host: {dispatch_host}")
        task["revision"] = expected_revision + 1
        if not task.get("budget"):
            task["budget"] = new_task_budget(profile, accounting_mode)
        elif accounting_mode and ((task.get("budget") or {}).get("policy") or {}).get("accountingMode", "invocation-v1") != accounting_mode:
            raise ValueError("accounting mode is frozen by the Task budget")
        seed = f"{task.get('taskId')}:{task.get('revision')}:{role}:{attempt}:{task.get('baseSha')}"
        invocation_id = hashlib.sha256(seed.encode()).hexdigest()[:24]
        grant = reserve_tool_calls(task["budget"], role, invocation_id)
        if grant < 1:
            task["budget"]["stop"] = {"reason": "tool-call budget exhausted", "role": role, "invocationId": invocation_id}
            write_object(task_path, task)
            raise ValueError("tool-call budget exhausted; Task stop reason was recorded")
        invocation = build_invocation(repo, task, profile, role, attempt=attempt, objective=objective, invocation_id=invocation_id,
                                      subject_kind=resolved_subject, dispatch_kind=resolved_kind,
                                      retry_of=retry_of, repair_cycle=repair_cycle or (active.get("cycle") if role == "worker" and task.get("state") == "Repair" and isinstance((task.get("execution") or {}).get("activeRepair"), Mapping) else None),
                                      review_lane=resolved_lane)
        task.setdefault("execution", {}).setdefault("invocations", []).append(invocation)
        write_object(task_path, task)
        return invocation


def result_findings(repo: Path, task: Mapping[str, Any], profile: Mapping[str, Any], result_value: Mapping[str, Any]):
    invocation = find_invocation(task, str(result_value.get("invocationId") or ""))
    if invocation is None:
        raise ValueError("AgentResult has no unique stored invocation")
    result_value = normalize_result(repo, invocation, result_value)
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


def _host_usage_receipt(task: Mapping[str, Any], invocation: Mapping[str, Any], receipt: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Accept only a host/runner receipt bound to this invocation and grant."""
    if not isinstance(receipt, Mapping) or receipt.get("trusted") is not True:
        return None
    if receipt.get("source") not in {"host-v1", "host", "runner"}:
        return None
    if receipt.get("invocationId") != invocation.get("invocationId"):
        return None
    reservations = [item for item in ((task.get("budget") or {}).get("reservations") or [])
                    if isinstance(item, Mapping) and item.get("invocationId") == invocation.get("invocationId")]
    if len(reservations) != 1:
        return None
    grant = reservations[0].get("amount")
    receipt_grant = receipt.get("grant", receipt.get("grantAmount"))
    calls = receipt.get("toolCalls")
    if receipt_grant != grant or not isinstance(calls, int) or isinstance(calls, bool) or calls < 0 or calls > int(grant):
        return None
    return {"trusted": True, "toolCalls": calls}


def normalize_result(repo: Path, invocation: Mapping[str, Any], result_value: Mapping[str, Any]) -> dict[str, Any]:
    """Fill transport metadata from a named invocation; never invent task evidence."""
    value = dict(result_value)
    value.setdefault("schemaVersion", SCHEMA_VERSION)
    value.setdefault("invocationId", invocation.get("invocationId"))
    value.setdefault("attempt", invocation.get("attempt"))
    for name in ("findings", "blockers", "remainingRisks"):
        value.setdefault(name, [])
    if "changedPaths" not in value:
        if invocation.get("role") == "worker" and value.get("candidateHead"):
            diff = git(repo, "diff", "--name-only", f"{invocation.get('baseSha')}..{value['candidateHead']}")
            if diff.returncode:
                raise ValueError("cannot derive changedPaths for the reported candidate")
            value["changedPaths"] = sorted(set(diff.stdout.splitlines()))
        elif invocation.get("role") != "worker" or value.get("status") != "succeeded":
            value["changedPaths"] = []
    return value


def accept_result(
    repo: Path,
    task_path: Path,
    profile: Mapping[str, Any],
    result_value: Mapping[str, Any],
    expected_revision: int | None = None,
    trusted_usage: Mapping[str, Any] | None = None,
    usage_receipt: Mapping[str, Any] | None = None,
    invocation_id: str | None = None,
) -> dict[str, Any]:
    with task_lock(task_path):
        task = read_object(task_path)
        if expected_revision is None:
            expected_revision = task.get("revision")
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        assert_budget_ledger(task)
        invocation = find_invocation(task, invocation_id or str(result_value.get("invocationId") or ""))
        if invocation is None:
            raise ValueError("result requires an explicit saved invocationId")
        if result_value.get("invocationId") not in (None, invocation["invocationId"]):
            raise ValueError("reported invocationId differs from the selected invocation")
        result_value = normalize_result(repo, invocation, result_value)
        recorded = [item for item in as_list((task.get("execution") or {}).get("agentResults"))
                    if item.get("invocationId") == invocation["invocationId"]]
        if recorded:
            content = lambda value: {key: item for key, item in value.items() if key != "usage"}
            if len(recorded) != 1 or content(recorded[0]) != content(result_value):
                raise ValueError("invocation already has a different recorded result")
            return {"ok": True, "reused": True, "taskId": task.get("taskId"), "revision": task["revision"], "invocationId": invocation["invocationId"]}
        checked = result_findings(repo, task, profile, result_value)
        if not checked.ok:
            raise ValueError(checked.findings[0].message)
        invocation = find_invocation(task, str(result_value.get("invocationId") or ""))
        stored_result = dict(result_value)
        accounting = invocation.get("usageAccounting") if isinstance(invocation, Mapping) else None
        if task.get("budget"):
            receipt = trusted_usage if trusted_usage is not None else usage_receipt
            if accounting == "host-v1":
                accepted_usage = _host_usage_receipt(task, invocation, receipt)
                consumed = settle_tool_calls(task["budget"], str(result_value.get("invocationId")), accepted_usage)
                # Never persist model-authored usage as an authority.  The
                # normalized ledger entry is the only usage attached to the
                # accepted result.
                stored_result.pop("usage", None)
                stored_result["usage"] = {"trusted": bool(accepted_usage), "toolCalls": consumed, "source": "host-v1", "invocationId": invocation.get("invocationId")}
            else:
                consumed = settle_tool_calls(task["budget"], str(result_value.get("invocationId")), result_value.get("usage"))
                stored_result.pop("usage", None)
                stored_result["usage"] = {"trusted": False, "toolCalls": consumed, "source": "invocation-v1", "invocationId": invocation.get("invocationId")}
        task.setdefault("execution", {}).setdefault("agentResults", []).append(stored_result)
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        return {"ok": True, "taskId": task.get("taskId"), "revision": task["revision"], "invocationId": result_value.get("invocationId")}


def record_route_failure(
    task_path: Path, *, failure_value: Mapping[str, Any], expected_revision: int,
    usage: Mapping[str, Any] | None = None,
    host_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a normalized RouteFailure and settle its outstanding grant atomically."""
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        assert_budget_ledger(task)
        normalized = normalize_route_failure(failure_value)
        invocation_id = normalized["invocationId"]
        invocation = find_invocation(task, invocation_id)
        if invocation is None:
            raise ValueError("RouteFailure has no unique stored invocation")
        if (invocation.get("assignedHost") and invocation.get("assignedModel")
                and invocation.get("assignedModel") != "current-host/default"):
            if (normalized["route"].get("hostId") != invocation.get("assignedHost")
                    or route_name(normalized["route"]) != invocation.get("assignedModel")):
                raise ValueError("RouteFailure route does not match the stored invocation")
        failures = task.setdefault("execution", {}).setdefault("routeFailures", [])
        if any(isinstance(item, Mapping) and item.get("invocationId") == invocation_id for item in failures):
            raise ValueError("RouteFailure invocationId was already settled")
        if invocation.get("usageAccounting") == "host-v1":
            accepted_usage = _host_usage_receipt(task, invocation, host_receipt)
        else:
            accepted_usage = None
        consumed = settle_tool_calls(task["budget"], invocation_id, accepted_usage)
        failures.append({
            **normalized,
            "usage": {
                "trusted": bool(isinstance(accepted_usage, Mapping) and accepted_usage.get("trusted") is True),
                "toolCalls": consumed,
                "source": invocation.get("usageAccounting") or "legacy-v0",
                "invocationId": invocation_id,
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
        assert_budget_ledger(task)
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
        assert_budget_ledger(task)
        if not task.get("budget"):
            raise ValueError("Task budget must be frozen by the first invocation before extension")
        entry = extend_budget(task["budget"], kind=kind, amount=amount, unresolved_question=unresolved_question, progress=progress, role=role)
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        return {"ok": True, "taskId": task.get("taskId"), "revision": task["revision"], "extension": entry}


def start_repair(
    task_path: Path,
    *,
    expected_revision: int,
    progress: str,
    plan: str,
    review: Mapping[str, Any] | None = None,
    review_ref: str | None = None,
    readiness_failure: Mapping[str, Any] | None = None,
    integration_failure: Mapping[str, Any] | None = None,
    target_finding_ids: list[str] | None = None,
    narrowed_cause: bool = False,
    scope_changed: bool = False,
    approach_invalid: bool = False,
) -> dict[str, Any]:
    """Atomically authorize one semantic repair cycle or require replanning."""
    if not plan.strip():
        raise ValueError("repair start requires a concrete plan")
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        history = task.setdefault("execution", {}).setdefault("repairHistory", [])
        if not isinstance(history, list):
            raise ValueError("execution.repairHistory must be an array")
        budget = task.get("budget")
        if not isinstance(budget, Mapping):
            raise ValueError("repair start requires a frozen Task budget")
        maximum = int(((budget.get("policy") or {}).get("maxRepairs", 3)))
        if len(history) >= maximum:
            task["previousState"] = task.get("state")
            task["state"] = "Replan Required"
            task["revision"] = expected_revision + 1
            task.setdefault("execution", {})["repairDecision"] = {"status": "replan", "reason": "repair ceiling exhausted"}
            write_object(task_path, task)
            return {"ok": False, "status": "replan", "taskId": task.get("taskId"), "revision": task["revision"]}
        if review is not None:
            if review.get("status") != "ChangesRequested":
                raise ValueError("repair start requires a ChangesRequested immutable review")
            source_ids = sorted(unresolved_finding_ids(review))
            source_digest = review_digest(review)
            source_head = (review.get("subject") or {}).get("headSha") if isinstance(review.get("subject"), Mapping) else None
            candidate_refs = {str(value) for value in (review_ref, review.get("_evidencePath")) if value}
            applications = as_list((task.get("execution") or {}).get("reviewApplications"))
            matched = next((item for item in applications if isinstance(item, Mapping)
                            and item.get("digest") == source_digest and str(item.get("reviewRef") or "") in candidate_refs), None)
            if matched is None:
                raise ValueError("repair source review must match a persisted immutable review application")
            source_ref = str(matched.get("reviewRef"))
        elif readiness_failure is not None:
            stored = ((task.get("execution") or {}).get("candidateReadiness") if isinstance(task.get("execution"), Mapping) else None)
            if not isinstance(stored, Mapping) or stored.get("status") != "failed" or not stored.get("digest") or readiness_digest(stored) != stored.get("digest"):
                raise ValueError("repair readiness source requires the stored failed candidateReadiness")
            supplied = readiness_failure.get("candidateReadiness") if isinstance(readiness_failure.get("candidateReadiness"), Mapping) else readiness_failure
            if not isinstance(supplied, Mapping) or supplied.get("digest") != stored.get("digest"):
                raise ValueError("repair readiness source is stale or mutated")
            source_ids = [str(item) for item in as_list(readiness_failure.get("findingIds") or readiness_failure.get("targets")) if item]
            stored_targets = [str(item) for item in as_list(stored.get("findingIds") or stored.get("targets") or stored.get("targetFindingIds")) if item]
            if not stored_targets or not set(source_ids).issubset(set(stored_targets)):
                raise ValueError("repair readiness targets must match exact stored candidate readiness targets")
            source_digest = str(stored["digest"])
            source_head = stored.get("candidateHead") or candidate_head(task)
            source_ref = "readiness:" + source_digest
        elif integration_failure is not None:
            stored = ((task.get("execution") or {}).get("integrationFailure") if isinstance(task.get("execution"), Mapping) else None)
            if not isinstance(stored, Mapping) or stored.get("digest") != integration_failure.get("digest"):
                raise ValueError("repair integration source must match stored integrationFailure evidence")
            source_ids = [str(item) for item in as_list(stored.get("targetFindingIds")) if item]
            source_digest = str(stored.get("digest") or "")
            source_head = stored.get("headSha")
            source_ref = "integration:" + source_digest
        else:
            raise ValueError("repair start requires an immutable review, failed readiness, or integration evidence")
        active = task.setdefault("execution", {}).get("activeRepair")
        if (isinstance(active, Mapping) and active.get("status") == "open"
                and active.get("sourceDigest") == source_digest and active.get("plan") == plan.strip()):
            return {"ok": True, "status": "repair", "idempotent": True, "taskId": task.get("taskId"), "revision": expected_revision, "cycle": active.get("cycle"), "targetFindingIds": active.get("targetFindingIds") or []}
        ids = [str(item) for item in (target_finding_ids or source_ids) if str(item)]
        if not ids:
            raise ValueError("repair start requires at least one concrete finding or readiness target")
        if not set(ids).issubset(set(source_ids)):
            raise ValueError("repair targets must be a subset of material findings in the immutable source")
        prior = history[-1] if history else None
        if prior and (scope_changed or approach_invalid or not (narrowed_cause or bool(as_list(prior.get("resolvedFindingIds"))))):
            task["previousState"] = task.get("state")
            task["state"] = "Replan Required"
            task["revision"] = expected_revision + 1
            task.setdefault("execution", {})["repairDecision"] = {"status": "replan", "reason": "no resolved finding or narrowed cause", "sourceDigest": source_digest}
            write_object(task_path, task)
            return {"ok": False, "status": "replan", "taskId": task.get("taskId"), "revision": task["revision"]}
        cycle = len(history) + 1
        remaining = list(ids)
        entry = {
            "cycle": cycle,
            "reviewRef": source_ref,
            "sourceDigest": source_digest,
            "sourceHead": source_head,
            "targetFindingIds": ids,
            "progress": progress.strip() or "pending repair result",
            "plan": plan.strip(),
            "resolvedFindingIds": [],
            "remainingFindingIds": remaining,
            "scopeChanged": bool(scope_changed),
            "approachInvalid": bool(approach_invalid),
            "narrowedCause": bool(narrowed_cause),
            "status": "open",
        }
        history.append(entry)
        if integration_failure is not None:
            task["reviewSpec"] = {"mode": "delta", "fullBaseSha": task.get("baseSha"), "previousReviewRef": source_ref, "previousReviewDigest": source_digest, "previousHead": source_head, "findingIds": list(ids)}
        budget.setdefault("usage", {})["repairCycles"] = cycle
        task.setdefault("execution", {})["activeRepair"] = {
            "cycle": cycle,
            "sourceDigest": source_digest,
            "sourceHead": source_head,
            "targetFindingIds": ids,
            "plan": plan.strip(),
            "narrowedCause": bool(narrowed_cause),
            "status": "open",
        }
        task["previousState"] = task.get("state")
        task["state"] = "Repair"
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        return {"ok": True, "status": "repair", "taskId": task.get("taskId"), "revision": task["revision"], "cycle": cycle, "targetFindingIds": ids}


def apply_review(
    repo: Path,
    task_path: Path,
    review_path: Path,
    *,
    expected_revision: int,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply an immutable review to the Task with a revision CAS."""
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        relative, canonical = canonical_evidence_path(repo, review_path)
        if not relative or not canonical or not canonical.is_file():
            raise ValueError("immutable review artifact must be an existing file inside the repository")
        review = read_object(canonical)
        review["_evidencePath"] = relative
        if isinstance(review.get("reviewSpec"), Mapping) and review["reviewSpec"].get("mode") == "delta":
            previous_ref = review["reviewSpec"].get("previousReviewRef")
            previous_relative, previous_path = canonical_evidence_path(repo, str(previous_ref).split("#", 1)[0] if previous_ref else None)
            if not previous_relative or not previous_path or not previous_path.is_file():
                raise ValueError("delta review requires an immutable predecessor inside the repository")
            review["previousReview"] = read_object(previous_path)
            review["previousReview"]["_evidencePath"] = previous_relative
        checked = validate_review(review, task, profile=profile, applying=True)
        if not checked.ok:
            raise ValueError(checked.findings[0].message)
        digest = review_digest(review)
        applications = task.setdefault("execution", {}).setdefault("reviewApplications", [])
        for application in applications:
            if isinstance(application, Mapping) and application.get("reviewRef") == relative and application.get("digest") == digest:
                return {"ok": True, "idempotent": True, "taskId": task.get("taskId"), "revision": expected_revision, "reviewRef": relative, "digest": digest}
        status = review.get("status")
        spec = review.get("reviewSpec") if isinstance(review.get("reviewSpec"), Mapping) else {}
        candidate_subject = review.get("subject") if isinstance(review.get("subject"), Mapping) else {}
        lane = str(spec.get("reviewLane") or f"{review.get('hostId') or 'native'}::{review.get('reviewerModel') or 'current-host/default'}")
        review_basis = _review_basis(spec, candidate_subject.get("headSha"))
        execution = task.setdefault("execution", {})
        material_finding_ids = sorted(unresolved_finding_ids(review))
        chains = execution.setdefault("reviewChains", {})
        if not isinstance(chains, dict):
            raise ValueError("execution.reviewChains must be an object")
        chains[lane] = {
            "reviewerLane": lane,
            "reviewRef": relative,
            "digest": digest,
            "head": candidate_subject.get("headSha"),
            "fullBaseSha": spec.get("fullBaseSha"),
            "planDigest": spec.get("planDigest"),
            "validationDigest": spec.get("validationDigest"),
            "findingIds": material_finding_ids,
            "reviewSpec": dict(spec),
            "forceFull": bool(spec.get("requiresFullReview")),
        }
        active = execution.get("activeRepair") if isinstance(execution.get("activeRepair"), Mapping) else None
        repair_decision = assess_repair_progress(task, review) if status == "ChangesRequested" else "accept"
        repair_outcome: dict[str, Any] | None = None
        if isinstance(active, Mapping) and active.get("status") == "open":
            history = execution.setdefault("repairHistory", [])
            entry = next((item for item in reversed(history) if isinstance(item, Mapping) and item.get("cycle") == active.get("cycle")), None)
            if entry is None:
                raise ValueError("active repair has no persisted repair history entry")
            target_ids = {str(item) for item in as_list(active.get("targetFindingIds")) if item}
            dispositions = review.get("findingDispositions")
            if not isinstance(dispositions, Mapping) or target_ids - {str(key) for key in dispositions}:
                raise ValueError("review after active repair requires a disposition for every target finding")

            def disposition_state(value: Any) -> str:
                if isinstance(value, Mapping):
                    value = value.get("disposition") or value.get("status") or value.get("state")
                return str(value or "").strip().lower()

            current_ids = {
                str(item.get("findingId")) for item in review.get("findings") or []
                if isinstance(item, Mapping) and item.get("findingId") and item.get("priority") in BLOCKING_PRIORITIES
            }
            resolved = sorted({finding_id for finding_id in target_ids if disposition_state(dispositions[finding_id]) == "resolved"})
            remaining = sorted((target_ids - set(resolved)) | current_ids)
            entry.update({
                "resolvedFindingIds": resolved,
                "remainingFindingIds": remaining,
                "status": "closed",
                "closedByReviewRef": relative,
                "closedReviewDigest": digest,
            })
            closed = dict(active)
            closed.update({"status": "closed", "resolvedFindingIds": resolved, "remainingFindingIds": remaining, "closedByReviewRef": relative, "closedReviewDigest": digest})
            execution["activeRepair"] = closed
            repair_outcome = {"cycle": active.get("cycle"), "resolvedFindingIds": resolved, "remainingFindingIds": remaining}
            if status == "ChangesRequested":
                policy = (task.get("budget") or {}).get("policy") or {}
                maximum = policy.get("maxRepairs", ((profile or {}).get("orchestration") or {}).get("maxRepairs", 3))
                must_replan = review.get("cycle", 1) >= maximum + 1 or entry.get("scopeChanged") or entry.get("approachInvalid")
                repair_decision = "repair" if not must_replan and (resolved or entry.get("narrowedCause")) else "replan"
        task["previousState"] = task.get("state")
        if status == "Accepted" and task.get("reviewPolicy") == "cross":
            accepted = [item for item in applications if isinstance(item, Mapping) and item.get("status") == "Accepted"]
            accepted.append({"status": status, "candidateHead": candidate_subject.get("headSha"), "reviewLane": lane, "basis": review_basis})
            lanes = {
                str(item.get("reviewLane")) for item in accepted
                if item.get("candidateHead") == candidate_subject.get("headSha") and item.get("reviewLane")
                and (item.get("basis") or _review_basis(item.get("reviewSpec") if isinstance(item.get("reviewSpec"), Mapping) else {}, item.get("candidateHead"))) == review_basis
            }
            cross_pending = len(lanes) < 2
        else:
            cross_pending = False
        if status == "Accepted" and not cross_pending:
            task["state"] = "Accepted"
            execution["crossReviewPending"] = False
        elif spec.get("requiresFullReview") is True:
            task["state"] = "Candidate"
            execution["fullReviewRequired"] = True
            fresh = _candidate_review_spec(task, lane)
            fresh.update({"mode": "full", "previousReviewRef": None, "previousReviewDigest": None, "previousHead": None, "findingIds": []})
            fresh.pop("requiresFullReview", None)
            task["reviewSpec"] = fresh
        elif status == "Accepted" and cross_pending:
            task["state"] = "Candidate"
            execution["crossReviewPending"] = True
        elif repair_decision == "replan":
            task["state"] = "Replan Required"
            execution["repairDecision"] = {"status": "replan", "reason": "review made no grounded progress", "reviewRef": relative, "digest": digest}
        else:
            task["state"] = "Repair"
            execution["repairDecision"] = {"status": "repair", "reviewRef": relative, "digest": digest, "outcome": repair_outcome}
        if isinstance(spec, Mapping) and not (spec.get("requiresFullReview") is True and status == "ChangesRequested"):
            task["reviewSpec"] = dict(spec)
        task["reviewRef"] = relative
        task.setdefault("reviewHistory", []).append(relative)
        application = {"reviewRef": relative, "digest": digest, "status": status, "mode": spec.get("mode", "legacy"),
                       "candidateHead": candidate_subject.get("headSha"), "reviewLane": lane, "basis": review_basis,
                       "reviewSpec": dict(spec), "repairOutcome": repair_outcome, "appliedRevision": expected_revision + 1}
        applications.append(application)
        bindings = task.setdefault("reviewBindings", [])
        binding = {"subject": "candidate", "reviewRef": relative, "digest": digest, "invocationId": spec.get("invocationId"), "status": status}
        if binding not in bindings:
            bindings.append(binding)
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        return {"ok": True, "idempotent": False, "taskId": task.get("taskId"), "revision": task["revision"], "reviewRef": relative, "digest": digest, "state": task["state"]}
