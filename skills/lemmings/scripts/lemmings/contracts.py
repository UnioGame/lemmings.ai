"""Canonical schema-v4 orchestration contracts for Lemmings."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 4
DISTRIBUTION_VERSION = "4.1.1"
PLUGIN_VERSION = "4.1.1"
STAGES = ("Prepare", "Dispatch", "Execute/Candidate", "Review/Repair", "Integrate/Close")
MODES = {"auto", "simple", "standard", "strict"}
TASK_STATES = {
    "Draft", "Planned", "Ready", "Active", "Repair", "Candidate", "Accepted", "Integrated",
    "Blocked", "Replan Required", "Cancelled", "Superseded",
}
TERMINAL_STATES = {"Integrated", "Replan Required", "Cancelled", "Superseded"}
TRANSITIONS = {
    "Draft": {"Ready", "Blocked", "Cancelled", "Superseded"},
    "Planned": {"Ready", "Blocked", "Cancelled", "Superseded"},
    "Ready": {"Active", "Blocked", "Cancelled", "Superseded"},
    "Active": {"Candidate", "Repair", "Blocked", "Cancelled", "Replan Required"},
    "Repair": {"Candidate", "Blocked", "Cancelled", "Replan Required"},
    "Candidate": {"Candidate", "Repair", "Accepted", "Blocked", "Cancelled", "Replan Required"},
    "Accepted": {"Integrated", "Replan Required"},
    "Blocked": {"Ready", "Active", "Cancelled", "Replan Required"},
    "Integrated": set(), "Replan Required": set(), "Cancelled": set(), "Superseded": set(),
}
REVIEW_STATES = {"Pending", "ChangesRequested", "Accepted"}
REVIEW_POLICIES = {"single", "cross"}
REVIEW_SUBJECT_KINDS = {"candidate", "baseline", "plan"}
CROSS_REVIEW_DEGRADATION = "cross-review-unavailable"
STRICT_RISKS = {
    "parallelWriters", "sharedContracts", "unitySerializedAssets", "submodules",
    "codegen", "externalResources", "multiRepository", "exclusiveResources",
    "highRisk", "baselineReview", "overlappingOwnership", "sharedSerializedAssets",
    "integrationBranch", "resourceLeases",
}
STANDARD_RISKS = {"mediumRisk", "publicContract", "isolatedWriter", "wideValidation", "candidateReview", "repair", "independentReview"}
FINDING_ORIGINS = {"implementation", "plan-contract", "validation", "integration"}
FINDING_PRIORITIES = {"P0", "P1", "P2", "P3"}
TASK_ROLES = {"manager", "worker", "reviewer", "explorer"}
DEFAULT_CONTEXT_POLICY = {
    "maxPacketBytes": 16384,
    "maxWorkingSetItems": 12,
    "maxExpansions": 1,
}
DEFAULT_INVOCATION_LIMITS = {
    "explorer": {"maxTurns": 6, "maxToolCalls": 12, "deadlineSeconds": 600},
    "reviewer": {"maxTurns": 8, "maxToolCalls": 16, "deadlineSeconds": 1200},
    "worker": {"maxTurns": 12, "maxToolCalls": 24, "deadlineSeconds": 2700},
}
MODE_RISK_CLASSES = {"low", "medium", "high"}
MODE_ORDER = {"simple": 0, "standard": 1, "strict": 2}
WORKSPACE_RELEASE_ACTIONS = {"current", "released-to-pool", "removed", "retained", "external"}
HOST_CAPABILITY_FIELDS = {
    "isolation", "parallelAgents", "cancellation", "structuredOutput",
    "usageAccounting", "capacityProbe", "modelCatalog", "toolCallLimits", "approvals",
}
ROUTE_FAILURE_CATEGORIES = {
    "quota_exhausted", "rate_limited", "model_unavailable",
    "auth_or_billing", "context_limit", "transient_transport",
}
ROUTING_RECOVERY_STATUSES = {"pending-confirmation", "approved", "paused", "completed"}


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


def schema_version(value: Mapping[str, Any]) -> int | None:
    version = value.get("schemaVersion")
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def schema_supported(value: Mapping[str, Any]) -> bool:
    return schema_version(value) == SCHEMA_VERSION


def schema_error(kind: str, value: Mapping[str, Any]) -> str:
    version = value.get("schemaVersion")
    if version == 2:
        return "schemaVersion 2 is unsupported by Lemmings 4.0; replace the legacy bundle"
    return f"unsupported schemaVersion: {version!r}; expected 4"


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
    current = task or {}
    resolved = current.get("resolvedMode")
    if isinstance(resolved, str) and resolved.lower() in {"simple", "standard", "strict"}:
        return resolved.lower()
    requested = str(current.get("requestedMode") or current.get("mode") or profile.get("mode") or "auto").lower()
    if requested in {"simple", "standard", "strict"}:
        return requested
    signals = set(as_list(current.get("modeReasons"))) | set(as_list(profile.get("modeReasons")))
    if phase or signals.intersection(STRICT_RISKS):
        return "strict"
    if signals.intersection(STANDARD_RISKS) or task:
        return "standard"
    return "simple"


def resolve_auto_mode(
    signals: Mapping[str, Any] | None = None,
    *,
    requested: str = "auto",
    phase: bool = False,
) -> dict[str, Any]:
    """Resolve Auto with an explainable priority ladder, without inspecting telemetry."""
    values = signals or {}
    requested_mode = str(requested or "auto").lower()
    if requested_mode not in MODES:
        raise ValueError(f"unknown requested mode: {requested_mode}")
    risk_class = str(values.get("riskClass") or "low").lower()
    if risk_class not in MODE_RISK_CLASSES:
        raise ValueError(f"unknown risk class: {risk_class}")
    mode_reasons = {str(item) for item in as_list(values.get("modeReasons"))}
    strict_reasons = sorted(mode_reasons.intersection(STRICT_RISKS))
    standard_reasons = sorted(mode_reasons.intersection(STANDARD_RISKS))
    ownership = values.get("ownership") if isinstance(values.get("ownership"), Mapping) else {}
    resources = values.get("resources") if isinstance(values.get("resources"), Mapping) else {}
    workspace = values.get("workspace") if isinstance(values.get("workspace"), Mapping) else {}
    if phase:
        strict_reasons.append("phase")
    if int(values.get("writerCount") or 0) > 1:
        strict_reasons.append("parallelWriters")
    if as_list(ownership.get("shared")):
        strict_reasons.append("sharedContracts")
    if as_list(resources.get("exclusive")):
        strict_reasons.append("exclusiveResources")
    if risk_class == "high":
        strict_reasons.append("highRisk")
    elif risk_class == "medium":
        standard_reasons.append("mediumRisk")
    if values.get("workerRequired"):
        standard_reasons.append("workerRequired")
    if int(values.get("ownershipDomainCount") or 1) > 1:
        standard_reasons.append("multipleOwnershipDomains")
    if workspace.get("backend") in {"code-worktree", "package-worktree", "unity-clone"}:
        standard_reasons.append("isolatedWriter")
    if values.get("reviewRequired") or values.get("state") in {"Repair", "Candidate", "Accepted"}:
        standard_reasons.append("candidateReview")
    if requested_mode != "auto":
        resolved = requested_mode
        reasons = ["explicit-mode-pin"]
    elif strict_reasons:
        resolved, reasons = "strict", sorted(set(strict_reasons))
    elif standard_reasons:
        resolved, reasons = "standard", sorted(set(standard_reasons))
    else:
        resolved, reasons = "simple", ["single-domain-low-risk"]
    return {
        "requestedMode": requested_mode,
        "resolvedMode": resolved,
        "riskClass": risk_class,
        "reasons": reasons,
        "capabilityDegradations": [str(item) for item in as_list(values.get("capabilityDegradations"))],
    }


def route_name(route: Mapping[str, Any]) -> str | None:
    provider = route.get("providerId")
    model = route.get("modelId")
    if not isinstance(provider, str) or not provider.strip() or not isinstance(model, str) or not model.strip():
        return None
    variant = route.get("variantId")
    return f"{provider}/{model}" + (f":{variant}" if isinstance(variant, str) and variant.strip() else "")


def route_model_identity(value: Any) -> str | None:
    """Return provider/model without a variant; variants are not distinct models."""
    if isinstance(value, Mapping):
        value = route_name(value)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.split(":", 1)[0]


def validate_model_routes(routes: Any) -> ValidationResult:
    result = ValidationResult()
    if not isinstance(routes, Mapping) or not routes:
        result.error("profile.model_routes", "modelRoutes must be a non-empty host map")
        return result
    for host_id, roles in routes.items():
        if not isinstance(host_id, str) or not host_id.strip() or not isinstance(roles, Mapping):
            result.error("profile.model_routes", "each modelRoutes host requires a non-empty id and role map")
            continue
        unknown = sorted(set(roles) - {"worker", "reviewer", "explorer"})
        if unknown:
            result.error("profile.model_routes", f"unsupported {host_id} roles: {', '.join(unknown)}")
        for role in ("worker", "reviewer", "explorer"):
            choices = roles.get(role)
            if not isinstance(choices, list) or not choices:
                result.error("profile.model_routes", f"modelRoutes.{host_id}.{role} must be a non-empty ordered route array")
                continue
            names: list[str] = []
            for index, choice in enumerate(choices):
                name = route_name(choice) if isinstance(choice, Mapping) else None
                if not name:
                    result.error("profile.model_route", f"modelRoutes.{host_id}.{role}[{index}] requires providerId and modelId")
                elif name in names:
                    result.error("profile.model_route", f"duplicate route in modelRoutes.{host_id}.{role}: {name}")
                else:
                    names.append(name)
                if isinstance(choice, Mapping) and "specializations" in choice:
                    specializations = choice.get("specializations")
                    if not isinstance(specializations, list) or any(
                        not isinstance(item, str) or not item.strip() for item in specializations
                    ):
                        result.error(
                            "profile.model_route_specializations",
                            f"modelRoutes.{host_id}.{role}[{index}].specializations must be a string array",
                        )
                    elif len(specializations) != len(set(specializations)):
                        result.error(
                            "profile.model_route_specializations_duplicate",
                            f"modelRoutes.{host_id}.{role}[{index}].specializations must not contain duplicates",
                        )
    return result


def validate_host_capabilities(value: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if not isinstance(value.get("hostId"), str) or not value.get("hostId"):
        result.error("host.id", "host capabilities require hostId")
    capabilities = value.get("capabilities")
    if not isinstance(capabilities, Mapping):
        result.error("host.capabilities", "capabilities must be an object")
        return result
    missing = HOST_CAPABILITY_FIELDS - set(capabilities)
    unknown = set(capabilities) - HOST_CAPABILITY_FIELDS
    if missing:
        result.error("host.capabilities", "missing host capabilities: " + ", ".join(sorted(missing)))
    if unknown:
        result.error("host.capabilities", "unknown host capabilities: " + ", ".join(sorted(unknown)))
    for name in HOST_CAPABILITY_FIELDS.intersection(capabilities):
        if not isinstance(capabilities[name], bool):
            result.error("host.capability", f"capabilities.{name} must be boolean")
    return result


def host_degradations(value: Mapping[str, Any]) -> list[str]:
    """Translate missing host mechanics into topology changes, never weaker guarantees."""
    capabilities = value.get("capabilities") if isinstance(value.get("capabilities"), Mapping) else {}
    degradations: list[str] = []
    if not capabilities.get("isolation") or not capabilities.get("parallelAgents"):
        degradations.append("serialize-writers")
    if not capabilities.get("cancellation"):
        degradations.append("ignore-late-results")
    if not capabilities.get("structuredOutput"):
        degradations.append("one-local-schema-correction")
    if not capabilities.get("usageAccounting"):
        degradations.append("count-time-budgets")
    if not capabilities.get("capacityProbe"):
        degradations.append("reactive-capacity-recovery")
    if not capabilities.get("approvals"):
        degradations.append("user-confirmation-required")
    return degradations


def _validate_v4_profile(profile: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if profile.get("distributionVersion") != DISTRIBUTION_VERSION:
        result.error("profile.distribution", f"distributionVersion must be {DISTRIBUTION_VERSION}")
    mode = str(profile.get("mode", "auto")).lower()
    if mode not in MODES:
        result.error("profile.mode", f"mode must be one of {sorted(MODES)}")
    result.extend(validate_model_routes(profile.get("modelRoutes")))
    context_policy = profile.get("contextPolicy")
    if not isinstance(context_policy, Mapping):
        result.error("profile.context_policy", "contextPolicy is required")
    else:
        for name, expected in DEFAULT_CONTEXT_POLICY.items():
            if context_policy.get(name) != expected:
                result.error("profile.context_policy", f"contextPolicy.{name} must be {expected}")
    orchestration = profile.get("orchestration")
    if not isinstance(orchestration, Mapping):
        result.error("profile.orchestration", "orchestration is required")
    else:
        writers = orchestration.get("maxConcurrentWriters")
        readers = orchestration.get("maxConcurrentReaders")
        if not isinstance(writers, int) or isinstance(writers, bool) or not 1 <= writers <= 4:
            result.error("profile.orchestration", "orchestration.maxConcurrentWriters must be between 1 and 4")
        if not isinstance(readers, int) or isinstance(readers, bool) or not 0 <= readers <= 2:
            result.error("profile.orchestration", "orchestration.maxConcurrentReaders must be between 0 and 2")
        expected = {"maxDelegationDepth": 1, "managerSlots": 1, "maxRepairs": 1, "maxTransportRetries": 1}
        for name, value in expected.items():
            if orchestration.get(name) != value:
                result.error("profile.orchestration", f"orchestration.{name} must be {value}")
    pool = profile.get("workspacePool")
    if not isinstance(pool, Mapping):
        result.error("profile.workspace_pool", "workspacePool is required")
    else:
        if pool.get("enabled") is not True or pool.get("maxIdle") != 2 or pool.get("maxIdleGiB") != 10 or pool.get("eviction") != "lru":
            result.error("profile.workspace_pool", "workspacePool must enable lru with maxIdle=2 and maxIdleGiB=10")
    return result


def validate_profile(profile: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if not schema_supported(profile):
        result.error("profile.schema", schema_error("profile", profile))
        return result
    return _validate_v4_profile(profile)


def _route_ref_valid(value: Any) -> bool:
    return isinstance(value, Mapping) and isinstance(value.get("hostId"), str) and bool(value.get("hostId")) and bool(route_name(value))


def current_recovery_route(task: Mapping[str, Any], role: str) -> Mapping[str, Any] | None:
    recovery = task.get("routingRecovery")
    if not isinstance(recovery, Mapping) or recovery.get("status") not in {"approved", "paused", "completed"}:
        return None
    routes = (recovery.get("roleRoutes") or {}).get(role)
    index = (recovery.get("currentIndex") or {}).get(role, 0)
    if not isinstance(routes, list) or not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(routes):
        return None
    route = routes[index]
    return route if _route_ref_valid(route) else None


def validate_routing_recovery(task: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    recovery = task.get("routingRecovery")
    if recovery is None:
        return result
    if not isinstance(recovery, Mapping):
        result.error("routing_recovery.shape", "routingRecovery must be an object or null")
        return result
    allowed_fields = {
        "status", "trigger", "proposalDigest", "selectedOptionId", "approvedTaskRevision",
        "scope", "roleRoutes", "currentIndex", "attempts", "resumeAt", "exhaustedRole",
    }
    unknown = sorted(set(recovery) - allowed_fields)
    if unknown:
        result.error("routing_recovery.compact", "routingRecovery contains unsupported fields: " + ", ".join(unknown))
    status = recovery.get("status")
    if status not in ROUTING_RECOVERY_STATUSES:
        result.error("routing_recovery.status", "routingRecovery status is invalid")
    if recovery.get("scope") != "task":
        result.error("routing_recovery.scope", "routingRecovery.scope must be task")
    trigger = recovery.get("trigger")
    if not isinstance(trigger, Mapping):
        result.error("routing_recovery.trigger", "routingRecovery requires a RouteFailure trigger")
    else:
        if trigger.get("category") not in ROUTE_FAILURE_CATEGORIES:
            result.error("routing_recovery.trigger", "RouteFailure category is invalid")
        if not isinstance(trigger.get("invocationId"), str) or not trigger.get("invocationId"):
            result.error("routing_recovery.trigger", "RouteFailure requires invocationId")
        if not _route_ref_valid(trigger.get("route")):
            result.error("routing_recovery.trigger", "RouteFailure requires a valid RouteRef")
        retry_after = trigger.get("retryAfter")
        if retry_after is not None and (not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool) or retry_after < 0):
            result.error("routing_recovery.trigger", "RouteFailure.retryAfter must be non-negative")
    if not isinstance(recovery.get("proposalDigest"), str) or not recovery.get("proposalDigest"):
        result.error("routing_recovery.digest", "routingRecovery requires proposalDigest")
    if status in {"approved", "paused", "completed"}:
        if not isinstance(recovery.get("selectedOptionId"), str) or not recovery.get("selectedOptionId"):
            result.error("routing_recovery.option", "approved recovery requires selectedOptionId")
        approved_revision = recovery.get("approvedTaskRevision")
        if not isinstance(approved_revision, int) or isinstance(approved_revision, bool) or approved_revision < 0:
            result.error("routing_recovery.revision", "approvedTaskRevision must be a non-negative integer")
        elif isinstance(task.get("revision"), int) and approved_revision >= task["revision"]:
            result.error("routing_recovery.revision", "approvedTaskRevision must precede the current Task revision")
    role_routes = recovery.get("roleRoutes")
    if role_routes:
        if not isinstance(role_routes, Mapping) or set(role_routes) != {"worker", "reviewer", "explorer"}:
            result.error("routing_recovery.routes", "roleRoutes must contain worker, reviewer, and explorer")
        else:
            indexes = recovery.get("currentIndex")
            if not isinstance(indexes, Mapping):
                result.error("routing_recovery.cursor", "route recovery requires currentIndex")
                indexes = {}
            for role in ("worker", "reviewer", "explorer"):
                routes = role_routes.get(role)
                if not isinstance(routes, list) or not routes:
                    result.error("routing_recovery.routes", f"routingRecovery requires at least one {role} route")
                    continue
                names: list[str] = []
                for route in routes:
                    if not _route_ref_valid(route):
                        result.error("routing_recovery.route", f"routingRecovery {role} contains an invalid RouteRef")
                        continue
                    name = f"{route['hostId']}::{route_name(route)}"
                    if name in names:
                        result.error("routing_recovery.route", f"routingRecovery {role} contains duplicate routes")
                    names.append(name)
                index = indexes.get(role)
                if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(routes):
                    result.error("routing_recovery.cursor", f"currentIndex.{role} is outside its route chain")
    elif status == "approved":
        result.error("routing_recovery.routes", "approved routingRecovery requires roleRoutes")
    attempts = recovery.get("attempts")
    if not isinstance(attempts, list) or len(attempts) > 12:
        result.error("routing_recovery.attempts", "routingRecovery.attempts must contain at most 12 entries")
    else:
        for attempt in attempts:
            if not isinstance(attempt, Mapping) or attempt.get("role") not in {"worker", "reviewer", "explorer"} or not _route_ref_valid(attempt.get("route")) or attempt.get("resultCode") not in ROUTE_FAILURE_CATEGORIES:
                result.error("routing_recovery.attempt", "routingRecovery attempt is invalid")
                break
    return result


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
    profile = profile or {}
    if role == "manager":
        return result
    host_id = models.get("hostId")
    if not isinstance(host_id, str) or not host_id.strip():
        result.error("model.host", "v4 models.hostId is required")
        return result
    roles = (profile.get("modelRoutes") or {}).get(host_id, {})
    choices = roles.get(role) if isinstance(roles, Mapping) else None
    allowed = [route_name(item) for item in choices or [] if isinstance(item, Mapping)]
    allowed = [item for item in allowed if item]
    recovery_route = current_recovery_route(task, role)
    recovery_override = bool(recovery_route and recovery_route.get("hostId") == host_id and route_name(recovery_route) == assigned)
    if assigned not in allowed and not recovery_override:
        result.error("model.assignment", f"models.assigned is not an approved {host_id}/{role} route")
    if requested and requested != assigned and not recovery_override:
        result.error("model.pin", "models.requested must take priority over assignment")
    if recovery_route and not recovery_override:
        result.error("model.recovery_assignment", "models assignment must match the current approved recovery route")
    if recovery_override and not models.get("fallbackReason"):
        result.error("model.fallback_reason", "approved recovery assignment requires models.fallbackReason")
    if actual and actual != assigned:
        if actual not in allowed[1:]:
            result.error("model.actual", "models.actual differs from assigned and is not an explicit fallback")
        if not models.get("fallbackReason"):
            result.error("model.fallback_reason", "fallback requires models.fallbackReason")
    return result


def validate_debt(task: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    validation = task.get("validation")
    debts = validation.get("debt") if isinstance(validation, Mapping) else []
    for index, debt in enumerate(as_list(debts)):
        if not isinstance(debt, dict) or not all(debt.get(k) not in (None, "") for k in ("reason", "owner", "futureGate", "blocking")):
            result.error("validation.debt", f"validation.debt[{index}] requires reason, owner, futureGate, blocking")
    return result


def candidate_head(task: Mapping[str, Any]) -> str | None:
    commits = task.get("commits") or {}
    if not isinstance(commits, Mapping):
        return None
    fixes = as_list(commits.get("fix"))
    return str(fixes[-1]) if fixes else (str(commits.get("candidate")) if commits.get("candidate") else None)


def task_worktree(task: Mapping[str, Any]) -> str | None:
    """Return the isolated workspace path declared by the canonical task contract."""
    workspace = task.get("workspace") or {}
    value = workspace.get("path") if isinstance(workspace, Mapping) else None
    return str(value) if value else None


def validate_mode_decision(task: Mapping[str, Any], phase: Mapping[str, Any] | None = None) -> ValidationResult:
    result = ValidationResult()
    requested = task.get("requestedMode")
    resolved = task.get("resolvedMode")
    floor = task.get("modeFloor")
    risk_class = task.get("riskClass")
    reasons = task.get("modeReasons")
    degradations = task.get("capabilityDegradations")
    if requested not in MODES:
        result.error("mode.requested", f"requestedMode must be one of {sorted(MODES)}")
    if resolved not in {"simple", "standard", "strict"}:
        result.error("mode.resolved", "resolvedMode must be simple, standard, or strict")
    if floor not in {"simple", "standard", "strict"}:
        result.error("mode.floor", "modeFloor must be simple, standard, or strict")
    if risk_class not in MODE_RISK_CLASSES:
        result.error("mode.risk", f"riskClass must be one of {sorted(MODE_RISK_CLASSES)}")
    if not isinstance(reasons, list) or not reasons or any(not isinstance(item, str) or not item.strip() for item in reasons):
        result.error("mode.reasons", "modeReasons must be a non-empty string array")
    if not isinstance(degradations, list) or any(not isinstance(item, str) or not item.strip() for item in degradations):
        result.error("mode.degradations", "capabilityDegradations must be a string array")
    if requested in MODES and risk_class in MODE_RISK_CLASSES:
        expected = resolve_auto_mode(task, requested=str(requested), phase=bool(phase))
        if resolved in MODE_ORDER:
            expected_mode = expected["resolvedMode"]
            if requested == "auto" and MODE_ORDER[resolved] < MODE_ORDER[expected_mode]:
                result.error("mode.resolution", f"resolvedMode cannot be below {expected_mode} for the declared signals")
            if requested != "auto" and resolved != expected_mode:
                result.error("mode.resolution", f"resolvedMode must equal explicit pin {expected_mode}")
            if requested != "auto":
                natural = resolve_auto_mode(task, requested="auto", phase=bool(phase))["resolvedMode"]
                if MODE_ORDER[resolved] < MODE_ORDER[natural]:
                    result.error("mode.pin_unsafe", f"explicit {resolved} pin conflicts with required {natural} guarantees; ask the user")
            if floor in MODE_ORDER and MODE_ORDER[resolved] < MODE_ORDER[floor]:
                result.error("mode.downgrade", "resolvedMode cannot fall below modeFloor after mutation")
    return result


def validate_workspace_v4(task: Mapping[str, Any], workspace: Mapping[str, Any], state: str) -> ValidationResult:
    result = ValidationResult()
    backend = workspace.get("backend")
    isolated = backend in {"code-worktree", "package-worktree", "unity-clone"}
    if workspace.get("managedBy") not in {"lemmings", "user", "external"}:
        result.error("workspace.manager", "workspace.managedBy must be lemmings, user, or external")
    if workspace.get("lifetime") not in {"task", "phase", "project", "external"}:
        result.error("workspace.lifetime", "workspace.lifetime must be task, phase, project, or external")
    if isolated and state not in {"Blocked", "Cancelled"} and not workspace.get("workspaceId"):
        result.error("workspace.id", f"{backend} requires workspaceId")
    if workspace.get("path"):
        result.error("workspace.path", "v4 Task stores workspaceId, not an absolute workspace path")
    close = task.get("close") if isinstance(task.get("close"), Mapping) else {}
    if state == "Integrated":
        disposition = close.get("workspaceDisposition")
        if not isinstance(disposition, Mapping):
            result.error("workspace.disposition", "Integrated v4 Task requires close.workspaceDisposition")
        else:
            action = disposition.get("releaseAction")
            if action not in WORKSPACE_RELEASE_ACTIONS:
                result.error("workspace.disposition", f"releaseAction must be one of {sorted(WORKSPACE_RELEASE_ACTIONS)}")
            if not disposition.get("releaseReason"):
                result.error("workspace.disposition", "workspaceDisposition.releaseReason is required")
    return result


def validate_invocation(invocation: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if schema_version(invocation) != SCHEMA_VERSION:
        result.error("invocation.schema", schema_error("AgentInvocation", invocation))
        return result
    for field_name in ("runId", "taskId", "invocationId", "role", "baseSha", "profileDigest", "taskDigest", "contextDigest", "objective", "outputSchemaVersion"):
        if not invocation.get(field_name):
            result.error("invocation.missing", f"missing invocation field: {field_name}")
    if invocation.get("role") not in {"worker", "reviewer", "explorer"}:
        result.error("invocation.role", "invocation role must be worker, reviewer, or explorer")
    for field_name in ("taskRevision", "attempt"):
        value = invocation.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            result.error("invocation.counter", f"{field_name} must be a non-negative integer")
    for field_name in ("acceptanceCriteria", "ownedPaths", "forbiddenPaths", "contextRefs", "validationCommands"):
        if not isinstance(invocation.get(field_name), list):
            result.error("invocation.shape", f"{field_name} must be an array")
    context_refs = invocation.get("contextRefs")
    if isinstance(context_refs, list):
        if len(context_refs) > DEFAULT_CONTEXT_POLICY["maxWorkingSetItems"]:
            result.error("context.entries", "AgentInvocation exceeds 12 context references")
        for index, item in enumerate(context_refs):
            if not isinstance(item, Mapping) or not item.get("ref") or not item.get("purpose") or not item.get("contentHash"):
                result.error("context.reference", f"contextRefs[{index}] requires ref, purpose, and contentHash")
    encoded = json.dumps(invocation, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > DEFAULT_CONTEXT_POLICY["maxPacketBytes"]:
        result.error("context.bytes", "AgentInvocation exceeds 16 KiB")
    limits = invocation.get("limits")
    role_limits = DEFAULT_INVOCATION_LIMITS.get(str(invocation.get("role")))
    if not isinstance(limits, Mapping) or not role_limits:
        result.error("invocation.limits", "role limits are required")
    else:
        for name, maximum in role_limits.items():
            value = limits.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > maximum:
                result.error("invocation.limits", f"limits.{name} must be between 1 and {maximum}")
    return result


def validate_agent_result(
    result_value: Mapping[str, Any],
    invocation: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    current_profile_digest: str | None = None,
    current_context_digest: str | None = None,
    current_task_digest: str | None = None,
) -> ValidationResult:
    result = ValidationResult()
    if schema_version(result_value) != SCHEMA_VERSION:
        result.error("result.schema", schema_error("AgentResult", result_value))
        return result
    if result_value.get("invocationId") != invocation.get("invocationId"):
        result.error("result.invocation", "AgentResult invocationId is stale or mismatched")
    if result_value.get("attempt") != invocation.get("attempt"):
        result.error("result.attempt", "AgentResult attempt is stale or mismatched")
    if invocation.get("taskRevision") != task.get("revision"):
        result.error("result.revision", "Task revision changed after dispatch")
    if invocation.get("baseSha") != task.get("baseSha"):
        result.error("result.base", "Task base changed after dispatch")
    if current_profile_digest is not None and invocation.get("profileDigest") != current_profile_digest:
        result.error("result.profile", "profile changed after dispatch")
    if current_context_digest is not None and invocation.get("contextDigest") != current_context_digest:
        result.error("result.context", "context changed after dispatch")
    if current_task_digest is not None and invocation.get("taskDigest") != current_task_digest:
        result.error("result.task", "task content changed after dispatch")
    if result_value.get("status") not in {"succeeded", "failed", "blocked", "cancelled"}:
        result.error("result.status", "AgentResult status is invalid")
    for field_name in ("changedPaths", "acceptanceEvidence", "validationEvidence", "findings", "blockers", "remainingRisks"):
        if not isinstance(result_value.get(field_name), list):
            result.error("result.shape", f"{field_name} must be an array")
    if result_value.get("status") == "succeeded" and invocation.get("role") == "worker" and not result_value.get("candidateHead"):
        result.error("result.candidate", "successful worker result requires candidateHead")
    return result


def validate_task(task: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> ValidationResult:
    result = ValidationResult()
    if not schema_supported(task):
        result.error("task.schema", schema_error("task", task))
        return result
    revision = task.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        result.error("task.revision", "v4 Task requires non-negative integer revision")
    specialization = task.get("specialization")
    if specialization is not None and (not isinstance(specialization, str) or not specialization.strip()):
        result.error("task.specialization", "specialization must be null or a non-empty string")
    review_policy = task.get("reviewPolicy")
    if review_policy is not None and review_policy not in REVIEW_POLICIES:
        result.error("review.policy", f"reviewPolicy must be null, {sorted(REVIEW_POLICIES)}")
    cross_review_refs = task.get("crossReviewRefs", [])
    if not isinstance(cross_review_refs, list) or any(not isinstance(value, str) or not value.strip() for value in cross_review_refs):
        result.error("review.cross_refs", "crossReviewRefs must be an array of non-empty review references")
    elif len(cross_review_refs) != len(set(cross_review_refs)):
        result.error("review.cross_refs_duplicate", "crossReviewRefs must not contain duplicate references")
    if task.get("reviewRef") and task.get("reviewRef") in cross_review_refs:
        result.error("review.cross_refs_primary", "crossReviewRefs must not repeat reviewRef")
    if not isinstance(task.get("planReviewRequired", False), bool):
        result.error("review.plan_required", "planReviewRequired must be boolean")
    if task.get("planReviewRequired") and not task.get("planReviewRef"):
        result.error("review.plan_reference", "planReviewRequired requires planReviewRef")
    result.extend(validate_mode_decision(task))
    domains = task.get("ownershipDomainCount")
    if not isinstance(domains, int) or isinstance(domains, bool) or domains < 1:
        result.error("ownership.domains", "v4 Task requires positive ownershipDomainCount")
    for name in ("taskId", "goal", "acceptance", "dependencies", "risks", "state", "ownership", "models", "workspace", "workingSet", "validation", "execution", "reviewHistory", "close"):
        if name not in task or task.get(name) is None:
            result.error("task.missing", f"missing task field: {name}")
    working_set = task.get("workingSet")
    if not isinstance(working_set, list):
        result.error("task.working_set", "workingSet must be an array")
    else:
        for index, entry in enumerate(working_set):
            if not isinstance(entry, Mapping) or not isinstance(entry.get("ref"), str) or not entry.get("ref", "").strip() or not isinstance(entry.get("purpose"), str) or not entry.get("purpose", "").strip():
                result.error("task.working_set", f"workingSet[{index}] requires non-empty ref and purpose strings")
            else:
                reference_path = str(entry["ref"]).split("#", 1)[0]
                if Path(reference_path).is_absolute() or reference_path.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", reference_path):
                    result.error("task.working_set", f"workingSet[{index}].ref must be repository-relative")
    for name in ("acceptance", "dependencies", "risks", "reviewHistory"):
        if name in task and not isinstance(task.get(name), list):
            result.error("task.shape", f"{name} must be an array")
    ownership_value = task.get("ownership")
    if not isinstance(ownership_value, Mapping) or any(not isinstance(ownership_value.get(name), list) for name in ("owned", "shared", "forbidden")):
        result.error("task.ownership", "ownership requires owned, shared, and forbidden arrays")
    state = str(task.get("state", ""))
    if state not in TASK_STATES:
        result.error("state.unknown", f"unknown task state: {state}")
    if state not in {"Draft", "Planned", "Cancelled", "Superseded"}:
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
    result.extend(validate_routing_recovery(task))
    result.extend(validate_models(task, profile))
    result.extend(validate_debt(task))
    execution = task.get("execution") or {}
    if not isinstance(execution, Mapping):
        result.error("task.execution", "execution must be an object")
        execution = {}
    else:
        for name in ("interfaces", "tests", "dependencyHandoffs", "validationEvidence", "attempts", "invocations", "agentResults"):
            if not isinstance(execution.get(name), list):
                result.error("task.execution", f"execution.{name} must be an array")
    invocations = execution.get("invocations") if isinstance(execution, Mapping) else None
    if isinstance(invocations, list):
        invocation_ids: set[str] = set()
        for index, invocation in enumerate(invocations):
            if not isinstance(invocation, Mapping):
                result.error("invocation.shape", f"execution.invocations[{index}] must be an object")
                continue
            result.extend(validate_invocation(invocation))
            invocation_id = str(invocation.get("invocationId") or "")
            if invocation_id in invocation_ids:
                result.error("invocation.duplicate", f"duplicate invocationId: {invocation_id}")
            invocation_ids.add(invocation_id)
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
    ownership = task.get("ownership")
    if not isinstance(ownership, Mapping):
        ownership = {}
    if mode == "strict" and role not in {"reviewer", "explorer"} and state in {"Ready", "Active", "Candidate", "Accepted", "Integrated"} and not as_list(ownership.get("owned")):
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
    if isolated_backend and estimated is None:
        result.error("workspace.estimate", f"{backend} requires workspace.estimatedGiB")
    if isolated_backend and approval == "declined" and state != "Blocked":
        result.error("workspace.declined", "declined workspace must fall back to safe current work or set the Task to Blocked")
    if "parallelWriters" in as_list(task.get("modeReasons")) and backend == "current" and state in {"Ready", "Active", "Candidate", "Accepted", "Integrated"}:
        result.error("workspace.parallel", "parallel writers cannot share the current checkout")
    result.extend(validate_workspace_v4(task, workspace, state))
    validation = task.get("validation")
    if not isinstance(validation, Mapping):
        result.error("task.validation", "validation must be an object")
        validation = {}
    else:
        for name in ("riskToTest", "commands", "allowedOutputs", "debt"):
            if not isinstance(validation.get(name), list):
                result.error("task.validation", f"validation.{name} must be an array")
        risk_to_test = validation.get("riskToTest")
        if isinstance(risk_to_test, list):
            mapped_risks: set[str] = set()
            for index, mapping in enumerate(risk_to_test):
                if not isinstance(mapping, Mapping) or not isinstance(mapping.get("risk"), str) or not mapping.get("risk", "").strip() or not isinstance(mapping.get("test"), str) or not mapping.get("test", "").strip():
                    result.error("validation.risk_to_test", f"validation.riskToTest[{index}] requires non-empty risk and test strings")
                    continue
                mapped_risks.add(str(mapping["risk"]))
            for risk in as_list(task.get("risks")):
                if isinstance(risk, str) and risk not in mapped_risks:
                    result.error("validation.risk_unmapped", f"risk has no declared test: {risk}")
    close = task.get("close")
    if not isinstance(close, Mapping) or "mergeCommit" not in close or not isinstance(close.get("integrationEvidence"), list):
        result.error("task.close", "close requires mergeCommit and integrationEvidence array")
    if state in {"Candidate", "Accepted", "Integrated"}:
        if not task.get("baseSha"):
            result.error("commit.base_required", f"{state} requires baseSha")
        head = candidate_head(task)
        if not head:
            result.error("commit.candidate", f"{state} requires a candidate or fix commit")
        evidence = execution.get("validationEvidence")
        debt = validation.get("debt")
        if not evidence and not debt:
            result.error("validation.evidence", "candidate requires validation evidence or owned debt")
        models = task.get("models") if isinstance(task.get("models"), Mapping) else {}
        if not models.get("actual"):
            result.error("model.actual_required", f"{state} requires models.actual")
    review_required = bool(task.get("reviewRequired")) or review_policy in REVIEW_POLICIES
    if detect_mode(profile, task) == "strict":
        review_required = True
    if state in {"Accepted", "Integrated"} and review_required and not task.get("reviewRef"):
        result.error("review.reference", f"{state} requires reviewRef")
    if state in {"Accepted", "Integrated"} and review_policy == "cross" and not cross_review_refs:
        result.error("review.cross_reference", f"{state} cross review requires crossReviewRefs")
    if state in {"Accepted", "Integrated"} and review_policy == "cross":
        history = set(as_list(task.get("reviewHistory")))
        current_refs = {value for value in [task.get("reviewRef"), *cross_review_refs] if value}
        missing_history = sorted(current_refs - history)
        if missing_history:
            result.error("review.cross_history", "current cross-review references must appear in reviewHistory")
    if state == "Integrated":
        tracked_quality = isinstance(execution, Mapping) and bool(execution.get("attempts"))
        if tracked_quality:
            commits = task.get("commits") if isinstance(task.get("commits"), Mapping) else {}
            expected_heads = [commits.get("candidate"), *as_list(commits.get("fix"))]
            actual_heads = [item.get("headSha") for item in attempts or [] if isinstance(item, Mapping)]
            if actual_heads != expected_heads:
                result.error("quality.attempt_heads", "Integrated execution attempts must match the candidate/fix commit sequence")
            if review_required and any(not item.get("reviewRef") or not item.get("reviewStatus") for item in attempts or [] if isinstance(item, Mapping)):
                result.error("quality.attempt_review", "Integrated execution attempts require reviewRef and reviewStatus")
        close = task.get("close") or {}
        merge_commit = close.get("mergeCommit")
        integration_evidence = close.get("integrationEvidence") or []
        if not merge_commit or not integration_evidence:
            result.error("integration.evidence", "Integrated requires mergeCommit and integrationEvidence")
        for index, evidence in enumerate(integration_evidence):
            if not isinstance(evidence, Mapping) or evidence.get("headSha") != merge_commit or not evidence.get("command") or evidence.get("passed") is not True:
                result.error("integration.evidence", f"integrationEvidence[{index}] must pass a command at close.mergeCommit")
    return result


def validate_phase(phase: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if not schema_supported(phase):
        result.error("phase.schema", schema_error("phase", phase))
        return result
    revision = phase.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        result.error("phase.revision", "v4 Phase requires non-negative integer revision")
    workspaces = (phase.get("close") or {}).get("workspaceDispositions") if isinstance(phase.get("close"), Mapping) else None
    if not isinstance(workspaces, list):
        result.error("phase.workspaces", "v4 Phase close requires workspaceDispositions array")
    else:
        for index, disposition in enumerate(workspaces):
            if not isinstance(disposition, Mapping) or disposition.get("releaseAction") not in WORKSPACE_RELEASE_ACTIONS or not disposition.get("releaseReason"):
                result.error("phase.workspace", f"workspaceDispositions[{index}] requires releaseAction and releaseReason")
    for name in ("phaseId", "baselineSha", "integrationBranch"):
        if not phase.get(name):
            result.error("phase.missing", f"missing phase field: {name}")
    for name in ("contracts", "leases", "close"):
        if name not in phase:
            result.error("phase.missing", f"missing phase field: {name}")
    if not isinstance(phase.get("contracts"), list):
        result.error("phase.contracts", "contracts must be an array")
    if not isinstance(phase.get("leases"), list):
        result.error("phase.leases", "leases must be an array")
    close = phase.get("close")
    if not isinstance(close, Mapping) or not isinstance(close.get("mergeCommits"), list) or not isinstance(close.get("phaseValidation"), list):
        result.error("phase.close", "close requires mergeCommits and phaseValidation arrays")
    elif close.get("mergeCommits") or close.get("phaseValidation"):
        active_leases = [lease for lease in as_list(phase.get("leases")) if isinstance(lease, Mapping) and lease.get("active", True)]
        if active_leases:
            result.error("phase.lease_close", "Phase close requires every lease to be released")
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


def plan_digest(value: Mapping[str, Any]) -> str:
    """Digest only the decision-bearing plan fields."""
    if value.get("taskId"):
        names = ("taskId", "goal", "acceptance", "dependencies", "requestedMode", "resolvedMode", "riskClass", "modeReasons", "ownership", "resources", "workingSet", "risks", "validation")
    else:
        names = ("phaseId", "baselineSha", "contractsFrozen", "contracts", "taskDag", "leases")
    body = {name: value.get(name) for name in names}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_review(
    review: Mapping[str, Any],
    task: Mapping[str, Any] | None = None,
    phase: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> ValidationResult:
    result = ValidationResult()
    if not schema_supported(review):
        result.error("review.schema", schema_error("review", review))
        return result
    revision = review.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        result.error("review.revision", "v4 Review requires non-negative integer revision")
    if not review.get("reviewId"):
        result.error("review.id", "reviewId is required")
    if review.get("status") not in REVIEW_STATES:
        result.error("review.status", f"review status must be one of {sorted(REVIEW_STATES)}")
    if not review.get("reviewerModel") or not review.get("hostId"):
        result.error("review.model", "v4 Review requires hostId and actual reviewerModel")
    elif profile:
        host_id = review.get("hostId")
        choices = (((profile.get("modelRoutes") or {}).get(host_id) or {}).get("reviewer") or [])
        allowed = [route_name(item) for item in choices if isinstance(item, Mapping)]
        recovery_route = current_recovery_route(task or {}, "reviewer")
        recovery_allowed = bool(recovery_route and recovery_route.get("hostId") == host_id and route_name(recovery_route) == review.get("reviewerModel"))
        if review.get("reviewerModel") not in allowed and not recovery_allowed:
            result.error("review.model", "reviewerModel is not an approved host reviewer route")
    if not isinstance(review.get("findings"), list):
        result.error("review.findings", "review findings must be an array")
    else:
        for index, finding in enumerate(review.get("findings") or []):
            if not isinstance(finding, Mapping):
                result.error("review.finding", f"findings[{index}] must be an object")
                continue
            priority = finding.get("priority")
            if priority not in FINDING_PRIORITIES:
                result.error("review.finding_priority", f"findings[{index}].priority must be P0, P1, P2, or P3")
            origin = finding.get("origin")
            if origin not in FINDING_ORIGINS:
                result.error("review.finding_origin", f"findings[{index}].origin is invalid")
            if not finding.get("summary"):
                result.error("review.finding_summary", f"findings[{index}].summary is required")
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
    elif kind == "plan":
        owner = task if subject.get("ownerKind") == "task" else phase if subject.get("ownerKind") == "phase" else None
        owner_id = (owner or {}).get("taskId") or (owner or {}).get("phaseId")
        if not owner or subject.get("ownerId") != owner_id:
            result.error("review.plan_owner", "plan review must name its Task or Phase owner")
        elif subject.get("planDigest") != plan_digest(owner):
            result.error("review.plan_stale", "plan review digest is stale")
        if review.get("status") != "Accepted":
            result.error("review.plan_status", "plan review must be Accepted")
    else:
        result.error("review.subject", "review subject.kind must be candidate, baseline, or plan")
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
    if phase and task.get("baseSha") and phase.get("baselineSha"):
        if git(repo, "merge-base", "--is-ancestor", str(phase.get("baselineSha")), str(task.get("baseSha"))).returncode:
            result.error("commit.phase_base", "v4 task baseSha must contain the Phase baseline")
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
    profile: Mapping[str, Any] | None = None,
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
    plan_ref = task.get("planReviewRef")
    if task.get("planReviewRequired") or plan_ref:
        relative, target = canonical_evidence_path(repo, plan_ref)
        if not relative or not target or not target.is_file():
            result.error("review.plan_evidence", "planReviewRef must be an existing file inside the repository")
        else:
            try:
                evidence = read_object(target)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                result.error("review.plan_parse", f"invalid plan review evidence: {error}")
            else:
                result.extend(validate_review(evidence, task, phase, profile))
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
        cross_refs = as_list(task.get("crossReviewRefs"))
        cross_models = [route_model_identity(review.get("reviewerModel"))]
        review_paths = {embedded_relative} if embedded_relative else set()
        review_ids = {review.get("reviewId")} if review.get("reviewId") else set()
        for index, value in enumerate(cross_refs):
            relative, target = canonical_evidence_path(repo, value)
            if not relative or not target or not target.is_file():
                result.error("review.cross_evidence", f"crossReviewRefs[{index}] must be an existing file inside the repository")
                continue
            if relative in review_paths:
                result.error("review.cross_path_duplicate", f"crossReviewRefs[{index}] duplicates an existing current review path")
            review_paths.add(relative)
            try:
                evidence = read_object(target)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                result.error("review.cross_parse", f"invalid crossReviewRefs[{index}] evidence: {error}")
                continue
            evidence["_evidencePath"] = str(value)
            result.extend(validate_review(evidence, task, phase, profile))
            cross_models.append(route_model_identity(evidence.get("reviewerModel")))
            review_id = evidence.get("reviewId")
            if review_id in review_ids:
                result.error("review.cross_id_duplicate", f"crossReviewRefs[{index}] duplicates an existing reviewId")
            elif review_id:
                review_ids.add(review_id)
        if task.get("reviewPolicy") == "cross":
            distinct_models = {value for value in cross_models if value}
            if len(distinct_models) < 2:
                result.error("review.cross_models", "cross review requires two distinct provider/model identities")
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
            if role != "manager":
                result.error("ownership.shared", f"candidate changes unowned shared path: {path}")
        elif not any(path_matches(path, str(rule)) for rule in owned):
            result.error("ownership.outside", f"candidate changes path outside ownership: {path}")
    return result


def dispatchable_tasks(tasks: Iterable[Mapping[str, Any]], phase: Mapping[str, Any]) -> dict[str, Any]:
    """Return eligibility; the manager still chooses the batch."""
    task_map = {str(task.get("taskId") or ""): task for task in tasks}
    dag = {
        str(node.get("taskId")): [str(value) for value in node.get("dependencies", [])]
        for node in as_list(phase.get("taskDag")) if isinstance(node, Mapping) and node.get("taskId")
    }
    eligible: list[str] = []
    blocked: dict[str, list[str]] = {}
    for task_id, task in task_map.items():
        reasons: list[str] = []
        if task.get("state") != "Ready":
            reasons.append(f"state:{task.get('state')}")
        for dependency in dag.get(task_id, as_list(task.get("dependencies"))):
            current = task_map.get(str(dependency))
            if not current or current.get("state") != "Integrated":
                reasons.append(f"dependency:{dependency}")
        if reasons:
            blocked[task_id] = reasons
        else:
            eligible.append(task_id)
    return {"eligible": sorted(eligible), "blocked": blocked}


def validate_batch(
    repo: Path,
    tasks: Iterable[Mapping[str, Any]],
    phase: Mapping[str, Any],
    selected: Iterable[str],
    profile: Mapping[str, Any] | None = None,
    *,
    available_slots: int | None = None,
    active_writers: int = 0,
    active_readers: int = 0,
) -> ValidationResult:
    result = ValidationResult()
    task_list = list(tasks)
    task_map = {str(task.get("taskId") or ""): task for task in task_list}
    ids = [str(item) for item in selected]
    configured = int((((profile or {}).get("orchestration") or {}).get("maxConcurrentWriters") or 2))
    if not ids or len(ids) > configured:
        result.error("batch.size", f"writer batch must contain between 1 and {configured} tasks")
        return result
    if len(ids) != len(set(ids)):
        result.error("batch.duplicate", "writer batch contains duplicate task ids")
    if len(task_map) != len(task_list):
        result.error("batch.task_duplicate", "loaded tasks contain duplicate task ids")
    if available_slots is not None and 1 + active_writers + active_readers + len(ids) > available_slots:
        result.error("batch.capacity", "writer batch exceeds confirmed host slots including the manager")
    eligibility = dispatchable_tasks(task_map.values(), phase)
    for task_id in ids:
        if task_id not in task_map:
            result.error("batch.task", f"unknown batch task: {task_id}")
        elif task_id not in eligibility["eligible"]:
            reasons = ", ".join(eligibility["blocked"].get(task_id, ["not eligible"]))
            result.error("batch.ineligible", f"task {task_id} is not dispatchable: {reasons}")
    chosen = [task_map[item] for item in ids if item in task_map]
    if len(chosen) > 1 and not all(task.get("parallelReason") in {"independent_paths", "independent_validation", "critical_path_latency"} for task in chosen):
        result.error("batch.reason", "parallel tasks require a declared parallelReason")
    workspaces: set[str] = set()
    current: list[str] = []
    for task in chosen:
        result.extend(validate_task(task, profile))
        workspace = task.get("workspace") if isinstance(task.get("workspace"), Mapping) else {}
        backend = workspace.get("backend", "current")
        identity = workspace.get("workspaceId") or workspace.get("path")
        if backend == "current":
            current.append(str(task.get("taskId")))
        if identity:
            normalized = normalize_path(str(identity))
            if normalized in workspaces:
                result.error("worktree.duplicate", f"duplicate workspace: {identity}")
            workspaces.add(normalized)
    if len(current) > 1:
        result.error("workspace.parallel", f"parallel writers cannot share current checkout: {', '.join(current)}")
    for index, task in enumerate(chosen):
        left = [*as_list((task.get("ownership") or {}).get("owned")), *as_list((task.get("ownership") or {}).get("shared"))]
        left_resources = set(as_list((task.get("resources") or {}).get("exclusive")))
        for other in chosen[index + 1:]:
            right = [*as_list((other.get("ownership") or {}).get("owned")), *as_list((other.get("ownership") or {}).get("shared"))]
            if any(paths_overlap(str(a), str(b)) for a in left for b in right):
                result.error("ownership.overlap", f"owned/shared paths overlap: {task.get('taskId')} and {other.get('taskId')}")
            overlap = left_resources.intersection(as_list((other.get("resources") or {}).get("exclusive")))
            if overlap:
                result.error("lease.conflict", f"exclusive resources overlap: {', '.join(sorted(map(str, overlap)))}")
    return result

def validate_wave(
    repo: Path,
    tasks: Iterable[Mapping[str, Any]],
    phase: Mapping[str, Any],
    profile: Mapping[str, Any] | None = None,
    complete: bool = True,
    validate_tasks: bool = True,
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
        if validate_tasks:
            result.extend(validate_task(task, profile))
        task_id = str(task.get("taskId") or "")
        if detect_mode(profile, task, phase) != "strict":
            result.error("phase.task_mode", f"phase task {task_id} must use Strict mode")
        if task_id not in dag:
            result.error("phase.task_missing", f"Strict task {task_id} is absent from phase.taskDag")
        elif [str(value) for value in as_list(task.get("dependencies"))] != dag[task_id]:
            result.error("phase.dependency_drift", f"task {task_id} dependencies differ from phase.taskDag")
        workspace = task.get("workspace") if isinstance(task.get("workspace"), Mapping) else {}
        worktree = str(workspace.get("workspaceId") or "")
        dispatchable = task.get("state") in {"Ready", "Active", "Candidate", "Accepted", "Integrated"}
        role = str(task.get("role") or "worker")
        backend = str((task.get("workspace") or {}).get("backend") or "current")
        if task.get("state") == "Active" and role not in {"reviewer", "explorer"} and backend == "current":
            active_current_writers.append(task_id)
        normalized = normalize_path(worktree)
        compare_now = task.get("state") == "Active"
        if worktree and compare_now:
            if normalized in worktrees:
                result.error("worktree.duplicate", f"duplicate worktree: {worktree}")
            worktrees.add(normalized)
        if dispatchable and "externalResources" in as_list(task.get("modeReasons")) and str(task.get("taskId")) not in lease_owners.values():
            result.error("lease.required", f"external-resource task {task.get('taskId')} requires an active lease")
    if len(active_current_writers) > 1:
        result.error("workspace.parallel", f"active writers cannot share the current checkout: {', '.join(active_current_writers)}")
    conflict_tasks = [task for task in tasks if task.get("state") == "Active"]
    for index, task in enumerate(conflict_tasks):
        left = as_list((task.get("ownership") or {}).get("owned"))
        for other in conflict_tasks[index + 1:]:
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


def check_task_repository(
    repo: Path,
    profile: Mapping[str, Any] | None = None,
    task: Mapping[str, Any] | None = None,
    phase: Mapping[str, Any] | None = None,
    review: Mapping[str, Any] | None = None,
    check_all: bool = False,
) -> ValidationResult:
    result = ValidationResult()
    mode = detect_mode(profile, task, phase)
    result.data["mode"] = mode
    if not task:
        result.data["idle"] = True
        return result
    result.data["idle"] = False
    result.extend(validate_task(task, profile))
    if task.get("state") in {"Candidate", "Accepted", "Integrated"}:
        result.extend(validate_repository_commits(repo, task, phase))
        result.extend(validate_repository_ownership(repo, task))
    if mode == "strict" or check_all:
        if not phase:
            result.error("phase.required", "Strict lifecycle requires a phase artifact")
        worktree_value = task_worktree(task)
        if worktree_value:
            info = inspect_worktree(repo, resolve_path(repo, str(worktree_value)) or repo)
            result.data["worktree"] = info
            if not info["exists"]:
                result.error("worktree.missing", f"declared worktree does not exist: {worktree_value}")
            elif not info["registered"]:
                result.error("worktree.unregistered", f"declared path is not an exact Git worktree: {worktree_value}")
    if review:
        result.extend(validate_review(review, task, phase, profile))
    elif task.get("state") in {"Accepted", "Integrated"} and (
        detect_mode(profile, task, phase) == "strict" or task.get("reviewRequired") is True
    ):
        result.error("review.required", "Accepted and Integrated tasks require immutable review evidence")
    if review:
        result.extend(validate_repository_evidence(repo, task, None, review, profile))
    return result


def check_repository(
    repo: Path,
    profile: Mapping[str, Any] | None = None,
    task: Mapping[str, Any] | None = None,
    phase: Mapping[str, Any] | None = None,
    review: Mapping[str, Any] | None = None,
    check_all: bool = False,
) -> ValidationResult:
    """Validate each supplied artifact and repository surface exactly once."""
    result = ValidationResult()
    if profile:
        result.extend(validate_profile(profile))
    result.extend(check_task_repository(repo, profile, task, phase, review, check_all))
    if phase:
        if task and (detect_mode(profile, task, phase) == "strict" or check_all):
            result.extend(validate_wave(repo, [task], phase, profile, complete=False, validate_tasks=False))
        elif not task:
            result.extend(validate_phase(phase))
            result.extend(validate_repository_evidence(repo, {}, phase, None))
    if review and not task:
        result.extend(validate_review(review, phase=phase, profile=profile))
    return result
