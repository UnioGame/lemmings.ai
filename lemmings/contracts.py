"""Canonical schema-v1 orchestration contracts for Lemmings."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
STAGES = ("Prepare", "Dispatch", "Execute/Candidate", "Review/Repair", "Integrate/Close")
MODES = {"auto", "simple", "standard", "strict"}
TASK_STATES = {
    "Planned", "Ready", "Active", "Candidate", "Accepted", "Integrated",
    "Blocked", "Replan Required", "Cancelled", "Superseded",
}
TERMINAL_STATES = {"Integrated", "Replan Required", "Cancelled", "Superseded"}
TRANSITIONS = {
    "Planned": {"Ready", "Blocked", "Cancelled", "Superseded"},
    "Ready": {"Active", "Blocked", "Cancelled", "Superseded"},
    "Active": {"Candidate", "Blocked", "Cancelled", "Replan Required"},
    "Candidate": {"Candidate", "Accepted", "Blocked", "Cancelled", "Replan Required"},
    "Accepted": {"Integrated", "Replan Required"},
    "Blocked": {"Ready", "Active", "Cancelled", "Replan Required"},
    "Integrated": set(), "Replan Required": set(), "Cancelled": set(), "Superseded": set(),
}
REVIEW_STATES = {"Pending", "ChangesRequested", "Accepted"}
STRICT_RISKS = {
    "parallelWriters", "sharedContracts", "unitySerializedAssets", "submodules",
    "codegen", "externalResources",
}
DEFAULT_MODELS = {
    "orchestrator": "gpt-5.6-sol:high",
    "reviewer": "gpt-5.6-sol:high",
    "worker": "gpt-5.6-luna:max",
    "validator": "gpt-5.6-luna:high",
    "explorer": "gpt-5.6-luna:high",
    "summarizer": "gpt-5.6-luna:medium",
}
DEFAULT_WORKER_POLICY = {
    "elevatedModel": "gpt-5.6-terra:max",
}
LEGACY_WORKER_ASSIGNMENTS = {"gpt-5.6-sol:medium"}
FINDING_ORIGINS = {"implementation", "plan-contract", "validation", "integration"}
FINDING_PRIORITIES = {"P0", "P1", "P2", "P3"}
ORCHESTRATOR_EFFORTS = {"high", "xhigh", "max", "ultra"}
TASK_ROLES = {"orchestrator", "worker", "reviewer", "validator", "explorer", "summarizer", "shared-contract-owner"}


@dataclass
class Finding:
    code: str
    message: str
    path: str | None = None
    severity: str = "error"

    def as_dict(self) -> dict[str, Any]:
        value = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.path:
            value["path"] = self.path
        return value


@dataclass
class ValidationResult:
    findings: list[Finding] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def error(self, code: str, message: str, path: str | None = None) -> None:
        self.findings.append(Finding(code, message, path))

    def warn(self, code: str, message: str, path: str | None = None) -> None:
        self.findings.append(Finding(code, message, path, "warning"))

    def extend(self, other: "ValidationResult") -> None:
        self.findings.extend(other.findings)
        self.data.update(other.data)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": sum(f.severity == "error" for f in self.findings),
            "warnings": sum(f.severity == "warning" for f in self.findings),
            "findings": [f.as_dict() for f in self.findings],
            "data": self.data,
        }


def read_object(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    value = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{target} must contain a JSON object")
    return value


def write_object(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_path(value: str) -> str:
    value = value.replace("\\", "/").strip().strip("/")
    return value.casefold() if os.name == "nt" else value


def paths_overlap(left: str, right: str) -> bool:
    a, b = normalize_path(left), normalize_path(right)
    if not a or not b:
        return False
    a = re.split(r"[\*\?\[]", a, maxsplit=1)[0].rstrip("/")
    b = re.split(r"[\*\?\[]", b, maxsplit=1)[0].rstrip("/")
    return not a or not b or a == b or a.startswith(b + "/") or b.startswith(a + "/")


def path_matches(path: str, rule: str) -> bool:
    value = normalize_path(path)
    pattern = normalize_path(rule)
    if not value or not pattern:
        return False
    if not any(character in pattern for character in "*?"):
        return value == pattern or value.startswith(pattern.rstrip("/") + "/")
    expression: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    expression.append("(?:.*/)?")
                    index += 1
                else:
                    expression.append(".*")
                continue
            expression.append("[^/]*")
        elif character == "?":
            expression.append("[^/]")
        else:
            expression.append(re.escape(character))
        index += 1
    expression.append("$")
    return re.fullmatch("".join(expression), value) is not None


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def git_common_dir(repo: Path) -> Path:
    process = git(repo, "rev-parse", "--git-common-dir")
    if process.returncode:
        raise ValueError(process.stderr.strip() or f"not a Git repository: {repo}")
    value = Path(process.stdout.strip())
    return (repo / value).resolve() if not value.is_absolute() else value.resolve()


def runtime_marker(repo: Path) -> Path:
    return git_common_dir(repo) / "lemmings" / "active.json"


def resolve_path(repo: Path, value: str | Path | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def detect_mode(
    profile: Mapping[str, Any] | None = None,
    task: Mapping[str, Any] | None = None,
    phase: Mapping[str, Any] | None = None,
) -> str:
    profile = profile or {}
    requested = str((task or {}).get("mode") or profile.get("mode") or "auto").lower()
    if requested in {"simple", "standard", "strict"}:
        return requested
    risks = set(as_list((task or {}).get("risks"))) | set(as_list(profile.get("risks")))
    if phase or risks.intersection(STRICT_RISKS):
        return "strict"
    return "standard" if task else "simple"


def validate_profile(profile: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if profile.get("schemaVersion") != SCHEMA_VERSION:
        result.error("profile.schema", "schemaVersion must be 1")
    mode = str(profile.get("mode", "auto")).lower()
    if mode not in MODES:
        result.error("profile.mode", f"mode must be one of {sorted(MODES)}")
    models = profile.get("models", {})
    if not isinstance(models, dict):
        result.error("profile.models", "models must be an object")
    else:
        if "complex-worker" in models:
            result.error("profile.legacy_role", "complex-worker is not valid in schema version 1; use worker")
        for role, required in DEFAULT_MODELS.items():
            if models.get(role, required) != required:
                result.error("model.fixed", f"{role} must use {required}")
    worker_policy = profile.get("workerPolicy", DEFAULT_WORKER_POLICY)
    if not isinstance(worker_policy, Mapping):
        result.error("profile.worker_policy", "workerPolicy must be an object")
    else:
        for route, required in DEFAULT_WORKER_POLICY.items():
            if worker_policy.get(route, required) != required:
                result.error(
                    "model.worker_route_fixed",
                    f"workerPolicy.{route} must use {required}",
                )
    fallback = profile.get("fallback", {})
    if fallback and not isinstance(fallback, dict):
        result.error("profile.fallback", "fallback must be an object")
    for field_name in ("requestedModels", "taskModels"):
        value = profile.get(field_name, {})
        if value and not isinstance(value, dict):
            result.error("profile.model_pins", f"{field_name} must be an object")
    requested_models = profile.get("requestedModels") or {}
    if isinstance(requested_models, dict):
        for role, model in requested_models.items():
            violation = model_pin_violation(str(role), str(model))
            if violation:
                result.error("model.pin_policy", violation)
    task_models = profile.get("taskModels") or {}
    if isinstance(task_models, dict):
        for task_id, pins in task_models.items():
            if not isinstance(pins, dict):
                result.error("profile.task_models", f"taskModels.{task_id} must be an object")
                continue
            for role, model in pins.items():
                violation = model_pin_violation(str(role), str(model))
                if violation:
                    result.error("model.pin_policy", f"taskModels.{task_id}: {violation}")
    tooling = profile.get("tooling") or {}
    if tooling:
        if not isinstance(tooling, Mapping) or not tooling.get("root"):
            result.error("profile.tooling", "tooling must contain repo-relative root")
        elif Path(str(tooling["root"])).is_absolute():
            result.error("profile.tooling", "profile tooling.root must be repo-relative; use the Git-common environment file for local absolute paths")
    game = profile.get("game") or {}
    if game:
        workspace = game.get("workspace") if isinstance(game, Mapping) else None
        if not isinstance(workspace, Mapping):
            result.error("profile.game", "game.workspace must be an object")
        elif workspace.get("largeThresholdGiB", 10) != 10:
            result.error("profile.workspace_threshold", "large workspace approval threshold is fixed at 10 GiB")
    return result


def model_pin_violation(role: str, model: str) -> str | None:
    if role == "reviewer" and model != DEFAULT_MODELS["reviewer"]:
        return f"reviewer must remain {DEFAULT_MODELS['reviewer']}"
    if role == "orchestrator":
        name, separator, effort = model.rpartition(":")
        if not separator or name != "gpt-5.6-sol" or effort not in ORCHESTRATOR_EFFORTS:
            return "orchestrator pin must use gpt-5.6-sol at high or higher effort"
    return None


def validate_models(task: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> ValidationResult:
    result = ValidationResult()
    models = task.get("models") or {}
    if not isinstance(models, dict):
        result.error("model.shape", "models must be an object")
        return result
    requested, assigned, actual = (models.get(name) for name in ("requested", "assigned", "actual"))
    if not assigned:
        result.error("model.assigned", "models.assigned is required before dispatch")
    role = str(task.get("role", "worker"))
    if role not in TASK_ROLES:
        result.error("task.role", f"unsupported task role: {role}")
    task_id = str(task.get("taskId", ""))
    profile = profile or {}
    task_pins = (profile.get("taskModels") or {}).get(task_id, {})
    task_pin = task_pins.get(role) if isinstance(task_pins, dict) else None
    global_pin = (profile.get("requestedModels") or {}).get(role)
    effective_pin = task_pin or global_pin
    if effective_pin:
        if requested != effective_pin:
            result.error("model.pin_requested", f"models.requested must equal effective pin {effective_pin}")
        if assigned != effective_pin:
            result.error("model.pin_assigned", f"models.assigned must equal effective pin {effective_pin}")
    elif requested and assigned != requested:
        result.error("model.pin", "models.requested must take priority over assignment")
    pin_violation = model_pin_violation(role, str(requested)) if requested else None
    if pin_violation:
        result.error("model.pin_policy", pin_violation)
    default_assignment = (profile.get("models") or {}).get(role) or DEFAULT_MODELS.get(role)
    allowed_assignments = {default_assignment} if default_assignment else set()
    if role == "worker":
        worker_policy = profile.get("workerPolicy") or DEFAULT_WORKER_POLICY
        if isinstance(worker_policy, Mapping):
            for route, required in DEFAULT_WORKER_POLICY.items():
                allowed_assignments.add(worker_policy.get(route) or required)
        # Historical unpinned Sol Medium packets remain readable, but this model
        # is no longer selected by automatic routing.
        allowed_assignments.update(LEGACY_WORKER_ASSIGNMENTS)
    if not effective_pin and not requested and allowed_assignments and assigned not in allowed_assignments:
        expected = " or ".join(sorted(str(value) for value in allowed_assignments))
        result.error("model.default_assignment", f"models.assigned must equal an approved role assignment: {expected}")
    if actual and actual != assigned:
        fallback_reason = models.get("fallbackReason")
        allowed = as_list(profile.get("fallback", {}).get("allowed"))
        if actual not in allowed:
            result.error("model.actual", "models.actual differs from assigned and is not an allowed fallback")
        if not fallback_reason:
            result.error("model.fallback_reason", "fallback requires models.fallbackReason")
    return result


def validate_debt(task: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    for index, debt in enumerate(as_list((task.get("validation") or {}).get("debt"))):
        if not isinstance(debt, dict) or not all(debt.get(k) not in (None, "") for k in ("reason", "owner", "futureGate", "blocking")):
            result.error("validation.debt", f"validation.debt[{index}] requires reason, owner, futureGate, blocking")
    return result


def candidate_head(task: Mapping[str, Any]) -> str | None:
    commits = task.get("commits") or {}
    fixes = as_list(commits.get("fix")) if isinstance(commits, dict) else []
    return str(fixes[-1]) if fixes else (str(commits.get("candidate")) if commits.get("candidate") else None)


def task_worktree(task: Mapping[str, Any]) -> str | None:
    """Return the isolated workspace path declared by the canonical task contract."""
    workspace = task.get("workspace") or {}
    value = workspace.get("path") if isinstance(workspace, Mapping) else None
    return str(value) if value else None


def validate_task(task: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> ValidationResult:
    result = ValidationResult()
    if task.get("schemaVersion") != SCHEMA_VERSION:
        result.error("task.schema", "schemaVersion must be 1")
    for name in ("taskId", "goal", "acceptance", "state", "ownership", "models", "workspace", "validation"):
        if name not in task or task.get(name) is None:
            result.error("task.missing", f"missing task field: {name}")
    state = str(task.get("state", ""))
    if state not in TASK_STATES:
        result.error("state.unknown", f"unknown task state: {state}")
    if state not in {"Planned", "Cancelled", "Superseded"}:
        if not isinstance(task.get("goal"), str) or not str(task.get("goal")).strip():
            result.error("task.goal", f"{state} requires a non-empty goal")
        acceptance = task.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            result.error("task.acceptance", f"{state} requires at least one acceptance criterion")
    previous = task.get("previousState")
    if previous and state not in TRANSITIONS.get(str(previous), set()) and state != previous:
        result.error("state.transition", f"illegal transition: {previous} -> {state}")
    cohort = task.get("telemetryCohort")
    if cohort is not None and (not isinstance(cohort, str) or not cohort.strip()):
        result.error("telemetry.cohort", "telemetryCohort must be null or a non-empty string")
    result.extend(validate_models(task, profile))
    result.extend(validate_debt(task))
    execution = task.get("execution") or {}
    attempts = execution.get("attempts") if isinstance(execution, Mapping) else None
    if attempts is not None:
        if not isinstance(attempts, list):
            result.error("quality.attempts", "execution.attempts must be an array")
        else:
            for index, attempt in enumerate(attempts, 1):
                if not isinstance(attempt, Mapping):
                    result.error("quality.attempt", f"execution.attempts[{index - 1}] must be an object")
                    continue
                if attempt.get("attempt") != index:
                    result.error("quality.attempt_number", f"execution.attempts[{index - 1}].attempt must be {index}")
                if attempt.get("kind") not in {"candidate", "fix"}:
                    result.error("quality.attempt_kind", f"execution.attempts[{index - 1}].kind must be candidate or fix")
                for field_name in ("actualModel", "headSha"):
                    if not attempt.get(field_name):
                        result.error("quality.attempt_field", f"execution.attempts[{index - 1}].{field_name} is required")
                failures = attempt.get("validationFailures")
                if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
                    result.error("quality.validation_failures", f"execution.attempts[{index - 1}].validationFailures must be a non-negative integer")
                review_status = attempt.get("reviewStatus")
                if review_status is not None and review_status not in REVIEW_STATES:
                    result.error("quality.review_status", f"execution.attempts[{index - 1}].reviewStatus is invalid")
    review_history = task.get("reviewHistory")
    if review_history is not None:
        if not isinstance(review_history, list) or any(not isinstance(value, str) or not value.strip() for value in review_history):
            result.error("quality.review_history", "reviewHistory must be an array of non-empty review references")
        elif len(review_history) != len(set(review_history)):
            result.error("quality.review_history_duplicate", "reviewHistory must not contain duplicate references")
    if isinstance(attempts, list) and isinstance(review_history, list):
        for index, attempt in enumerate(attempts):
            if isinstance(attempt, Mapping) and attempt.get("reviewRef") and attempt.get("reviewRef") not in review_history:
                result.error("quality.attempt_review", f"execution.attempts[{index}].reviewRef must appear in reviewHistory")
    summary = task.get("qualitySummary")
    if summary is not None and not isinstance(summary, Mapping):
        result.error("quality.summary", "qualitySummary must be null or an object")
    mode = detect_mode(profile, task)
    role = str(task.get("role", "worker"))
    ownership = task.get("ownership") or {}
    if mode == "strict" and role not in {"reviewer", "explorer", "validator", "summarizer"} and state in {"Ready", "Active", "Candidate", "Accepted", "Integrated"} and not as_list(ownership.get("owned")):
        result.error("ownership.required", f"{state} Strict writer requires non-empty ownership.owned")
    workspace = task.get("workspace") or {}
    if not isinstance(workspace, Mapping):
        result.error("workspace.shape", "workspace must be an object")
        workspace = {}
    backend = workspace.get("backend")
    if workspace.get("policy") not in {"auto", "current", "isolated"}:
        result.error("workspace.policy", "workspace.policy must be auto, current, or isolated")
    if backend not in {"current", "code-worktree", "package-worktree", "unity-clone"}:
        result.error("workspace.backend", "workspace.backend must be current, code-worktree, package-worktree, or unity-clone")
    estimated = workspace.get("estimatedGiB")
    if estimated is not None and (not isinstance(estimated, (int, float)) or isinstance(estimated, bool) or estimated < 0):
        result.error("workspace.estimate", "workspace.estimatedGiB must be a non-negative number or null")
    isolated_backend = backend in {"code-worktree", "package-worktree", "unity-clone"}
    approval = workspace.get("approval")
    unprovisioned_workspace = isolated_backend and (approval == "pending" or (approval == "declined" and state == "Blocked"))
    if isinstance(estimated, (int, float)) and estimated > 10 and isolated_backend and approval not in {"approved", "pending", "declined"}:
        result.error("workspace.approval", "workspace estimates above 10 GiB require explicit approval")
    if approval not in {"not-required", "pending", "approved", "declined"}:
        result.error("workspace.approval", "workspace.approval must be not-required, pending, approved, or declined")
    if not workspace.get("reason"):
        result.error("workspace.reason", "workspace.reason is required")
    if isolated_backend and not workspace.get("path") and not unprovisioned_workspace:
        result.error("workspace.path", f"{backend} requires workspace.path")
    if isolated_backend and estimated is None:
        result.error("workspace.estimate", f"{backend} requires workspace.estimatedGiB")
    if isolated_backend and approval == "declined" and state != "Blocked":
        result.error("workspace.declined", "declined workspace must fall back to safe current work or set the Task to Blocked")
    if "parallelWriters" in as_list(task.get("risks")) and backend == "current" and state in {"Ready", "Active", "Candidate", "Accepted", "Integrated"}:
        result.error("workspace.parallel", "parallel writers cannot share the current checkout")
    if state in {"Candidate", "Accepted", "Integrated"}:
        if not task.get("baseSha"):
            result.error("commit.base_required", f"{state} requires baseSha")
        head = candidate_head(task)
        if not head:
            result.error("commit.candidate", f"{state} requires a candidate or fix commit")
        evidence = (task.get("execution") or {}).get("validationEvidence")
        debt = (task.get("validation") or {}).get("debt")
        if not evidence and not debt:
            result.error("validation.evidence", "candidate requires validation evidence or owned debt")
        if not (task.get("models") or {}).get("actual"):
            result.error("model.actual_required", f"{state} requires models.actual")
        if not (task.get("execution") or {}).get("handoff"):
            result.error("execution.handoff", f"{state} requires embedded execution.handoff")
    if state in {"Accepted", "Integrated"} and not task.get("reviewRef"):
        result.error("review.reference", f"{state} requires reviewRef")
    if state == "Integrated":
        tracked_quality = isinstance(execution, Mapping) and "attempts" in execution or "reviewHistory" in task or "qualitySummary" in task
        if tracked_quality:
            commits = task.get("commits") or {}
            expected_heads = [commits.get("candidate"), *as_list(commits.get("fix"))]
            actual_heads = [item.get("headSha") for item in attempts or [] if isinstance(item, Mapping)]
            if actual_heads != expected_heads:
                result.error("quality.attempt_heads", "Integrated execution attempts must match the candidate/fix commit sequence")
            if any(not item.get("reviewRef") or not item.get("reviewStatus") for item in attempts or [] if isinstance(item, Mapping)):
                result.error("quality.attempt_review", "Integrated execution attempts require reviewRef and reviewStatus")
            if not isinstance(summary, Mapping) or summary.get("complete") is not True:
                result.error("quality.incomplete", "Integrated tracked task requires complete qualitySummary")
        else:
            result.warn("quality.legacy", "Integrated task is legacy/incomplete: tracked quality fields are absent")
        close = task.get("close") or {}
        if not close.get("mergeCommit") or close.get("integrationValidationPassed") is not True:
            result.error("integration.evidence", "Integrated requires mergeCommit and integrationValidationPassed")
    return result


def validate_phase(phase: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if phase.get("schemaVersion") != SCHEMA_VERSION:
        result.error("phase.schema", "schemaVersion must be 1")
    for name in ("phaseId", "baselineSha", "integrationBranch"):
        if not phase.get(name):
            result.error("phase.missing", f"missing phase field: {name}")
    if phase.get("contractsFrozen") is not True:
        result.error("phase.contracts", "Strict phase requires frozen contracts")
    if not phase.get("baselineReviewRef"):
        result.error("phase.baseline_review", "Strict phase requires baselineReviewRef")
    dag = phase.get("taskDag")
    if not isinstance(dag, list):
        result.error("phase.task_dag", "Strict phase requires taskDag array")
    else:
        dependencies: dict[str, list[str]] = {}
        for index, node in enumerate(dag):
            if not isinstance(node, Mapping) or not node.get("taskId") or not isinstance(node.get("dependencies", []), list):
                result.error("phase.task_dag", f"taskDag[{index}] requires taskId and dependencies array")
                continue
            task_id = str(node["taskId"])
            if task_id in dependencies:
                result.error("phase.task_duplicate", f"duplicate taskDag taskId: {task_id}")
            dependencies[task_id] = [str(value) for value in node.get("dependencies", [])]
        for task_id, values in dependencies.items():
            for dependency in values:
                if dependency not in dependencies:
                    result.error("phase.dependency_missing", f"{task_id} depends on unknown task {dependency}")
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(task_id: str) -> None:
            if task_id in visiting:
                result.error("phase.dependency_cycle", f"taskDag contains a cycle at {task_id}")
                return
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in dependencies.get(task_id, []):
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)
        for task_id in dependencies:
            visit(task_id)
    return result


def validate_review(
    review: Mapping[str, Any],
    task: Mapping[str, Any] | None = None,
    phase: Mapping[str, Any] | None = None,
) -> ValidationResult:
    result = ValidationResult()
    if review.get("schemaVersion") != SCHEMA_VERSION:
        result.error("review.schema", "schemaVersion must be 1")
    if not review.get("reviewId"):
        result.error("review.id", "reviewId is required")
    if review.get("status") not in REVIEW_STATES:
        result.error("review.status", f"review status must be one of {sorted(REVIEW_STATES)}")
    if review.get("reviewerModel") != DEFAULT_MODELS["reviewer"]:
        result.error("review.model", f"reviewer must use {DEFAULT_MODELS['reviewer']}")
    if not isinstance(review.get("findings"), list):
        result.error("review.findings", "review findings must be an array")
    else:
        for index, finding in enumerate(review.get("findings") or []):
            if not isinstance(finding, Mapping):
                result.error("review.finding", f"findings[{index}] must be an object")
                continue
            priority = finding.get("priority")
            if priority is None:
                result.warn("review.finding_priority_missing", f"findings[{index}] is legacy/incomplete: priority is missing")
            elif priority not in FINDING_PRIORITIES:
                result.error("review.finding_priority", f"findings[{index}].priority must be P0, P1, P2, or P3")
            origin = finding.get("origin")
            if origin is None:
                result.warn("review.finding_origin_missing", f"findings[{index}] is legacy/incomplete: origin is missing")
            elif origin not in FINDING_ORIGINS:
                result.error("review.finding_origin", f"findings[{index}].origin is invalid")
    if not isinstance(review.get("validation"), list):
        result.error("review.validation", "review validation must be an array")
    cycle = review.get("cycle")
    if not isinstance(cycle, int) or isinstance(cycle, bool) or cycle < 1 or cycle > 2:
        result.error("review.cycle", "review cycle must be 1 or 2")
    subject = review.get("subject") or {}
    kind = subject.get("kind") if isinstance(subject, Mapping) else None
    if kind == "candidate":
        if not task:
            result.error("review.task_required", "candidate review requires its task packet")
            return result
        if subject.get("taskId") != task.get("taskId"):
            result.error("review.task", "review subject taskId differs from task")
        if subject.get("headSha") != candidate_head(task):
            result.error("review.stale", "review subject headSha must equal latest candidate/fix head")
        if subject.get("baseSha") != task.get("baseSha"):
            result.error("review.base", "review subject baseSha must equal task baseSha")
        if subject.get("baseSha") == subject.get("headSha"):
            result.error("review.range", "review range must be non-empty")
        if task.get("state") in {"Accepted", "Integrated"} and review.get("status") != "Accepted":
            result.error("review.verdict", "Accepted and Integrated tasks require an Accepted review")
        if review.get("status") == "ChangesRequested" and cycle == 2 and task.get("state") != "Replan Required":
            result.error("review.replan", "second failed review requires Replan Required")
    elif kind == "baseline":
        if not phase:
            result.error("review.phase_required", "baseline review requires its phase artifact")
            return result
        if subject.get("phaseId") != phase.get("phaseId"):
            result.error("review.phase", "review subject phaseId differs from phase")
        if subject.get("sha") != phase.get("baselineSha"):
            result.error("review.baseline", "review subject sha must equal phase baselineSha")
        if review.get("status") != "Accepted":
            result.error("review.baseline_status", "Strict baseline review must be Accepted")
    else:
        result.error("review.subject", "review subject.kind must be candidate or baseline")
    return result


def validate_repository_commits(repo: Path, task: Mapping[str, Any], phase: Mapping[str, Any] | None = None) -> ValidationResult:
    result = ValidationResult()
    commits = task.get("commits") or {}
    values = [commits.get("candidate"), *as_list(commits.get("fix"))] if isinstance(commits, dict) else []
    resolved: list[str] = []
    for value in values:
        if not value:
            continue
        process = git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
        if process.returncode:
            result.error("commit.missing", f"commit does not resolve: {value}")
        else:
            resolved.append(process.stdout.strip())
    for parent, child in zip(resolved, resolved[1:]):
        if git(repo, "merge-base", "--is-ancestor", parent, child).returncode:
            result.error("commit.sequence", "each fix commit must descend from the preceding candidate/fix commit")
    head = candidate_head(task)
    baseline = task.get("baseSha") or (phase or {}).get("baselineSha")
    if phase and task.get("baseSha") and task.get("baseSha") != phase.get("baselineSha"):
        result.error("commit.phase_base", "Strict task baseSha must equal phase baselineSha")
    if baseline and head:
        baseline_process = git(repo, "rev-parse", "--verify", f"{baseline}^{{commit}}")
        if baseline_process.returncode:
            result.error("commit.baseline_missing", f"declared base commit does not resolve: {baseline}")
        elif git(repo, "merge-base", "--is-ancestor", str(baseline), str(head)).returncode:
            result.error("commit.lineage", "candidate head must descend from phase baseline")
    return result


def canonical_evidence_path(repo: Path, value: Any) -> tuple[str | None, Path | None]:
    if not value:
        return None, None
    path = Path(str(value))
    target = path.resolve() if path.is_absolute() else (repo / path).resolve()
    try:
        relative = target.relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None, target
    return normalize_path(relative), target


def validate_repository_evidence(
    repo: Path,
    task: Mapping[str, Any],
    phase: Mapping[str, Any] | None,
    review: Mapping[str, Any] | None,
) -> ValidationResult:
    result = ValidationResult()
    if phase:
        value = phase.get("baselineReviewRef")
        relative, target = canonical_evidence_path(repo, value)
        if not relative or not target or not target.is_file():
            result.error("phase.baseline_evidence_path", "baseline review evidence must be an existing file inside the repository")
        else:
            try:
                evidence = read_object(target)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                result.error("phase.baseline_evidence_parse", f"invalid baseline review evidence: {error}")
            else:
                result.extend(validate_review(evidence, phase=phase))
        baseline = phase.get("baselineSha")
        if baseline and git(repo, "rev-parse", "--verify", f"{baseline}^{{commit}}").returncode:
            result.error("commit.baseline_missing", f"phase baseline does not resolve: {baseline}")
    if review:
        embedded = task.get("reviewRef")
        embedded_relative, embedded_target = canonical_evidence_path(repo, embedded)
        actual_relative, actual_target = canonical_evidence_path(repo, review.get("_evidencePath"))
        if not embedded_relative or not embedded_target or not embedded_target.is_file():
            result.error("review.evidence_missing", "task review evidence must be an existing file inside the repository")
        if not actual_relative or not actual_target or not actual_target.is_file():
            result.error("review.evidence_missing", "immutable review artifact must exist inside the repository")
        elif embedded_relative != actual_relative:
            result.error("review.evidence_path", "task review evidence path differs from the validated review artifact")
    allowed_heads = {
        value for value in [
            (task.get("commits") or {}).get("candidate"),
            *as_list((task.get("commits") or {}).get("fix")),
        ] if value
    }
    for index, value in enumerate(as_list(task.get("reviewHistory"))):
        relative, target = canonical_evidence_path(repo, value)
        if not relative or not target or not target.is_file():
            result.error("review.history_evidence", f"reviewHistory[{index}] must be an existing file inside the repository")
            continue
        try:
            evidence = read_object(target)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            result.error("review.history_parse", f"invalid reviewHistory[{index}] evidence: {error}")
            continue
        subject = evidence.get("subject") or {}
        if subject.get("kind") != "candidate" or subject.get("taskId") != task.get("taskId"):
            result.error("review.history_subject", f"reviewHistory[{index}] must bind this candidate task")
        if subject.get("baseSha") != task.get("baseSha") or subject.get("headSha") not in allowed_heads:
            result.error("review.history_range", f"reviewHistory[{index}] must bind a recorded candidate/fix range")
    return result


def validate_repository_ownership(repo: Path, task: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    base, head = task.get("baseSha"), candidate_head(task)
    if not base or not head:
        return result
    process = git(repo, "diff", "--name-only", f"{base}..{head}")
    if process.returncode:
        result.error("ownership.diff", "cannot inspect candidate commit range ownership")
        return result
    ownership = task.get("ownership") or {}
    owned = as_list(ownership.get("owned"))
    forbidden = as_list(ownership.get("forbidden"))
    shared = as_list(ownership.get("shared"))
    role = str(task.get("role", "worker"))
    for path in (line.strip() for line in process.stdout.splitlines() if line.strip()):
        if any(path_matches(path, str(rule)) for rule in forbidden):
            result.error("ownership.forbidden", f"candidate changes forbidden path: {path}")
        elif any(path_matches(path, str(rule)) for rule in shared):
            if role not in {"orchestrator", "shared-contract-owner"}:
                result.error("ownership.shared", f"candidate changes unowned shared path: {path}")
        elif not any(path_matches(path, str(rule)) for rule in owned):
            result.error("ownership.outside", f"candidate changes path outside ownership: {path}")
    return result


def validate_wave(
    repo: Path,
    tasks: Iterable[Mapping[str, Any]],
    phase: Mapping[str, Any],
    profile: Mapping[str, Any] | None = None,
    complete: bool = True,
) -> ValidationResult:
    result = validate_phase(phase)
    result.extend(validate_repository_evidence(repo, {}, phase, None))
    tasks = list(tasks)
    dag = {
        str(node.get("taskId")): [str(value) for value in node.get("dependencies", [])]
        for node in as_list(phase.get("taskDag")) if isinstance(node, Mapping) and node.get("taskId")
    }
    task_ids = {str(task.get("taskId") or "") for task in tasks}
    if complete:
        for missing in sorted(set(dag) - task_ids):
            result.error("phase.task_artifact_missing", f"phase task has no loaded Task artifact: {missing}")
    worktrees: set[str] = set()
    active_current_writers: list[str] = []
    lease_owners: dict[str, str] = {}
    for lease in as_list(phase.get("leases")):
        if not isinstance(lease, dict) or not lease.get("resource") or not lease.get("owner"):
            result.error("lease.shape", "each lease requires resource and owner")
            continue
        if lease.get("active", True) is not True:
            continue
        resource, owner = str(lease["resource"]), str(lease["owner"])
        if resource in lease_owners and lease_owners[resource] != owner:
            result.error("lease.conflict", f"resource {resource} is leased to multiple owners")
        lease_owners[resource] = owner
    for task in tasks:
        result.extend(validate_task(task, profile))
        task_id = str(task.get("taskId") or "")
        if detect_mode(profile, task, phase) != "strict":
            result.error("phase.task_mode", f"phase task {task_id} must use Strict mode")
        if task_id not in dag:
            result.error("phase.task_missing", f"Strict task {task_id} is absent from phase.taskDag")
        elif [str(value) for value in as_list(task.get("dependencies"))] != dag[task_id]:
            result.error("phase.dependency_drift", f"task {task_id} dependencies differ from phase.taskDag")
        worktree = str(task_worktree(task) or "")
        dispatchable = task.get("state") in {"Ready", "Active", "Candidate", "Accepted", "Integrated"}
        role = str(task.get("role") or "worker")
        backend = str((task.get("workspace") or {}).get("backend") or "current")
        if task.get("state") == "Active" and role not in {"reviewer", "explorer", "validator", "summarizer"} and backend == "current":
            active_current_writers.append(task_id)
        normalized = normalize_path(worktree)
        if worktree:
            if normalized in worktrees:
                result.error("worktree.duplicate", f"duplicate worktree: {worktree}")
            worktrees.add(normalized)
        if dispatchable and "externalResources" in as_list(task.get("risks")) and str(task.get("taskId")) not in lease_owners.values():
            result.error("lease.required", f"external-resource task {task.get('taskId')} requires an active lease")
    if len(active_current_writers) > 1:
        result.error("workspace.parallel", f"active writers cannot share the current checkout: {', '.join(active_current_writers)}")
    for index, task in enumerate(tasks):
        left = as_list((task.get("ownership") or {}).get("owned"))
        for other in tasks[index + 1:]:
            right = as_list((other.get("ownership") or {}).get("owned"))
            if any(paths_overlap(str(a), str(b)) for a in left for b in right):
                result.error("ownership.overlap", f"owned paths overlap: {task.get('taskId')} and {other.get('taskId')}")
    return result


def inspect_worktree(repo: Path, path: Path) -> dict[str, Any]:
    path = path.resolve()
    top = git(path, "rev-parse", "--show-toplevel") if path.exists() else None
    branch = git(path, "branch", "--show-current") if path.exists() else None
    status = git(path, "status", "--porcelain") if path.exists() else None
    return {
        "path": str(path),
        "exists": path.is_dir(),
        "registered": bool(top and not top.returncode and Path(top.stdout.strip()).resolve() == path),
        "branch": branch.stdout.strip() if branch and not branch.returncode else None,
        "clean": bool(status and not status.returncode and not status.stdout.strip()),
    }


def check_repository(
    repo: Path,
    profile: Mapping[str, Any] | None = None,
    task: Mapping[str, Any] | None = None,
    phase: Mapping[str, Any] | None = None,
    review: Mapping[str, Any] | None = None,
    check_all: bool = False,
) -> ValidationResult:
    result = ValidationResult()
    if profile:
        result.extend(validate_profile(profile))
    mode = detect_mode(profile, task, phase)
    result.data["mode"] = mode
    if not task:
        result.data["idle"] = True
        if phase:
            result.extend(validate_phase(phase))
            result.extend(validate_repository_evidence(repo, {}, phase, None))
        if review:
            result.extend(validate_review(review, phase=phase))
        return result
    result.data["idle"] = False
    result.extend(validate_task(task, profile))
    if task.get("state") in {"Candidate", "Accepted", "Integrated"}:
        result.extend(validate_repository_commits(repo, task, phase))
        if mode == "strict" or check_all:
            result.extend(validate_repository_ownership(repo, task))
    if mode == "strict" or check_all:
        if not phase:
            result.error("phase.required", "Strict lifecycle requires a phase artifact")
        else:
            result.extend(validate_wave(repo, [task], phase, profile, complete=False))
        worktree_value = task_worktree(task)
        if worktree_value:
            info = inspect_worktree(repo, resolve_path(repo, str(worktree_value)) or repo)
            result.data["worktree"] = info
            if not info["exists"]:
                result.error("worktree.missing", f"declared worktree does not exist: {worktree_value}")
            elif not info["registered"]:
                result.error("worktree.unregistered", f"declared path is not an exact Git worktree: {worktree_value}")
    if review:
        result.extend(validate_review(review, task, phase))
    elif task.get("state") in {"Accepted", "Integrated"}:
        result.error("review.required", "Accepted and Integrated tasks require immutable review evidence")
    if review:
        result.extend(validate_repository_evidence(repo, task, None, review))
    return result
