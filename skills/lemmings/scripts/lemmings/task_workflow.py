"""Low ceremony task preparation and recoverable candidate submission.

The manager supplies a small semantic ``TaskBrief v1`` document. This module
turns it into the existing schema-v5 Task and keeps transport metadata in the
canonical Task. It intentionally does not create a second brief artifact.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .budget import new_task_budget
from .contracts import (
    SCHEMA_VERSION,
    as_list,
    git,
    plan_digest,
    read_object,
    route_name,
    validate_task,
    write_object,
)
from .invocations import (
    accept_result,
    find_invocation,
    normalize_result,
    reference_hash,
    result_findings,
    task_lock,
)
from .readiness import prepare_candidate, validation_digest


TASK_BRIEF_VERSION = 1
MODES = {"auto", "simple", "standard", "strict"}
RISK_CLASSES = {"low", "medium", "high"}
ACCOUNTING_MODES = {"host-v1", "invocation-v1"}

__all__ = ["TaskBriefError", "TASK_BRIEF_VERSION", "prepare_task", "submit_candidate"]


class TaskBriefError(ValueError):
    """A user-actionable TaskBrief or candidate submission error."""

    def __init__(self, message: str, *, kind: str = "brief") -> None:
        super().__init__(message)
        self.kind = kind

    def as_dict(self) -> dict[str, Any]:
        actions = {
            "brief": "Correct the TaskBrief and run task prepare again; an existing Task is never overwritten.",
            "artifact": "Correct the report and resubmit the same invocation; do not rerun the worker.",
            "evidence": "Refresh only the named evidence or validation check; keep the saved invocation binding.",
            "readiness": "Inspect the stored candidateReadiness and fix the named check before reviewer dispatch.",
            "conflict": "Read the saved submission result and resume it; do not create a second invocation.",
        }
        return {"ok": False, "kind": self.kind, "error": str(self), "nextAction": actions.get(self.kind, actions["brief"])}


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskBriefError(f"TaskBrief {name} must be a non-empty string")
    return value.strip()


def _string_array(value: Any, name: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise TaskBriefError(f"TaskBrief {name} must be an array of non-empty strings")
    if not allow_empty and not value:
        raise TaskBriefError(f"TaskBrief {name} must not be empty")
    return [item.strip() for item in value]


def _route(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise TaskBriefError(f"TaskBrief {name} must contain a model assignment")
        if "::" in text:
            host, model = text.split("::", 1)
        else:
            host, model = "native", text
        if "/" in model:
            provider, model_id = model.split("/", 1)
        else:
            provider, model_id = "native", model
        return {"hostId": host, "providerId": provider, "modelId": model_id}
    if not isinstance(value, Mapping):
        raise TaskBriefError(f"TaskBrief {name} must be a route object or identity string")
    route = {
        "hostId": _nonempty(value.get("hostId"), f"{name}.hostId"),
        "providerId": _nonempty(value.get("providerId"), f"{name}.providerId"),
        "modelId": _nonempty(value.get("modelId"), f"{name}.modelId"),
    }
    if value.get("variantId") not in (None, ""):
        route["variantId"] = _nonempty(value.get("variantId"), f"{name}.variantId")
    return route


def _risk_key(value: Any, index: int) -> str:
    if isinstance(value, str):
        return _nonempty(value, f"risks[{index}]")
    if isinstance(value, Mapping):
        return _nonempty(value.get("risk") or value.get("id") or value.get("name"), f"risks[{index}].risk")
    raise TaskBriefError(f"TaskBrief risks[{index}] must be a string or object with risk/id/name")


def _decisions(brief: Mapping[str, Any]) -> Mapping[str, Any]:
    value = brief.get("managerDecision")
    if value is None:
        value = brief.get("decisions")
    if not isinstance(value, Mapping):
        raise TaskBriefError("TaskBrief managerDecision is required and must be an object")
    return value


def _assignment_block(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("roleAssignments", "modelAssignment", "assignments"):
        value = decision.get(key)
        if isinstance(value, Mapping):
            return value
    value = decision.get("models")
    if isinstance(value, Mapping):
        return value
    raise TaskBriefError("TaskBrief managerDecision requires role/model assignments")


def _owner_assignment(assignments: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    owner_value = assignments.get("worker")
    if owner_value is None and "assigned" in assignments:
        owner_value = assignments
    if owner_value is None:
        raise TaskBriefError("TaskBrief managerDecision requires a worker model assignment")
    owner = _route(owner_value, "managerDecision.worker")
    reviewer_value = assignments.get("reviewer")
    reviewer = _route(reviewer_value, "managerDecision.reviewer") if reviewer_value is not None else None
    explorer_value = assignments.get("explorer")
    explorer = _route(explorer_value, "managerDecision.explorer") if explorer_value is not None else None
    return owner, reviewer, explorer


def _validate_brief(brief: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not isinstance(brief, Mapping):
        raise TaskBriefError("TaskBrief must be a JSON object")
    if brief.get("schemaVersion", brief.get("briefVersion")) != TASK_BRIEF_VERSION:
        raise TaskBriefError("TaskBrief schemaVersion must be 1")
    semantic: dict[str, Any] = {
        "taskId": _nonempty(brief.get("taskId"), "taskId"),
        "goal": _nonempty(brief.get("goal"), "goal"),
        "acceptance": _string_array(brief.get("acceptance"), "acceptance", allow_empty=False),
        "dependencies": _string_array(brief.get("dependencies", []), "dependencies"),
    }
    risks_value = brief.get("risks")
    if not isinstance(risks_value, list):
        raise TaskBriefError("TaskBrief risks must be an array")
    risk_keys = [_risk_key(item, index) for index, item in enumerate(risks_value)]
    if len(set(risk_keys)) != len(risk_keys):
        raise TaskBriefError("TaskBrief risks must have unique risk identifiers")
    semantic["risks"] = copy.deepcopy(risks_value)
    validation = brief.get("validation")
    if not isinstance(validation, Mapping):
        raise TaskBriefError("TaskBrief validation is required and must be an object")
    commands = _string_array(validation.get("commands"), "validation.commands")
    allowed_outputs = _string_array(validation.get("allowedOutputs"), "validation.allowedOutputs")
    risk_to_test = validation.get("riskToTest", brief.get("riskToTest"))
    if not isinstance(risk_to_test, list):
        raise TaskBriefError("TaskBrief validation.riskToTest is required")
    normalized_risk_to_test: list[dict[str, str]] = []
    seen_risks: set[str] = set()
    for index, item in enumerate(risk_to_test):
        if not isinstance(item, Mapping):
            raise TaskBriefError(f"TaskBrief validation.riskToTest[{index}] must be an object")
        risk = _nonempty(item.get("risk"), f"validation.riskToTest[{index}].risk")
        test = _nonempty(item.get("test") or item.get("command"), f"validation.riskToTest[{index}].test")
        if risk in seen_risks:
            raise TaskBriefError(f"TaskBrief validation.riskToTest duplicates risk: {risk}")
        seen_risks.add(risk)
        normalized_risk_to_test.append({"risk": risk, "test": test})
    if set(risk_keys) != seen_risks:
        raise TaskBriefError("TaskBrief riskToTest must exactly cover risks")
    semantic["validation"] = {"riskToTest": normalized_risk_to_test, "commands": commands, "allowedOutputs": allowed_outputs}
    ownership = brief.get("ownership")
    if not isinstance(ownership, Mapping):
        raise TaskBriefError("TaskBrief ownership is required and must be an object")
    semantic["ownership"] = {name: _string_array(ownership.get(name), f"ownership.{name}") for name in ("owned", "shared", "forbidden")}
    working_set = brief.get("workingSet")
    if not isinstance(working_set, list):
        raise TaskBriefError("TaskBrief workingSet is required and must be an array")
    semantic["workingSet"] = []
    for index, item in enumerate(working_set):
        if not isinstance(item, Mapping):
            raise TaskBriefError(f"TaskBrief workingSet[{index}] must be an object")
        entry = {"ref": _nonempty(item.get("ref"), f"workingSet[{index}].ref"), "purpose": _nonempty(item.get("purpose"), f"workingSet[{index}].purpose")}
        if item.get("contentHash"):
            entry["contentHash"] = _nonempty(item.get("contentHash"), f"workingSet[{index}].contentHash")
        semantic["workingSet"].append(entry)
    decision = _decisions(brief)
    required = ("requestedMode", "resolvedMode", "riskClass", "modeReasons", "workerRequired", "reviewRequired", "planReviewRequired", "workspace")
    for name in required:
        if name not in decision:
            raise TaskBriefError(f"TaskBrief managerDecision.{name} is required")
    requested = _nonempty(decision.get("requestedMode"), "managerDecision.requestedMode").lower()
    resolved = _nonempty(decision.get("resolvedMode"), "managerDecision.resolvedMode").lower()
    if requested not in MODES or resolved not in {"simple", "standard", "strict"}:
        raise TaskBriefError("TaskBrief managerDecision mode must be auto/simple/standard/strict")
    if requested != "auto" and requested != resolved:
        raise TaskBriefError("TaskBrief explicit requestedMode must equal resolvedMode")
    risk_class = _nonempty(decision.get("riskClass"), "managerDecision.riskClass").lower()
    if risk_class not in RISK_CLASSES:
        raise TaskBriefError("TaskBrief managerDecision.riskClass must be low, medium, or high")
    mode_reasons = _string_array(decision.get("modeReasons"), "managerDecision.modeReasons")
    for name in ("workerRequired", "reviewRequired", "planReviewRequired"):
        if not isinstance(decision.get(name), bool):
            raise TaskBriefError(f"TaskBrief managerDecision.{name} must be boolean")
    workspace = decision.get("workspace")
    if not isinstance(workspace, Mapping):
        raise TaskBriefError("TaskBrief managerDecision.workspace must be an object")
    for name in ("policy", "backend", "reason"):
        if name not in workspace:
            raise TaskBriefError(f"TaskBrief managerDecision.workspace.{name} is required")
    workspace_value = {name: _nonempty(workspace.get(name), f"managerDecision.workspace.{name}") for name in ("policy", "backend", "reason")}
    for name in ("workspaceId", "path", "managedBy", "lifetime", "approval", "estimatedGiB"):
        if name in workspace:
            workspace_value[name] = copy.deepcopy(workspace[name])
    assignments = _assignment_block(decision)
    owner, reviewer, explorer = _owner_assignment(assignments)
    review_policy = decision.get("reviewPolicy")
    if review_policy not in (None, "single", "cross"):
        raise TaskBriefError("TaskBrief managerDecision.reviewPolicy must be null, single, or cross")
    if decision.get("reviewRequired") and review_policy is None:
        review_policy = "single"
    reviewer_recovery = (_route(decision.get("reviewerRecovery"), "managerDecision.reviewerRecovery")
                         if decision.get("reviewerRecovery") is not None else None)
    accounting = decision.get("accountingMode", "invocation-v1")
    if accounting not in ACCOUNTING_MODES:
        raise TaskBriefError("TaskBrief managerDecision.accountingMode must be host-v1 or invocation-v1")
    raw_capabilities = decision.get("hostCapabilities", {})
    if not isinstance(raw_capabilities, Mapping):
        raise TaskBriefError("TaskBrief managerDecision.hostCapabilities must be an object keyed by hostId")
    capabilities: dict[str, dict[str, Any]] = {}
    for host_id, capability in raw_capabilities.items():
        if not isinstance(host_id, str) or not host_id.strip() or not isinstance(capability, Mapping):
            raise TaskBriefError("TaskBrief managerDecision.hostCapabilities entries require a hostId and object")
        capabilities[host_id.strip()] = copy.deepcopy(dict(capability))
    if accounting == "host-v1":
        executing = [owner]
        if decision.get("reviewRequired") or decision.get("planReviewRequired"):
            if reviewer is None:
                raise TaskBriefError("host-v1 review requires an explicit reviewer route")
            executing.append(reviewer)
        if review_policy == "cross":
            if reviewer_recovery is None:
                raise TaskBriefError("cross review requires a distinct reviewerRecovery route")
            executing.append(reviewer_recovery)
        unsupported = sorted({str(route.get("hostId")) for route in executing
                              if (capabilities.get(str(route.get("hostId"))) or {}).get("usageAccounting") is not True})
        if unsupported:
            raise TaskBriefError("host-v1 requires trusted usageAccounting capability for every executing host: " + ", ".join(unsupported))
    capability_snapshot = {"hosts": capabilities}
    capability_snapshot["digest"] = hashlib.sha256(json.dumps(capability_snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    decisions = {
        "requestedMode": requested, "resolvedMode": resolved, "riskClass": risk_class, "modeReasons": mode_reasons,
        "workerRequired": decision["workerRequired"], "reviewRequired": decision["reviewRequired"], "planReviewRequired": decision["planReviewRequired"],
        "reviewPolicy": review_policy, "workspace": workspace_value, "owner": owner, "reviewer": reviewer, "explorer": explorer,
        "accountingMode": accounting, "hostCapabilities": capability_snapshot,
    }
    if decision.get("planReviewRef") is not None:
        decisions["planReviewRef"] = _nonempty(decision.get("planReviewRef"), "managerDecision.planReviewRef")
    if reviewer_recovery is not None:
        decisions["reviewerRecovery"] = reviewer_recovery
    decisions["writerCount"] = decision.get("writerCount", 1)
    decisions["ownershipDomainCount"] = decision.get("ownershipDomainCount", 1)
    return semantic, decisions, {"worker": owner, "reviewer": reviewer, "explorer": explorer}


def _repo_head(repo: Path) -> str:
    process = git(repo, "rev-parse", "--verify", "HEAD^{commit}")
    if process.returncode:
        raise TaskBriefError(process.stderr.strip() or "workspace must be a Git repository")
    return process.stdout.strip()


def _workspace_relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as error:
        raise TaskBriefError("Task path must be inside the repository") from error


def prepare_task(repo: Path, task_path: Path, brief: Mapping[str, Any], *, profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create one canonical Ready Task from a validated TaskBrief v1."""
    semantic, decision, assignments = _validate_brief(brief)
    repo = repo.resolve()
    task_path = task_path.resolve()
    relative_task = _workspace_relative(repo, task_path)
    if task_path.exists():
        raise TaskBriefError(f"Task already exists and will not be overwritten: {relative_task}", kind="conflict")
    base_sha = _repo_head(repo)
    working_set: list[dict[str, Any]] = []
    for entry in semantic["workingSet"]:
        item = dict(entry)
        item["contentHash"] = reference_hash(repo, item)
        working_set.append(item)
    owner = assignments["worker"]
    reviewer = assignments.get("reviewer")
    explorer = assignments.get("explorer") or {"hostId": "native", "providerId": "native", "modelId": "current-host/default"}
    owner_identity = "current-host/default" if owner.get("modelId") == "current-host/default" else (route_name(owner) or "current-host/default")
    owner_host = owner.get("hostId") or "native"
    workspace = dict(decision["workspace"])
    workspace.setdefault("workspaceId", None)
    workspace.setdefault("managedBy", "external")
    workspace.setdefault("lifetime", "external")
    workspace.setdefault("estimatedGiB", 0)
    workspace.setdefault("approval", "not-required")
    task: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION, "revision": 0, "taskId": semantic["taskId"], "goal": semantic["goal"],
        "acceptance": semantic["acceptance"], "dependencies": semantic["dependencies"],
        "requestedMode": decision["requestedMode"], "resolvedMode": decision["resolvedMode"], "modeFloor": decision["resolvedMode"],
        "riskClass": decision["riskClass"], "modeReasons": decision["modeReasons"], "capabilityDegradations": [],
        "writerCount": int(decision["writerCount"]), "ownershipDomainCount": int(decision["ownershipDomainCount"]),
        "workerRequired": decision["workerRequired"], "parallelReason": None, "reviewRequired": decision["reviewRequired"],
        "planReviewRequired": decision["planReviewRequired"], "planReviewRef": decision.get("planReviewRef"), "reviewPolicy": decision["reviewPolicy"],
        "specialization": None, "state": "Ready", "previousState": "Draft", "role": "worker", "risks": semantic["risks"], "telemetryCohort": None,
        "ownership": semantic["ownership"], "resources": {"exclusive": []}, "workingSet": working_set,
        "models": {"hostId": owner_host, "requested": None, "assigned": owner_identity, "actual": None, "fallbackReason": None},
        "workspace": workspace,
        "execution": {"interfaces": [], "tests": [], "dependencyHandoffs": [], "handoff": None, "validationEvidence": [], "attempts": [], "invocations": [], "agentResults": [], "repairHistory": [], "reviewApplications": [], "reviewChains": {}, "candidateReadiness": None, "activeRepair": None, "repairDecision": None, "fullReviewRequired": False},
        "baseSha": base_sha, "commits": {"candidate": None, "fix": []},
        "validation": {**semantic["validation"], "debt": []}, "reviewRef": None, "crossReviewRefs": [], "reviewHistory": [],
        "close": {"mergeCommit": None, "integrationEvidence": [], "workspaceDisposition": None},
        "budget": new_task_budget(profile or {}, accounting_mode=decision["accountingMode"]),
        "accountingCapabilities": copy.deepcopy(decision["hostCapabilities"]),
        "owner": {"kind": "task", "id": semantic["taskId"], "revision": 0},
        "reviewBindings": [],
    }
    if reviewer is not None or decision.get("reviewerRecovery") is not None:
        task["roleAssignments"] = {"worker": owner, "reviewer": reviewer, "explorer": explorer}
        if decision.get("reviewerRecovery") is not None:
            task["reviewerRecovery"] = decision["reviewerRecovery"]
    checked = validate_task(task, profile)
    if not checked.ok:
        raise TaskBriefError(checked.findings[0].message)
    task_path.parent.mkdir(parents=True, exist_ok=True)
    with task_lock(task_path):
        if task_path.exists():
            raise TaskBriefError(f"Task already exists and will not be overwritten: {relative_task}", kind="conflict")
        write_object(task_path, task)
    return {"ok": True, "taskId": task["taskId"], "revision": 0, "state": task["state"], "path": relative_task}


