"""Frozen per-Task budget policy and cumulative reservation ledger."""

from __future__ import annotations

import copy
from typing import Any, Mapping


HARD_CONTEXT_CEILINGS = {
    "maxPacketBytes": 32768,
    "maxWorkingSetItems": 24,
    "maxExpansions": 3,
}
HARD_TOOL_CALL_CEILINGS = {"explorer": 24, "reviewer": 32, "worker": 48}
INITIAL_CONTEXT = {"maxPacketBytes": 16384, "maxWorkingSetItems": 12, "maxExpansions": 1}
INITIAL_TOOL_CALLS = {"explorer": 12, "reviewer": 16, "worker": 24}
DEFAULT_INVOCATION_LIMITS = {"worker": 5, "reviewer": 7, "explorer": 5}
ACCOUNTING_MODES = {"host-v1", "invocation-v1"}


def policy_from_profile(profile: Mapping[str, Any], accounting_mode: str | None = None) -> dict[str, Any]:
    context = profile.get("contextPolicy") if isinstance(profile.get("contextPolicy"), Mapping) else {}
    ceilings = context.get("ceilings") if isinstance(context.get("ceilings"), Mapping) else {}
    configured_tools = profile.get("invocationBudgets") if isinstance(profile.get("invocationBudgets"), Mapping) else {}
    tools: dict[str, dict[str, int]] = {}
    for role, hard in HARD_TOOL_CALL_CEILINGS.items():
        configured = configured_tools.get(role) if isinstance(configured_tools.get(role), Mapping) else {}
        tools[role] = {
            "initial": int(configured.get("initialToolCalls", INITIAL_TOOL_CALLS[role])),
            "ceiling": int(configured.get("maxToolCalls", hard)),
        }
    selected_mode = accounting_mode or profile.get("accountingMode") or profile.get("budgetAccountingMode") or "host-v1"
    if selected_mode not in ACCOUNTING_MODES:
        raise ValueError("accountingMode must be host-v1 or invocation-v1")
    configured_limits = profile.get("invocationLimits") if isinstance(profile.get("invocationLimits"), Mapping) else {}
    invocation_limits = {
        role: int(configured_limits.get(role, DEFAULT_INVOCATION_LIMITS[role]))
        for role in DEFAULT_INVOCATION_LIMITS
    }
    if any(value < 1 for value in invocation_limits.values()):
        raise ValueError("invocationLimits must contain positive integers")
    return {
        "accountingMode": selected_mode,
        "invocationLimits": invocation_limits,
        "context": {
            name: {"initial": int(context.get(name, INITIAL_CONTEXT[name])), "ceiling": int(ceilings.get(name, hard))}
            for name, hard in HARD_CONTEXT_CEILINGS.items()
        },
        "toolCalls": tools,
        "maxRepairs": int((profile.get("orchestration") or {}).get("maxRepairs", 3)),
    }


def new_task_budget(profile: Mapping[str, Any], accounting_mode: str | None = None) -> dict[str, Any]:
    policy = policy_from_profile(profile, accounting_mode)
    return {
        "policy": policy,
        "usage": {
            "contextExpansions": 0,
            "toolCalls": {role: 0 for role in HARD_TOOL_CALL_CEILINGS},
            "invocations": {role: 0 for role in DEFAULT_INVOCATION_LIMITS},
            "repairCycles": 0,
        },
        "reservations": [],
        "extensions": [],
        "grants": [],
        "lockedRoles": [],
        "stop": None,
    }


def approved_tool_calls(budget: Mapping[str, Any], role: str) -> int:
    policy = ((budget.get("policy") or {}).get("toolCalls") or {}).get(role) or {}
    approved = int(policy.get("initial", 0))
    for item in budget.get("extensions") or []:
        if isinstance(item, Mapping) and item.get("kind") == "toolCalls" and item.get("role") == role:
            amount = item.get("amount", 0)
            approved += amount if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0 else 0
    return min(approved, int(policy.get("ceiling", approved)))


def reserve_tool_calls(budget: dict[str, Any], role: str, invocation_id: str) -> int:
    mode = ((budget.get("policy") or {}).get("accountingMode") or "host-v1")
    if role not in HARD_TOOL_CALL_CEILINGS:
        return 0
    existing = [item for item in budget.get("grants") or [] if isinstance(item, Mapping) and item.get("invocationId") == invocation_id]
    if len(existing) == 1:
        return int(existing[0].get("amount", 0))
    if mode == "invocation-v1":
        limits = ((budget.get("policy") or {}).get("invocationLimits") or {})
        used = int(((budget.get("usage") or {}).get("invocations") or {}).get(role, 0))
        if used >= int(limits.get(role, DEFAULT_INVOCATION_LIMITS[role])):
            return 0
        grant = int((((budget.get("policy") or {}).get("toolCalls") or {}).get(role) or {}).get("initial", INITIAL_TOOL_CALLS[role]))
        budget.setdefault("usage", {}).setdefault("invocations", {})[role] = used + 1
        budget.setdefault("reservations", []).append({"invocationId": invocation_id, "role": role, "amount": grant})
        budget.setdefault("grants", []).append({"invocationId": invocation_id, "role": role, "amount": grant})
        budget["stop"] = None
        return grant
    if role in (budget.get("lockedRoles") or []):
        return 0
    used = int(((budget.get("usage") or {}).get("toolCalls") or {}).get(role, 0))
    outstanding = sum(
        item.get("amount", 0) for item in budget.get("reservations") or []
        if isinstance(item, Mapping) and item.get("role") == role and isinstance(item.get("amount"), int) and not isinstance(item.get("amount"), bool)
    )
    remaining = approved_tool_calls(budget, role) - used - outstanding
    if remaining <= 0:
        return 0
    grant = remaining
    budget.setdefault("reservations", []).append({"invocationId": invocation_id, "role": role, "amount": grant})
    budget.setdefault("grants", []).append({"invocationId": invocation_id, "role": role, "amount": grant})
    budget["stop"] = None
    return grant


