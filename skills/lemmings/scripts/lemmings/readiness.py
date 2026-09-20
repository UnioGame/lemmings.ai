"""Deterministic candidate preparation and the reviewer readiness gate.

The Task remains the canonical store.  This module deliberately keeps command
output out of prompts: a bounded tail is persisted as evidence and the
reviewer only receives the resulting digest and status.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    ValidationResult,
    as_list,
    candidate_head,
    git,
    normalize_path,
    path_matches,
    plan_digest,
    read_object,
    validate_repository_ownership,
    write_object,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validation_digest(task: Mapping[str, Any]) -> str:
    """Return the digest of checks that must be true for this candidate."""
    validation = task.get("validation") if isinstance(task.get("validation"), Mapping) else {}
    body = {
        "commands": [str(value).strip() for value in as_list(validation.get("commands")) if str(value).strip()],
        "riskToTest": validation.get("riskToTest") or [],
        "allowedOutputs": validation.get("allowedOutputs") or [],
    }
    return _digest(body)


def result_digest(value: Mapping[str, Any]) -> str:
    return _digest({key: value for key, value in value.items() if key not in {"_evidencePath", "usage"}})


def readiness_digest(value: Mapping[str, Any]) -> str:
    return _digest({key: item for key, item in value.items() if key not in {"digest", "preparedAt"}})


def _task_relative(repo: Path, task_path: Path | None) -> str | None:
    if task_path is None:
        return None
    try:
        return normalize_path(task_path.resolve().relative_to(repo.resolve()).as_posix())
    except ValueError:
        return None


def _clean(repo: Path, task_path: Path | None, task: Mapping[str, Any]) -> bool:
    status = git(repo, "status", "--porcelain")
    if status.returncode:
        return False
    task_relative = _task_relative(repo, task_path)
    allowed = [str(value) for value in as_list((task.get("validation") or {}).get("allowedOutputs"))]
    for line in status.stdout.splitlines():
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if task_relative and normalize_path(path) in {task_relative, task_relative + ".lock"}:
            continue
        normalized_path = normalize_path(path)
        if any(path_matches(path, rule) or (line.startswith("?? ") and normalize_path(str(rule)).startswith(normalized_path + "/")) for rule in allowed):
            continue
        return False
    return True


def _live_candidate(repo: Path, task: Mapping[str, Any], task_path: Path | None, head: str) -> ValidationResult:
    result = ValidationResult()
    current = git(repo, "rev-parse", "--verify", "HEAD^{commit}")
    if current.returncode or current.stdout.strip() != head:
        result.error("candidate.live_head", "candidate readiness requires git HEAD to equal the recorded candidate head")
    if not _clean(repo, task_path, task):
        result.error("candidate.live_tree", "candidate readiness requires a clean tree outside the canonical Task and allowed outputs")
    return result


def _worker_result(task: Mapping[str, Any], invocation_id: str | None, head: str) -> Mapping[str, Any] | None:
    results = [item for item in as_list((task.get("execution") or {}).get("agentResults")) if isinstance(item, Mapping)]
    candidates = [item for item in results if item.get("status") == "succeeded" and item.get("candidateHead") == head]
    if invocation_id:
        candidates = [item for item in candidates if item.get("invocationId") == invocation_id]
    return candidates[-1] if candidates else None


def _debt_for(debts: list[Any], command: str, head: str) -> Mapping[str, Any] | None:
    for debt in debts:
        if not isinstance(debt, Mapping):
            continue
        debt_command = debt.get("command") or debt.get("check")
        debt_head = debt.get("candidateHead") or debt.get("headSha")
        if (str(debt_command or "") == command and str(debt_head or "") == head
                and str(debt.get("classification") or debt.get("kind") or "") == "unavailable"
                and all(debt.get(key) not in (None, "") for key in ("reason", "owner", "futureGate"))):
            return debt
    return None


def _check_entry(checks: list[Any], command: str, head: str) -> Mapping[str, Any] | None:
    for check in checks:
        if isinstance(check, Mapping) and str(check.get("command") or "") == command and str(check.get("headSha") or "") == head:
            return check
    return None


def _check_passed(check: Mapping[str, Any], debt: Mapping[str, Any] | None) -> bool:
    """Unavailable checks pass only when their exact executor debt is present."""
    if check.get("passed") is True:
        return True
    return str(check.get("classification") or "executed") == "unavailable" and debt is not None


def _next_lane_spec(task: Mapping[str, Any], readiness: Mapping[str, Any], head: str, lane: str, chain: Mapping[str, Any]) -> dict[str, Any]:
    previous = chain.get("reviewSpec") if isinstance(chain.get("reviewSpec"), Mapping) else {}
    history = ((task.get("execution") or {}).get("repairHistory") if isinstance(task.get("execution"), Mapping) else []) or []
    scope_changed = bool(history and isinstance(history[-1], Mapping) and (history[-1].get("scopeChanged") or history[-1].get("approachInvalid")))
    contract_changed = (
        previous.get("fullBaseSha") not in (None, task.get("baseSha"))
        or previous.get("planDigest") not in (None, plan_digest(task))
        or previous.get("validationDigest") not in (None, validation_digest(task))
    )
    fresh = bool(chain.get("forceFull") or scope_changed or contract_changed)
    spec = dict(previous)
    spec.update({
        "fullBaseSha": task.get("baseSha"),
        "candidateHead": head,
        "readinessDigest": readiness.get("digest"),
        "planDigest": plan_digest(task),
        "validationDigest": validation_digest(task),
        "reviewLane": lane,
    })
    for key in ("invocationId", "invocationBasis", "invocationDigest"):
        spec.pop(key, None)
    if fresh:
        spec.update({"mode": "full", "previousReviewRef": None, "previousReviewDigest": None, "previousHead": None,
                     "findingIds": [], "inspectionRange": None})
    else:
        spec.update({"mode": "delta", "previousReviewRef": chain.get("reviewRef"), "previousReviewDigest": chain.get("digest"),
                     "previousHead": chain.get("head"), "findingIds": list(chain.get("findingIds") or []),
                     "inspectionRange": {"from": chain.get("head"), "to": head}})
    return spec


def validate_candidate_readiness(
    repo: Path,
    task: Mapping[str, Any],
    *,
    candidate_head_value: str | None = None,
    task_path: Path | None = None,
    require: bool = True,
) -> ValidationResult:
    """Validate readiness stored on a Task against the current candidate.

    A missing gate is a hard error for new reviewer dispatches.  Historical
    v5.0.0 Tasks can still be inspected by the general Task validator; they
    are upgraded when a new candidate is prepared.
    """
    result = ValidationResult()
    execution = task.get("execution") if isinstance(task.get("execution"), Mapping) else {}
    readiness = execution.get("candidateReadiness") if isinstance(execution, Mapping) else None
    if not isinstance(readiness, Mapping):
        if require:
            result.error("candidate.readiness", "candidate readiness evidence is required before reviewer dispatch")
        return result
    head = str(candidate_head_value or candidate_head(task) or "")
    if not head:
        result.error("candidate.readiness_head", "candidate readiness requires a candidate head")
        return result
    result.extend(_live_candidate(repo, task, task_path, head))
    if readiness.get("status") != "passed":
        result.error("candidate.readiness_status", "candidate readiness checks did not pass")
    if readiness.get("candidateHead") != head:
        result.error("candidate.readiness_stale", "candidate readiness is bound to a different candidate head")
    if readiness.get("baseSha") != task.get("baseSha"):
        result.error("candidate.readiness_base", "candidate readiness is bound to a different base")
    expected_plan = plan_digest(task)
    expected_validation = validation_digest(task)
    if readiness.get("planDigest") != expected_plan:
        result.error("candidate.readiness_plan", "candidate readiness plan digest is stale")
    if readiness.get("validationDigest") != expected_validation:
        result.error("candidate.readiness_validation", "candidate readiness validation digest is stale")
    if readiness.get("digest") != readiness_digest(readiness):
        result.error("candidate.readiness_digest", "candidate readiness digest is invalid")
    checks = readiness.get("checks")
    if not isinstance(checks, list):
        result.error("candidate.readiness_checks", "candidate readiness checks must be an array")
        checks = []
    debts = readiness.get("debt")
    if not isinstance(debts, list):
        result.error("candidate.readiness_debt", "candidate readiness debt must be an array")
        debts = []
    commands = [str(value).strip() for value in as_list((task.get("validation") or {}).get("commands")) if str(value).strip()]
    for command in commands:
        check = _check_entry(checks, command, head)
        debt = _debt_for(debts, command, head)
        if check is None and debt is None:
            result.error("candidate.readiness_missing", f"required validation check is missing: {command}")
            continue
        if check is not None:
            passed = check.get("passed") is True
            # A failed process is never converted into unavailable debt.  A
            # debt entry is only legal when no executor check was recorded.
            classification = str(check.get("classification") or "executed")
            if not passed and classification != "unavailable":
                result.error("candidate.readiness_failed", f"validation check failed: {command}")
            if classification == "unavailable" and debt is None:
                result.error("candidate.readiness_missing", f"unavailable validation check requires exact readiness debt: {command}")
            if check.get("headSha") != head:
                result.error("candidate.readiness_stale", f"validation check is stale: {command}")
        if debt is not None and check is not None and check.get("passed") is not True and str(check.get("classification") or "executed") != "unavailable":
            result.error("candidate.readiness_debt_mask", f"unavailable debt cannot mask a failed check: {command}")
    for debt in debts:
        debt_command = str(debt.get("command") or debt.get("check") or "") if isinstance(debt, Mapping) else ""
        if not isinstance(debt, Mapping) or debt_command not in commands or not _debt_for([debt], debt_command, head):
            result.error("candidate.readiness_debt", "readiness debt must be unavailable and bound to an exact command/head with reason, owner, and futureGate")
    manager_candidate = task.get("workerRequired") is False and readiness.get("managerCandidate") is True
    worker_invocation = readiness.get("workerInvocationId")
    if not worker_invocation and not manager_candidate:
        result.error("candidate.readiness_worker", "candidate readiness must bind an accepted worker invocation")
    elif worker_invocation:
        invocation = next((item for item in as_list((task.get("execution") or {}).get("invocations"))
                           if isinstance(item, Mapping) and item.get("invocationId") == worker_invocation), None)
        if not isinstance(invocation, Mapping) or invocation.get("role") != "worker":
            result.error("candidate.readiness_worker", "candidate readiness workerInvocationId must reference a stored worker invocation")
    worker_result = _worker_result(task, str(worker_invocation) if worker_invocation else None, head)
    if worker_invocation and worker_result is None:
        result.error("candidate.readiness_worker", "candidate readiness worker result is missing or stale")
    if worker_result is not None and readiness.get("workerResultDigest") != result_digest(worker_result):
        result.error("candidate.readiness_worker", "candidate readiness worker result digest is stale")
    if readiness.get("cleanBefore") is not True or readiness.get("cleanAfter") is not True:
        result.error("candidate.readiness_tree", "candidate readiness requires clean before/after checks")
    return result


def prepare_candidate(
    repo: Path,
    task_path: Path,
    *,
    expected_revision: int,
    expected_head: str | None = None,
    debt: list[Any] | None = None,
) -> dict[str, Any]:
    """Run declared candidate checks and persist one idempotent readiness record."""
    from .invocations import task_lock  # local import avoids a module cycle

    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("revision") != expected_revision:
            raise ValueError(f"stale Task revision: expected {expected_revision}, actual {task.get('revision')}")
        head = str(expected_head or candidate_head(task) or "")
        if not head:
            raise ValueError("candidate prepare requires candidateHead")
        live = _live_candidate(repo, task, task_path, head)
        if not live.ok:
            raise ValueError(live.findings[0].message)
        resolved = git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
        if resolved.returncode:
            raise ValueError("candidateHead does not resolve to a commit")
        existing = ((task.get("execution") or {}).get("candidateReadiness"))
        if isinstance(existing, Mapping) and existing.get("candidateHead") == head and existing.get("planDigest") == plan_digest(task) and existing.get("validationDigest") == validation_digest(task):
            checked = validate_candidate_readiness(repo, task, task_path=task_path)
            if checked.ok:
                return {"ok": True, "taskId": task.get("taskId"), "revision": expected_revision, "candidateReadiness": dict(existing), "reused": True}
        ownership = validate_repository_ownership(repo, task)
        if not ownership.ok:
            raise ValueError(ownership.findings[0].message)
        invocation_id = None
        worker_result = None
        results = [item for item in as_list((task.get("execution") or {}).get("agentResults")) if isinstance(item, Mapping)]
        for item in reversed(results):
            if item.get("status") == "succeeded" and item.get("candidateHead") == head:
                worker_result = item
                invocation_id = str(item.get("invocationId") or "") or None
                break
        manager_candidate = task.get("workerRequired") is False
        if worker_result is None and not manager_candidate:
            raise ValueError("candidate prepare requires an accepted successful worker result for candidateHead")
        clean_before = _clean(repo, task_path, task)
        checks: list[dict[str, Any]] = []
        supplied_debt = list(debt or [])
        commands = [str(value).strip() for value in as_list((task.get("validation") or {}).get("commands")) if str(value).strip()]
        for command in commands:
            common_process = git(repo, "rev-parse", "--git-common-dir")
            common = Path(common_process.stdout.strip()) if not common_process.returncode and Path(common_process.stdout.strip()).is_absolute() else repo / (common_process.stdout.strip() if not common_process.returncode else ".git")
            artifact = common.resolve() / "lemmings" / "validation" / (head + "-" + hashlib.sha256(command.encode()).hexdigest()[:12] + ".log")
            artifact.parent.mkdir(parents=True, exist_ok=True)
            try:
                with artifact.open("wb") as output:
                    process = subprocess.run(command, cwd=repo, shell=True, stdout=output, stderr=subprocess.STDOUT, check=False)
                size = artifact.stat().st_size
                with artifact.open("rb") as output:
                    output.seek(max(0, size - 4096))
                    tail = output.read(4096).decode("utf-8", errors="replace")
                checks.append({"command": command, "headSha": head, "passed": process.returncode == 0, "exitCode": process.returncode,
                               "classification": "executed", "diagnostics": {"tail": tail, "totalBytes": size, "omittedBytes": max(0, size - 4096),
                               "artifact": "git-common-dir:lemmings/validation/" + artifact.name}})
            except OSError as error:
                checks.append({"command": command, "headSha": head, "passed": False, "classification": "unavailable", "errorType": type(error).__name__})
        clean_after = _clean(repo, task_path, task)
        live_after = git(repo, "rev-parse", "--verify", "HEAD^{commit}")
        live_head_after = not live_after.returncode and live_after.stdout.strip() == head
        readiness: dict[str, Any] = {
            "version": 1,
            "status": "passed" if clean_before and clean_after and live_head_after and all(
                _check_passed(item, _debt_for(supplied_debt, str(item.get("command") or ""), head))
                for item in checks
            ) else "failed",
            "candidateHead": head,
            "baseSha": task.get("baseSha"),
            "planDigest": plan_digest(task),
            "validationDigest": validation_digest(task),
            "workerInvocationId": invocation_id,
            "workerResultDigest": result_digest(worker_result) if worker_result is not None else None,
            "managerCandidate": manager_candidate,
            "checks": checks,
            "debt": supplied_debt,
            "cleanBefore": clean_before,
            "cleanAfter": clean_after,
        }
        readiness["digest"] = readiness_digest(readiness)
        task.setdefault("execution", {})["candidateReadiness"] = readiness
        chains = task.setdefault("execution", {}).get("reviewChains")
        if isinstance(chains, Mapping):
            refreshed_chains: dict[str, Any] = {}
            for lane, chain in chains.items():
                if isinstance(chain, Mapping):
                    refreshed = dict(chain)
                    refreshed["reviewSpec"] = _next_lane_spec(task, readiness, head, str(lane), chain)
                    refreshed_chains[str(lane)] = refreshed
            task["execution"]["reviewChains"] = refreshed_chains
        current_spec = task.get("reviewSpec")
        if isinstance(current_spec, Mapping):
            # Preserve an immutable delta predecessor while rebinding its next
            # candidate/readiness basis.  A missing spec remains a first full
            # lane review and is created by invocation dispatch.
            refreshed = dict(current_spec)
            refreshed.update({
                "fullBaseSha": task.get("baseSha"),
                "candidateHead": head,
                "readinessDigest": readiness["digest"],
                "planDigest": readiness["planDigest"],
                "validationDigest": readiness["validationDigest"],
            })
            task["reviewSpec"] = refreshed
        task["revision"] = expected_revision + 1
        write_object(task_path, task)
        checked = validate_candidate_readiness(repo, task, task_path=task_path)
        if not checked.ok:
            return {"ok": False, "taskId": task.get("taskId"), "revision": task["revision"], "candidateReadiness": readiness,
                    "findings": [item.as_dict() for item in checked.findings]}
        return {"ok": True, "taskId": task.get("taskId"), "revision": task["revision"], "candidateReadiness": readiness, "reused": False}
