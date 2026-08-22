"""Confirmation-gated project routes and task-local recovery plans."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .contracts import ROUTE_FAILURE_CATEGORIES, SCHEMA_VERSION, read_object, route_name, schema_error, validate_model_routes, write_object

ROLES = ("worker", "reviewer", "explorer")
CAPACITY_STATUSES = {"available", "depleted", "unknown"}


@contextmanager
def _task_lock(task_path: Path) -> Iterator[None]:
    lock_path = task_path.with_suffix(task_path.suffix + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise ValueError(f"Task is locked: {task_path}") from error
    os.close(descriptor)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def normalize_catalog(value: Mapping[str, Any]) -> dict[str, Any]:
    host_id = value.get("hostId")
    models = value.get("models")
    if not isinstance(host_id, str) or not host_id.strip() or not isinstance(models, list):
        raise ValueError("catalog requires hostId and models array")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(models):
        if not isinstance(item, Mapping) or not item.get("providerId") or not item.get("modelId"):
            raise ValueError(f"catalog.models[{index}] requires providerId and modelId")
        variants = item.get("variants", [])
        if not isinstance(variants, list) or any(not isinstance(entry, str) or not entry.strip() for entry in variants):
            raise ValueError(f"catalog.models[{index}].variants must be a string array")
        normalized.append({
            "providerId": str(item["providerId"]),
            "modelId": str(item["modelId"]),
            "variants": sorted(set(variants)),
            "capabilities": sorted(set(str(entry) for entry in item.get("capabilities", []))),
        })
    normalized.sort(key=lambda item: (item["providerId"], item["modelId"]))
    return {"hostId": host_id, "models": normalized}


def normalize_route_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    host_id = value.get("hostId")
    if not isinstance(host_id, str) or not host_id.strip():
        raise ValueError("RouteRef requires hostId")
    route = {
        "hostId": host_id,
        "providerId": value.get("providerId"),
        "modelId": value.get("modelId"),
    }
    if value.get("variantId") not in (None, ""):
        route["variantId"] = value["variantId"]
    if not route_name(route):
        raise ValueError("RouteRef requires providerId and modelId")
    return route


def route_ref_name(value: Mapping[str, Any]) -> str:
    route = normalize_route_ref(value)
    return f"{route['hostId']}::{route_name(route)}"


def normalize_route_failure(value: Mapping[str, Any]) -> dict[str, Any]:
    category = value.get("category")
    if category not in ROUTE_FAILURE_CATEGORIES:
        raise ValueError("RouteFailure category is invalid")
    invocation_id = value.get("invocationId")
    if not isinstance(invocation_id, str) or not invocation_id.strip():
        raise ValueError("RouteFailure requires invocationId")
    result: dict[str, Any] = {
        "category": category,
        "invocationId": invocation_id,
        "route": normalize_route_ref(value.get("route") or {}),
        "resumable": bool(value.get("resumable", False)),
    }
    retry_after = value.get("retryAfter")
    if retry_after is not None:
        if not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool) or retry_after < 0:
            raise ValueError("RouteFailure retryAfter must be non-negative seconds or null")
        result["retryAfter"] = retry_after
    reset_at = value.get("resetAt")
    if reset_at is not None:
        if not isinstance(reset_at, str) or not reset_at.strip():
            raise ValueError("RouteFailure resetAt must be a non-empty string or null")
        result["resetAt"] = reset_at
    return result


def route_failure_action(value: Mapping[str, Any], *, transient_retries: int = 0, context_reductions: int = 0) -> str:
    failure = normalize_route_failure(value)
    category = failure["category"]
    if category == "rate_limited" and failure.get("retryAfter", 31) <= 30 and transient_retries < 1:
        return "retry"
    if category == "transient_transport" and transient_retries < 1:
        return "retry"
    if category == "context_limit" and context_reductions < 1:
        return "shrink-context"
    return "recover"


def normalize_capacity_probe(value: Mapping[str, Any]) -> dict[str, Any]:
    status = value.get("status")
    if status not in CAPACITY_STATUSES:
        raise ValueError("capacity probe status must be available, depleted, or unknown")
    result: dict[str, Any] = {"status": status, "route": normalize_route_ref(value.get("route") or {})}
    remaining = value.get("remainingTokens")
    if remaining is not None:
        if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 0:
            raise ValueError("capacity remainingTokens must be a non-negative integer or null")
        result["remainingTokens"] = remaining
    if value.get("resetAt") is not None:
        if not isinstance(value["resetAt"], str) or not value["resetAt"].strip():
            raise ValueError("capacity resetAt must be a non-empty string or null")
        result["resetAt"] = value["resetAt"]
    return result


def _catalog_routes(catalog: Mapping[str, Any]) -> set[str]:
    routes: set[str] = set()
    for item in catalog["models"]:
        base = f"{item['providerId']}/{item['modelId']}"
        routes.add(base)
        routes.update(f"{base}:{variant}" for variant in item.get("variants", []))
    return routes


def _normalize_catalogs(values: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    catalogs = [normalize_catalog(value) for value in values]
    host_ids = [item["hostId"] for item in catalogs]
    if len(host_ids) != len(set(host_ids)):
        raise ValueError("recovery catalogs require unique hostId values")
    return sorted(catalogs, key=lambda item: item["hostId"])


def _available_route_refs(catalogs: list[Mapping[str, Any]]) -> set[str]:
    return {
        f"{catalog['hostId']}::{route}"
        for catalog in catalogs
        for route in _catalog_routes(catalog)
    }


def _normalize_impact(value: Any, option_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"recovery option {option_id} requires impact")
    impact: dict[str, Any] = {}
    for field in ("quality", "cost", "speed"):
        current = value.get(field)
        if not isinstance(current, str) or not current.strip():
            raise ValueError(f"recovery option {option_id} impact.{field} is required")
        impact[field] = current
    limitations = value.get("limitations", [])
    if not isinstance(limitations, list) or any(not isinstance(item, str) or not item.strip() for item in limitations):
        raise ValueError(f"recovery option {option_id} impact.limitations must be a string array")
    impact["limitations"] = limitations
    return impact


def _normalize_recovery_options(plan: Mapping[str, Any], catalogs: list[Mapping[str, Any]], failure: Mapping[str, Any]) -> list[dict[str, Any]]:
    options = plan.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        raise ValueError("recovery plan requires two to four options")
    available = _available_route_refs(catalogs)
    normalized: list[dict[str, Any]] = []
    option_ids: set[str] = set()
    for index, value in enumerate(options):
        if not isinstance(value, Mapping):
            raise ValueError(f"recovery options[{index}] must be an object")
        option_id = value.get("optionId")
        if not isinstance(option_id, str) or not option_id.strip() or option_id in option_ids:
            raise ValueError("recovery options require unique non-empty optionId values")
        option_ids.add(option_id)
        summary = value.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError(f"recovery option {option_id} requires summary")
        kind = value.get("kind", "routes")
        current: dict[str, Any] = {
            "optionId": option_id,
            "kind": kind,
            "summary": summary,
            "impact": _normalize_impact(value.get("impact"), option_id),
        }
        if kind == "wait":
            resume_at = value.get("resumeAt") or failure.get("resetAt")
            if not isinstance(resume_at, str) or not resume_at.strip():
                raise ValueError(f"wait option {option_id} requires resumeAt or RouteFailure.resetAt")
            current["resumeAt"] = resume_at
        elif kind == "routes":
            role_routes = value.get("roleRoutes")
            if not isinstance(role_routes, Mapping) or set(role_routes) != set(ROLES):
                raise ValueError(f"route option {option_id} requires worker, reviewer, and explorer roleRoutes")
            current["roleRoutes"] = {}
            for role in ROLES:
                choices = role_routes.get(role)
                if not isinstance(choices, list) or not choices:
                    raise ValueError(f"route option {option_id} requires at least one {role} route")
                routes = [normalize_route_ref(item) for item in choices if isinstance(item, Mapping)]
                if len(routes) != len(choices):
                    raise ValueError(f"route option {option_id} {role} routes must be objects")
                names = [route_ref_name(item) for item in routes]
                if len(names) != len(set(names)):
                    raise ValueError(f"route option {option_id} has duplicate {role} routes")
                missing = [name for name in names if name not in available]
                if missing:
                    raise ValueError(f"route is absent from current catalogs: {missing[0]}")
                current["roleRoutes"][role] = routes
        else:
            raise ValueError(f"recovery option {option_id} kind must be routes or wait")
        normalized.append(current)
    return normalized


def build_recovery_proposal(
    config: Mapping[str, Any],
    task: Mapping[str, Any],
    catalog_values: list[Mapping[str, Any]],
    failure_value: Mapping[str, Any],
    plan_value: Mapping[str, Any],
) -> dict[str, Any]:
    if task.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(schema_error("Task", task))
    if not isinstance(task.get("revision"), int):
        raise ValueError("recovery requires a schema v3 Task with revision")
    catalogs = _normalize_catalogs(catalog_values)
    failure = normalize_route_failure(failure_value)
    options = _normalize_recovery_options(plan_value, catalogs, failure)
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "taskId": task.get("taskId"),
        "taskRevision": task["revision"],
        "baseTaskDigest": digest(task),
        "baseConfigDigest": digest(config),
        "catalogDigest": digest(catalogs),
        "trigger": failure,
        "options": options,
    }
    return {**body, "proposalDigest": digest(body)}


def apply_recovery_proposal(
    task_path: Path,
    config: Mapping[str, Any],
    catalog_values: list[Mapping[str, Any]],
    failure_value: Mapping[str, Any],
    plan_value: Mapping[str, Any],
    option_id: str,
    confirmation: str,
) -> dict[str, Any]:
    with _task_lock(task_path):
        return _apply_recovery_proposal_locked(task_path, config, catalog_values, failure_value, plan_value, option_id, confirmation)


def _apply_recovery_proposal_locked(
    task_path: Path,
    config: Mapping[str, Any],
    catalog_values: list[Mapping[str, Any]],
    failure_value: Mapping[str, Any],
    plan_value: Mapping[str, Any],
    option_id: str,
    confirmation: str,
) -> dict[str, Any]:
    task = read_object(task_path)
    proposal = build_recovery_proposal(config, task, catalog_values, failure_value, plan_value)
    if confirmation != proposal["proposalDigest"]:
        raise ValueError("confirmation digest does not match the current recovery proposal")
    selected = next((item for item in proposal["options"] if item["optionId"] == option_id), None)
    if selected is None:
        raise ValueError(f"unknown recovery option: {option_id}")
    previous_revision = task["revision"]
    recovery: dict[str, Any] = {
        "status": "paused" if selected["kind"] == "wait" else "approved",
        "trigger": proposal["trigger"],
        "proposalDigest": confirmation,
        "selectedOptionId": option_id,
        "approvedTaskRevision": previous_revision,
        "scope": "task",
        "roleRoutes": selected.get("roleRoutes", {}),
        "currentIndex": {role: 0 for role in ROLES} if selected["kind"] == "routes" else {},
        "attempts": [],
    }
    if selected["kind"] == "wait":
        recovery["resumeAt"] = selected["resumeAt"]
    else:
        role = str(task.get("role") or "worker")
        if role in ROLES:
            first = recovery["roleRoutes"][role][0]
            models = dict(task.get("models") or {})
            models.update({
                "hostId": first["hostId"],
                "assigned": route_name(first),
                "actual": None,
                "fallbackReason": f"approved-recovery:{confirmation}",
            })
            task["models"] = models
    task["routingRecovery"] = recovery
    task["revision"] = previous_revision + 1
    write_object(task_path, task)
    return {"ok": True, "taskId": task.get("taskId"), "revision": task["revision"], "routingRecovery": recovery}


def advance_recovery_route(
    task_path: Path,
    role: str,
    failure_value: Mapping[str, Any],
    expected_revision: int,
    *,
    transient_retries: int = 0,
    context_reductions: int = 0,
) -> dict[str, Any]:
    with _task_lock(task_path):
        return _advance_recovery_route_locked(
            task_path,
            role,
            failure_value,
            expected_revision,
            transient_retries=transient_retries,
            context_reductions=context_reductions,
        )


def _advance_recovery_route_locked(
    task_path: Path,
    role: str,
    failure_value: Mapping[str, Any],
    expected_revision: int,
    *,
    transient_retries: int = 0,
    context_reductions: int = 0,
) -> dict[str, Any]:
    task = read_object(task_path)
    if task.get("revision") != expected_revision:
        raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
    recovery = dict(task.get("routingRecovery") or {})
    if recovery.get("status") != "approved" or role not in ROLES:
        raise ValueError("advance requires an approved recovery plan and valid role")
    failure = normalize_route_failure(failure_value)
    action = route_failure_action(failure, transient_retries=transient_retries, context_reductions=context_reductions)
    if action != "recover":
        return {"ok": True, "action": action, "revision": task["revision"]}
    role_routes = (recovery.get("roleRoutes") or {}).get(role) or []
    indexes = dict(recovery.get("currentIndex") or {})
    current_index = indexes.get(role, 0)
    if not isinstance(current_index, int) or current_index < 0 or current_index >= len(role_routes):
        raise ValueError(f"invalid recovery cursor for {role}")
    current_route = normalize_route_ref(role_routes[current_index])
    if route_ref_name(current_route) != route_ref_name(failure["route"]):
        raise ValueError("RouteFailure does not match the current approved route")
    attempts = list(recovery.get("attempts") or [])
    attempts.append({"role": role, "route": current_route, "resultCode": failure["category"]})
    recovery["attempts"] = attempts[-12:]
    next_index = current_index + 1
    if next_index >= len(role_routes):
        recovery["status"] = "paused"
        recovery["exhaustedRole"] = role
        action = "proposal-required"
        next_route = None
    else:
        indexes[role] = next_index
        recovery["currentIndex"] = indexes
        next_route = normalize_route_ref(role_routes[next_index])
        action = "advanced"
        if str(task.get("role") or "worker") == role:
            models = dict(task.get("models") or {})
            models.update({"hostId": next_route["hostId"], "assigned": route_name(next_route), "actual": None})
            task["models"] = models
    task["routingRecovery"] = recovery
    task["revision"] = expected_revision + 1
    write_object(task_path, task)
    return {"ok": True, "action": action, "revision": task["revision"], "nextRoute": next_route, "routingRecovery": recovery}


def build_proposal(config: Mapping[str, Any], catalog_value: Mapping[str, Any], routes_value: Mapping[str, Any]) -> dict[str, Any]:
    catalog = normalize_catalog(catalog_value)
    host_id = catalog["hostId"]
    routes = {host_id: routes_value.get(host_id, routes_value)}
    validation = validate_model_routes(routes)
    if not validation.ok:
        raise ValueError(validation.findings[0].message)
    available = _catalog_routes(catalog)
    for role in ROLES:
        for route in routes[host_id][role]:
            name = route_name(route)
            if name not in available:
                raise ValueError(f"route is absent from current {host_id} catalog: {name}")
    before = config.get("modelRoutes", {})
    merged = {**(dict(before) if isinstance(before, Mapping) else {}), **routes}
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "hostId": host_id,
        "baseConfigDigest": digest(config),
        "catalogDigest": digest(catalog),
        "before": before.get(host_id) if isinstance(before, Mapping) else None,
        "after": merged[host_id],
    }
    return {**body, "proposalDigest": digest(body)}


def apply_proposal(config_path: Path, catalog_value: Mapping[str, Any], routes_value: Mapping[str, Any], confirmation: str) -> dict[str, Any]:
    config = read_object(config_path)
    proposal = build_proposal(config, catalog_value, routes_value)
    if confirmation != proposal["proposalDigest"]:
        raise ValueError("confirmation digest does not match the current proposal")
    host_id = proposal["hostId"]
    current = config.get("modelRoutes")
    model_routes = dict(current) if isinstance(current, Mapping) else {}
    model_routes[host_id] = proposal["after"]
    updated = dict(config)
    updated["modelRoutes"] = model_routes
    write_object(config_path, updated)
    return {"ok": True, "proposalDigest": confirmation, "hostId": host_id, "modelRoutes": proposal["after"]}
