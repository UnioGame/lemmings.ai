"""Canonical schema-v1 contracts for Lemmings."""

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
    "complex-worker": "gpt-5.6-sol:medium",
}
ORCHESTRATOR_EFFORTS = {"high", "xhigh", "max", "ultra"}


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
        for role, required in DEFAULT_MODELS.items():
            if models.get(role, required) != required:
                result.error("model.fixed", f"{role} must use {required}")
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
    if not effective_pin and not requested and default_assignment and assigned != default_assignment:
        result.error("model.default_assignment", f"models.assigned must equal role default {default_assignment}")
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


def validate_task(task: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> ValidationResult:
    result = ValidationResult()
    if task.get("schemaVersion") != SCHEMA_VERSION:
        result.error("task.schema", "schemaVersion must be 1")
    for name in ("taskId", "state", "ownership", "models", "validation"):
        if not task.get(name):
            result.error("task.missing", f"missing task field: {name}")
    state = str(task.get("state", ""))
    if state not in TASK_STATES:
        result.error("state.unknown", f"unknown task state: {state}")
    previous = task.get("previousState")
    if previous and state not in TRANSITIONS.get(str(previous), set()) and state != previous:
        result.error("state.transition", f"illegal transition: {previous} -> {state}")
    result.extend(validate_models(task, profile))
    result.extend(validate_debt(task))
    mode = detect_mode(profile, task)
    role = str(task.get("role", "worker"))
    ownership = task.get("ownership") or {}
    if mode == "strict" and role not in {"reviewer", "explorer", "validator", "summarizer"} and state in {"Ready", "Active", "Candidate", "Accepted", "Integrated"} and not as_list(ownership.get("owned")):
        result.error("ownership.required", f"{state} Strict writer requires non-empty ownership.owned")
    review = task.get("review") or {}
    cycle = review.get("cycle", 0) if isinstance(review, dict) else -1
    if not isinstance(cycle, int) or isinstance(cycle, bool) or cycle < 0 or cycle > 2:
        result.error("review.cycle", "review.cycle must be between 0 and 2")
    if cycle >= 2 and review.get("status") == "ChangesRequested" and state != "Replan Required":
        result.error("review.replan", "second failed review requires Replan Required")
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
    if state in {"Accepted", "Integrated"}:
        if review.get("status") != "Accepted":
            result.error("review.accepted", f"{state} requires an Accepted review")
        if review.get("head") != candidate_head(task):
            result.error("review.stale", "review head must equal the latest candidate/fix head")
        if not review.get("evidence"):
            result.error("review.evidence", "accepted task must reference immutable review evidence")
        if review.get("base") != task.get("baseSha"):
            result.error("review.base", "embedded review base must equal task baseSha")
        if review.get("base") == review.get("head"):
            result.error("review.range", "review range must be non-empty")
    if state == "Integrated":
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
    baseline_review = phase.get("baselineReview") or {}
    if baseline_review.get("status") != "Accepted":
        result.error("phase.baseline_review", "Strict phase requires an Accepted baselineReview")
    if baseline_review.get("reviewerModel") != DEFAULT_MODELS["reviewer"]:
        result.error("phase.baseline_reviewer", f"baseline review must use {DEFAULT_MODELS['reviewer']}")
    if not baseline_review.get("evidence"):
        result.error("phase.baseline_evidence", "accepted baseline review requires immutable evidence")
    return result


def validate_review(review: Mapping[str, Any], task: Mapping[str, Any], phase: Mapping[str, Any] | None = None) -> ValidationResult:
    result = ValidationResult()
    if review.get("schemaVersion") != SCHEMA_VERSION:
        result.error("review.schema", "schemaVersion must be 1")
    if review.get("taskId") != task.get("taskId"):
        result.error("review.task", "review taskId differs from task")
    if review.get("status") not in REVIEW_STATES:
        result.error("review.status", f"review status must be one of {sorted(REVIEW_STATES)}")
    if review.get("head") != candidate_head(task):
        result.error("review.stale", "review head must equal latest candidate/fix head")
    if review.get("reviewerModel") != DEFAULT_MODELS["reviewer"]:
        result.error("review.model", f"reviewer must use {DEFAULT_MODELS['reviewer']}")
    embedded = task.get("review") or {}
    for field_name in ("taskId", "base", "head", "status"):
        if embedded.get(field_name) != review.get(field_name):
            result.error("review.binding", f"task review {field_name} differs from immutable evidence")
    if not embedded.get("evidence"):
        result.error("review.evidence_path", "task review must reference immutable review evidence")
    if phase and review.get("base") != phase.get("baselineSha"):
        result.error("review.base", "review base must equal phase baselineSha")
    if review.get("base") != task.get("baseSha"):
        result.error("review.base", "immutable review base must equal task baseSha")
    if review.get("base") == review.get("head"):
        result.error("review.range", "immutable review range must be non-empty")
    if task.get("state") in {"Accepted", "Integrated"} and review.get("status") != "Accepted":
        result.error("review.verdict", "Accepted and Integrated tasks require Accepted immutable review")
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
        value = (phase.get("baselineReview") or {}).get("evidence")
        relative, target = canonical_evidence_path(repo, value)
        if not relative or not target or not target.is_file():
            result.error("phase.baseline_evidence_path", "baseline review evidence must be an existing file inside the repository")
        else:
            try:
                evidence = read_object(target)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                result.error("phase.baseline_evidence_parse", f"invalid baseline review evidence: {error}")
            else:
                expected = {
                    "schemaVersion": SCHEMA_VERSION,
                    "phaseId": phase.get("phaseId"),
                    "baselineSha": phase.get("baselineSha"),
                    "status": "Accepted",
                    "reviewerModel": DEFAULT_MODELS["reviewer"],
                }
                for field_name, expected_value in expected.items():
                    if evidence.get(field_name) != expected_value:
                        result.error("phase.baseline_binding", f"baseline review {field_name} differs from phase")
        baseline = phase.get("baselineSha")
        if baseline and git(repo, "rev-parse", "--verify", f"{baseline}^{{commit}}").returncode:
            result.error("commit.baseline_missing", f"phase baseline does not resolve: {baseline}")
    if review:
        embedded = (task.get("review") or {}).get("evidence")
        embedded_relative, embedded_target = canonical_evidence_path(repo, embedded)
        actual_relative, actual_target = canonical_evidence_path(repo, review.get("_evidencePath"))
        if not embedded_relative or not embedded_target or not embedded_target.is_file():
            result.error("review.evidence_missing", "task review evidence must be an existing file inside the repository")
        if not actual_relative or not actual_target or not actual_target.is_file():
            result.error("review.evidence_missing", "immutable review artifact must exist inside the repository")
        elif embedded_relative != actual_relative:
            result.error("review.evidence_path", "task review evidence path differs from the validated review artifact")
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


def validate_wave(repo: Path, tasks: Iterable[Mapping[str, Any]], phase: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> ValidationResult:
    result = validate_phase(phase)
    result.extend(validate_repository_evidence(repo, {}, phase, None))
    tasks = list(tasks)
    worktrees: set[str] = set()
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
        worktree = str(task.get("worktree") or "")
        if not worktree:
            result.error("worktree.required", f"Strict task {task.get('taskId')} requires worktree")
        normalized = normalize_path(worktree)
        if normalized in worktrees:
            result.error("worktree.duplicate", f"duplicate worktree: {worktree}")
        worktrees.add(normalized)
        if "externalResources" in as_list(task.get("risks")) and str(task.get("taskId")) not in lease_owners.values():
            result.error("lease.required", f"external-resource task {task.get('taskId')} requires an active lease")
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
            result.error("review.task_required", "review evidence cannot be validated without its task packet")
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
            result.extend(validate_wave(repo, [task], phase, profile))
        worktree_value = task.get("worktree")
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