def settle_tool_calls(budget: dict[str, Any], invocation_id: str, usage: Mapping[str, Any] | None) -> int:
    reservations = budget.get("reservations") or []
    matches = [item for item in reservations if isinstance(item, Mapping) and item.get("invocationId") == invocation_id]
    if len(matches) != 1:
        raise ValueError("AgentResult has no unique budget reservation")
    reservation = matches[0]
    role, grant = str(reservation.get("role")), int(reservation.get("amount", 0))
    mode = ((budget.get("policy") or {}).get("accountingMode") or "host-v1")
    if mode == "invocation-v1":
        budget["reservations"] = [item for item in reservations if item is not reservation]
        if budget.get("stop") and budget["stop"].get("invocationId") == invocation_id:
            budget["stop"] = None
        return 0
    trusted = bool(isinstance(usage, Mapping) and usage.get("trusted") is True)
    reported = usage.get("toolCalls") if isinstance(usage, Mapping) else None
    if trusted:
        if not isinstance(reported, int) or isinstance(reported, bool) or reported < 0 or reported > grant:
            raise ValueError("trusted tool-call usage must be between zero and the invocation grant")
        consumed = reported
    else:
        consumed = grant
        locked = budget.setdefault("lockedRoles", [])
        if role not in locked:
            locked.append(role)
    budget.setdefault("usage", {}).setdefault("toolCalls", {})[role] = int(
        budget.setdefault("usage", {}).setdefault("toolCalls", {}).get(role, 0)
    ) + consumed
    budget["reservations"] = [item for item in reservations if item is not reservation]
    if budget.get("stop") and budget["stop"].get("invocationId") == invocation_id:
        budget["stop"] = None
    return consumed


def approved_context_value(budget: Mapping[str, Any], name: str) -> int:
    policy = (((budget.get("policy") or {}).get("context") or {}).get(name) or {})
    approved = int(policy.get("initial", 0)) + sum(
        item.get("amount", 0) for item in budget.get("extensions") or []
        if isinstance(item, Mapping) and item.get("kind") == name and isinstance(item.get("amount"), int) and not isinstance(item.get("amount"), bool)
    )
    return min(approved, int(policy.get("ceiling", approved)))


def consume_context_expansions(budget: dict[str, Any], amount: int = 1) -> int:
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
        raise ValueError("context expansion usage must be a positive integer")
    usage = budget.setdefault("usage", {})
    current = int(usage.get("contextExpansions", 0))
    if current + amount > approved_context_value(budget, "maxExpansions"):
        raise ValueError("context expansion budget exhausted")
    usage["contextExpansions"] = current + amount
    return usage["contextExpansions"]


def extend_budget(
    budget: dict[str, Any], *, kind: str, amount: int, unresolved_question: str,
    progress: str, role: str | None = None,
) -> dict[str, Any]:
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
        raise ValueError("budget extension amount must be a positive integer")
    if not unresolved_question.strip() or not progress.strip():
        raise ValueError("budget extension requires an unresolved question and achieved progress")
    if kind == "toolCalls":
        if role not in HARD_TOOL_CALL_CEILINGS:
            raise ValueError("tool-call extension requires explorer, reviewer, or worker role")
        if role in (budget.get("lockedRoles") or []):
            raise ValueError("untrusted tool-call accounting forbids automatic extension for this role")
        approved = approved_tool_calls(budget, role)
        ceiling = int((((budget.get("policy") or {}).get("toolCalls") or {}).get(role) or {}).get("ceiling", 0))
        if approved + amount > ceiling:
            raise ValueError("tool-call extension exceeds the frozen Task ceiling")
    elif kind in HARD_CONTEXT_CEILINGS:
        role = None
        policy = (((budget.get("policy") or {}).get("context") or {}).get(kind) or {})
        approved = int(policy.get("initial", 0)) + sum(
            item.get("amount", 0) for item in budget.get("extensions") or []
            if isinstance(item, Mapping) and item.get("kind") == kind and isinstance(item.get("amount"), int) and not isinstance(item.get("amount"), bool)
        )
        if approved + amount > int(policy.get("ceiling", 0)):
            raise ValueError("context extension exceeds the frozen Task ceiling")
    else:
        raise ValueError("unsupported budget extension kind")
    entry = {
        "kind": kind, "role": role, "amount": amount,
        "unresolvedQuestion": unresolved_question.strip(), "progress": progress.strip(),
    }
    budget.setdefault("extensions", []).append(entry)
    return copy.deepcopy(entry)