def _stored_result(task: Mapping[str, Any], invocation_id: str) -> Mapping[str, Any] | None:
    values = [item for item in as_list((task.get("execution") or {}).get("agentResults")) if isinstance(item, Mapping) and item.get("invocationId") == invocation_id]
    return values[0] if len(values) == 1 else None


def _readiness_failure(task: Mapping[str, Any], head: str, message: str) -> dict[str, Any]:
    value: dict[str, Any] = {"version": 1, "status": "failed", "candidateHead": head, "baseSha": task.get("baseSha"), "planDigest": plan_digest(task), "validationDigest": validation_digest(task), "workerInvocationId": None, "workerResultDigest": None, "checks": [], "debt": [], "cleanBefore": False, "cleanAfter": False, "findings": [{"code": "candidate.readiness", "message": message}]}
    value["digest"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return value


def submit_candidate(repo: Path, task_path: Path, profile: Mapping[str, Any], report: Mapping[str, Any], *, invocation_id: str, host_receipt: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Accept, promote and prepare one worker result, resuming safe stages."""
    repo = repo.resolve()
    task_path = task_path.resolve()
    with task_lock(task_path):
        task = read_object(task_path)
        invocation = find_invocation(task, invocation_id)
        if not invocation or invocation.get("role") != "worker":
            raise TaskBriefError("candidate submit requires a saved worker invocation", kind="artifact")
        if report.get("invocationId") not in (None, invocation_id):
            raise TaskBriefError("reported invocationId differs from --invocation-id", kind="artifact")
        report_value = {**dict(report), "invocationId": invocation_id}
        if report_value.get("status") == "succeeded":
            actual_head = _repo_head(repo)
            if report.get("candidateHead") not in (None, actual_head):
                raise TaskBriefError("successful candidateHead must equal the exact repository HEAD", kind="evidence")
            report_value["candidateHead"] = actual_head
            if not isinstance(report.get("acceptanceEvidence"), list) or not report.get("acceptanceEvidence"):
                raise TaskBriefError("successful worker result requires non-empty acceptanceEvidence", kind="artifact")
            if not isinstance(report.get("validationEvidence"), list):
                raise TaskBriefError("successful worker result requires validationEvidence", kind="artifact")
        normalized = normalize_result(repo, invocation, report_value)
        if _stored_result(task, invocation_id) is None:
            checked = result_findings(repo, task, profile, normalized)
            if not checked.ok:
                raise TaskBriefError(checked.findings[0].message, kind="evidence")
        accepted = accept_result(repo, task_path, profile, normalized, int(task.get("revision", 0)), trusted_usage=host_receipt, invocation_id=invocation_id)
        task = read_object(task_path)
        stored = _stored_result(task, invocation_id)
        if stored is None:
            raise TaskBriefError("accepted result was not persisted", kind="conflict")
        if stored.get("status") != "succeeded":
            return {"ok": True, "status": stored.get("status"), "taskId": task.get("taskId"), "revision": task.get("revision"), "invocationId": invocation_id, "nextAction": "Inspect the saved worker failure; no candidate was promoted."}
        head = str(stored.get("candidateHead") or "")
        if not head:
            raise TaskBriefError("successful worker result has no candidateHead", kind="artifact")
        task = read_object(task_path)
        execution = task.setdefault("execution", {})
        prior_submission = execution.get("candidateSubmission")
        if (isinstance(prior_submission, Mapping) and prior_submission.get("promoted") is True
                and prior_submission.get("invocationId") == invocation_id
                and prior_submission.get("candidateHead") == head):
            try:
                readiness = prepare_candidate(repo, task_path, expected_revision=int(task.get("revision", 0)), expected_head=head)
            except (OSError, ValueError) as error:
                raise TaskBriefError(str(error), kind="readiness") from error
            final = read_object(task_path)
            return {"ok": bool(readiness.get("ok")), "status": "Candidate", "taskId": final.get("taskId"),
                    "revision": final.get("revision"), "invocationId": invocation_id, "candidateHead": head,
                    "reused": True, "candidateReadiness": readiness.get("candidateReadiness"),
                    "findings": readiness.get("findings", []),
                    "nextAction": "Run review start for the saved candidate." if readiness.get("ok") else "Inspect candidateReadiness and fix the named validation check."}
        before_state = task.get("state")
        active = execution.get("activeRepair") if isinstance(execution.get("activeRepair"), Mapping) else None
        fix_mode = before_state == "Repair" or (isinstance(active, Mapping) and active.get("status") == "open")
        commits = task.setdefault("commits", {"candidate": None, "fix": []})
        if fix_mode:
            fixes = commits.setdefault("fix", [])
            if head not in fixes:
                fixes.append(head)
        elif commits.get("candidate") != head:
            commits["candidate"] = head
        models = task.setdefault("models", {})
        models["actual"] = invocation.get("assignedModel") or models.get("assigned") or "current-host/default"
        if invocation.get("assignedHost"):
            models["hostId"] = invocation["assignedHost"]
        execution["validationEvidence"] = copy.deepcopy(stored.get("validationEvidence") or [])
        submission = execution.setdefault("candidateSubmission", {})
        submission.update({"invocationId": invocation_id, "candidateHead": head, "promoted": True, "mode": "repair" if fix_mode else "initial"})
        if task.get("state") != "Candidate":
            task["previousState"] = task.get("state")
            task["state"] = "Candidate"
        task["revision"] = int(task.get("revision", 0)) + 1
        write_object(task_path, task)
        promoted_revision = task["revision"]
        try:
            readiness = prepare_candidate(repo, task_path, expected_revision=promoted_revision, expected_head=head)
        except (OSError, ValueError) as error:
            current = read_object(task_path)
            current.setdefault("execution", {})["candidateReadiness"] = _readiness_failure(current, head, str(error))
            current["revision"] = int(current.get("revision", promoted_revision)) + 1
            write_object(task_path, current)
            return {"ok": False, "kind": "readiness", "taskId": current.get("taskId"), "revision": current["revision"], "invocationId": invocation_id, "candidateHead": head, "candidateReadiness": current["execution"]["candidateReadiness"], "nextAction": "Inspect candidateReadiness and fix the named validation or tree check."}
        final = read_object(task_path)
        if not readiness.get("ok"):
            return {"ok": False, "kind": "readiness", "taskId": final.get("taskId"), "revision": final.get("revision"), "invocationId": invocation_id, "candidateHead": head, "candidateReadiness": readiness.get("candidateReadiness"), "findings": readiness.get("findings", []), "nextAction": "Inspect candidateReadiness and fix the named validation check."}
        return {"ok": True, "status": "Candidate", "taskId": final.get("taskId"), "revision": final.get("revision"), "invocationId": invocation_id, "candidateHead": head, "reused": bool(accepted.get("reused")), "candidateReadiness": readiness.get("candidateReadiness"), "nextAction": "Run review start for the saved candidate."}
