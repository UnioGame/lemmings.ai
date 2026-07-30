"""Core contracts and validation for the orchestration plugin.

The module intentionally uses only the Python standard library.  Tracked JSON
and Markdown artifacts remain the source of truth; derived state is computed
on demand and is never written by validators.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


ADAPTERS = {"generic-markdown-v1", "autoqa-markdown-v1"}
TERMINAL_STATES = {"Integrated", "Cancelled", "Superseded", "Replan Required"}
TASK_STATES = {
    "Planned",
    "Ready",
    "Dispatched",
    "In Progress",
    "Candidate",
    "Sol Review",
    "Changes Requested",
    "Accepted",
    *TERMINAL_STATES,
    "Blocked",
}
TRANSITIONS = {
    "Planned": {"Ready", "Blocked", "Cancelled", "Superseded"},
    "Ready": {"Dispatched", "Blocked", "Cancelled", "Superseded", "Replan Required"},
    "Dispatched": {"In Progress", "Blocked", "Cancelled", "Replan Required"},
    "In Progress": {"Candidate", "Blocked", "Cancelled", "Replan Required"},
    "Candidate": {"Sol Review", "Blocked", "Replan Required"},
    "Sol Review": {"Accepted", "Changes Requested", "Replan Required"},
    "Changes Requested": {"Candidate", "Replan Required", "Cancelled"},
    "Accepted": {"Integrated", "Replan Required"},
    "Blocked": {"Ready", "In Progress", "Cancelled", "Replan Required"},
    "Integrated": set(),
    "Replan Required": set(),
    "Cancelled": set(),
    "Superseded": set(),
}
REQUIRED_PROFILE = {
    "schemaVersion",
    "taskAdapter",
    "roadmap",
    "worktreeRoot",
    "phaseBranchPattern",
    "taskBranchPattern",
    "maxAgents",
    "maxWriters",
    "integrationStrategy",
    "reviewCycles",
}


@dataclass
class Finding:
    code: str
    message: str
    path: str | None = None
    severity: str = "error"

    def as_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.path:
            result["path"] = self.path
        return result


@dataclass
class ValidationResult:
    findings: list[Finding] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

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
            "errors": sum(item.severity == "error" for item in self.findings),
            "warnings": sum(item.severity == "warning" for item in self.findings),
            "findings": [item.as_dict() for item in self.findings],
            "data": self.data,
        }


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _coerce_scalar(value: str) -> Any:
    value = value.strip().strip("`")
    if not value:
        return ""
    if value.lower() in {"true", "yes"}:
        return True
    if value.lower() in {"false", "no"}:
        return False
    if value.lower() in {"null", "none", "n/a"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def _canonical_name(name: str) -> str:
    aliases = {
        "id": "taskId",
        "task": "taskId",
        "taskid": "taskId",
        "phase": "phaseId",
        "phaseid": "phaseId",
        "phasewave": "phaseWave",
        "phasewavestate": "phaseWave",
        "wave": "waveId",
        "waveid": "waveId",
        "state": "state",
        "status": "state",
        "baseline": "baselineSha",
        "baselinesha": "baselineSha",
        "basesha": "baselineSha",
        "baselinecommit": "baselineSha",
        "reviewedbasesha": "reviewedBaseSha",
        "integrationbranch": "integrationBranch",
        "baselineaccepted": "baselineAccepted",
        "contractsfrozen": "contractsFrozen",
        "baselineandcontractfreezeapprovals": "baselineContractApprovals",
        "branch": "branch",
        "worktree": "worktree",
        "baseshabranchabsoluteworktree": "baseBranchWorktree",
        "preferredmodel": "preferredModel",
        "approvedfallback": "approvedFallback",
        "fallbackmodel": "approvedFallback",
        "selectedmodel": "selectedModel",
        "selectedruntimemodel": "selectedModel",
        "actualmodel": "actualModel",
        "actualruntimemodel": "actualModel",
        "actualmodelreasoning": "actualModel",
        "actualmodeleffort": "actualModel",
        "preferredapprovedfallbackselectedactual": "modelAssignments",
        "reviewcycle": "reviewCycle",
        "reviewcycles": "reviewCycle",
        "candidatecommit": "candidateCommit",
        "candidatecommits": "candidateCommits",
        "fixcommits": "fixCommits",
        "fixcommit": "fixCommit",
        "candidatefixcommits": "candidateFixCommits",
        "commitrange": "commitRange",
        "candidatecommitrange": "commitRange",
        "candidaterange": "commitRange",
        "verdict": "verdict",
        "reviewverdict": "reviewVerdict",
        "dependencies": "dependencies",
        "ownedpaths": "ownedPaths",
        "writepaths": "ownedPaths",
        "writeset": "ownedPaths",
        "sharedpaths": "sharedPaths",
        "sharedsetowner": "sharedPathsOwner",
        "forbiddenpaths": "forbiddenPaths",
        "integrationorder": "integrationOrder",
        "role": "role",
        "accepted": "accepted",
    }
    return aliases.get(_key(name), name.strip())


def parse_markdown(text: str, adapter: str) -> dict[str, Any]:
    if adapter not in ADAPTERS:
        raise ValueError(f"unknown adapter: {adapter}")
    result: dict[str, Any] = {"adapter": adapter}
    lists: dict[str, list[Any]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading:
            heading_text = heading.group(1)
            task_title = re.match(r"^(?:Task\s+)?([A-Za-z][A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*)\s*:", heading_text)
            prefixed_task = re.match(
                r"^(?:Handoff|Sol\s+Review)\s*:\s*([A-Za-z][A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*)",
                heading_text,
                re.IGNORECASE,
            )
            if task_title and "taskId" not in result:
                result["taskId"] = task_title.group(1)
            elif prefixed_task and "taskId" not in result:
                result["taskId"] = prefixed_task.group(1)
            phase_title = re.match(r"^Phase\s+([^:/]+)", heading_text, re.IGNORECASE)
            if phase_title and "phaseId" not in result:
                result["phaseId"] = phase_title.group(1).strip()
            current = _canonical_name(heading_text)
            continue
        field_match = re.match(r"^(?:[-*]\s+)?(?:\*\*)?([^:*`]+?)(?:\*\*)?\s*:\s*(.*?)\s*$", line)
        if field_match:
            name = _canonical_name(field_match.group(1))
            result[name] = _coerce_scalar(field_match.group(2))
            continue
        item = re.match(r"^[-*]\s+(.+)$", line)
        if item and current:
            lists.setdefault(current, []).append(_coerce_scalar(item.group(1)))
    for name, values in lists.items():
        if name not in result:
            result[name] = values
    # Both adapters support two-column contract tables used by the generic
    # templates. AutoQA additionally relies on this for compact legacy packets.
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cols = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cols) != 2 or not cols[0] or set(cols[0]) <= {"-", ":"}:
            continue
        if _key(cols[0]) in {"field", "key"}:
            continue
        result[_canonical_name(cols[0])] = _coerce_scalar(cols[1])
    phase_wave = result.pop("phaseWave", None)
    if isinstance(phase_wave, str):
        pieces = [piece.strip() for piece in phase_wave.split("/")]
        if pieces:
            result.setdefault("phaseId", pieces[0])
        if len(pieces) > 1:
            result.setdefault("waveId", pieces[1])
        if len(pieces) > 2:
            result.setdefault("state", pieces[2])
    base_branch_worktree = result.pop("baseBranchWorktree", None)
    if isinstance(base_branch_worktree, str):
        pieces = [piece.strip() for piece in base_branch_worktree.split(" / ", 2)]
        if pieces:
            result.setdefault("baselineSha", pieces[0])
        if len(pieces) > 1:
            result.setdefault("branch", pieces[1])
        if len(pieces) > 2:
            result.setdefault("worktree", pieces[2])
    assignments = result.pop("modelAssignments", None)
    if isinstance(assignments, str):
        pieces = [piece.strip() for piece in assignments.split(" / ")]
        for index, name in enumerate(
            ("preferredModel", "approvedFallback", "selectedModel", "actualModel")
        ):
            if len(pieces) > index:
                result.setdefault(name, pieces[index])
    approvals = result.get("baselineContractApprovals")
    if approvals and not str(approvals).lstrip().startswith("<"):
        result.setdefault("baselineAccepted", True)
        result.setdefault("contractsFrozen", True)
    if result.get("phaseId") and result.get("state") == "Accepted":
        result.setdefault("baselineAccepted", True)
    if isinstance(result.get("reviewCycle"), str):
        match = re.search(r"\d+", result["reviewCycle"])
        if match:
            result["reviewCycle"] = int(match.group())
    combined_commits = result.pop("candidateFixCommits", None)
    if isinstance(combined_commits, str) and combined_commits:
        separator = ".." if ".." in combined_commits else ","
        commits = [item.strip() for item in combined_commits.split(separator) if item.strip()]
        if commits:
            result.setdefault("candidateCommit", commits[0])
        if len(commits) > 1:
            result.setdefault("fixCommits", commits[1:])
    return result


def load_artifact(path: str | Path, adapter: str = "generic-markdown-v1") -> dict[str, Any]:
    artifact = Path(path)
    text = artifact.read_text(encoding="utf-8-sig")
    if artifact.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"{artifact} must contain a JSON object")
        return data
    return parse_markdown(text, adapter)


def as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip().strip("/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized.casefold() if os.name == "nt" else normalized


def paths_overlap(left: str, right: str) -> bool:
    a, b = normalize_path(left), normalize_path(right)
    if not a or not b:
        return False
    if any(token in a + b for token in ("*", "?", "[")):
        # Conservative glob check: compare the fixed prefix before a wildcard.
        a = re.split(r"[\*\?\[]", a, maxsplit=1)[0].rstrip("/")
        b = re.split(r"[\*\?\[]", b, maxsplit=1)[0].rstrip("/")
        if not a or not b:
            return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def validate_profile(profile: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    for field_name in sorted(REQUIRED_PROFILE - set(profile)):
        result.error("profile.missing", f"Missing profile field: {field_name}")
    if profile.get("schemaVersion") != 1:
        result.error("profile.schema", "schemaVersion must be 1")
    if profile.get("taskAdapter") not in ADAPTERS:
        result.error("profile.adapter", f"taskAdapter must be one of {sorted(ADAPTERS)}")
    if profile.get("integrationStrategy") not in {"no-ff", "ff-only", "squash"}:
        result.error("profile.integration", "integrationStrategy must be no-ff, ff-only, or squash")
    for name in ("maxAgents", "maxWriters", "reviewCycles"):
        value = profile.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            result.error("profile.limit", f"{name} must be a positive integer")
    if isinstance(profile.get("maxAgents"), int) and isinstance(profile.get("maxWriters"), int):
        if profile["maxWriters"] > profile["maxAgents"]:
            result.error("profile.parallelism", "maxWriters cannot exceed maxAgents")
    if isinstance(profile.get("reviewCycles"), int) and profile["reviewCycles"] > 2:
        result.error("profile.review_cycles", "reviewCycles cannot exceed 2")
    return result


def validate_phase(phase: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    for name in ("phaseId", "integrationBranch", "reviewedBaseSha", "baselineAccepted"):
        if not phase.get(name):
            result.error("phase.missing", f"Missing phase baseline field: {name}")
    if phase.get("baselineAccepted") is not True:
        result.error("phase.baseline", "Phase baseline must be accepted before dispatch")
    frozen = phase.get("contractsFrozen", phase.get("contractFreezeAccepted"))
    if frozen is not True:
        result.error("phase.contract_freeze", "Shared/public contracts must be frozen before dispatch")
    if not as_list(phase.get("phaseValidation")):
        result.warn("phase.validation", "No phase-wide validation commands declared")
    return result


def validate_transition(previous: str | None, current: str) -> ValidationResult:
    result = ValidationResult()
    if current not in TASK_STATES:
        result.error("state.unknown", f"Unknown task state: {current}")
    if previous and previous != current:
        if previous not in TASK_STATES or current not in TRANSITIONS.get(previous, set()):
            result.error("state.transition", f"Illegal transition: {previous} -> {current}")
    return result


def validate_models(task: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    preferred = task.get("preferredModel")
    fallback = task.get("approvedFallback")
    selected = task.get("selectedModel")
    actual = task.get("actualModel")
    pin = task.get("userPinnedModel")
    available = set(as_list(task.get("availableModels")))
    if not preferred:
        result.error("model.preferred", "preferredModel is required")
    if not selected:
        result.error("model.selected", "selectedModel is required")
    if pin and selected != pin:
        result.error("model.pin", f"User pin {pin!r} must take priority over selected model {selected!r}")
    allowed = {item for item in (preferred, fallback, pin) if item}
    if selected and selected not in allowed:
        result.error("model.unapproved", "selectedModel must equal preferredModel, approvedFallback, or userPinnedModel")
    if available:
        if preferred not in available and selected != fallback and not pin:
            result.error("model.fallback", "Unavailable preferred model may use only approvedFallback")
        if selected not in available:
            result.error("model.unavailable", f"Selected model is unavailable: {selected}")
    if actual and selected and actual != selected:
        result.error("model.actual", f"actualModel {actual!r} does not match selectedModel {selected!r}")
    return result


def validate_validation_debt(task: Mapping[str, Any]) -> ValidationResult:
    result = ValidationResult()
    for index, debt in enumerate(as_list(task.get("validationDebt"))):
        if not isinstance(debt, dict):
            result.error("validation_debt.shape", f"validationDebt[{index}] must be an object")
            continue
        if not debt.get("reason") or not debt.get("owner") or not debt.get("futureGate"):
            result.error(
                "validation_debt.owner",
                f"validationDebt[{index}] requires reason, owner, and futureGate",
            )
        if debt.get("blocking") not in (True, False):
            result.error("validation_debt.policy", f"validationDebt[{index}] requires boolean blocking")
    return result


def validate_task(task: Mapping[str, Any], phase: Mapping[str, Any] | None = None) -> ValidationResult:
    result = ValidationResult()
    for name in ("taskId", "phaseId", "state", "baselineSha", "branch", "worktree"):
        if not task.get(name):
            result.error("task.missing", f"Missing task field: {name}")
    result.extend(validate_transition(task.get("previousState"), str(task.get("state", ""))))
    result.extend(validate_models(task))
    result.extend(validate_validation_debt(task))
    cycle = task.get("reviewCycle", 0)
    if not isinstance(cycle, int) or isinstance(cycle, bool) or cycle < 0:
        result.error("review.cycle", "reviewCycle must be a non-negative integer")
    elif cycle > 2:
        result.error("review.replan", "More than two review cycles requires Replan Required")
    if cycle == 2 and task.get("reviewVerdict") == "changes-requested" and task.get("state") != "Replan Required":
        result.error("review.replan", "Second failed review must transition to Replan Required")
    if task.get("state") in {"Candidate", "Sol Review", "Accepted", "Integrated"}:
        commits = as_list(task.get("candidateCommits", task.get("candidateCommit")))
        if not commits:
            result.error("candidate.commit", "Candidate and later states require a candidate commit")
    if task.get("state") == "Accepted" and task.get("integrated") is True:
        result.error("state.accepted", "Accepted is distinct from Integrated")
    if task.get("state") == "Integrated" and task.get("integrationValidationPassed") is not True:
        result.error("integration.validation", "Integrated requires successful integration validation")
    if phase:
        phase_sha = phase.get("reviewedBaseSha")
        if phase_sha and task.get("baselineSha") != phase_sha:
            result.error("baseline.drift", "Task baselineSha differs from phase reviewedBaseSha")
        if task.get("phaseId") != phase.get("phaseId"):
            result.error("phase.drift", "Task phaseId differs from phase baseline")
    return result


def dependency_cycles(tasks: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    graph = {str(task.get("taskId")): [str(dep) for dep in as_list(task.get("dependencies"))] for task in tasks}
    cycles: list[list[str]] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            start = visiting.index(node)
            cycles.append(visiting[start:] + [node])
            return
        if node in visited or node not in graph:
            return
        visiting.append(node)
        for child in graph[node]:
            visit(child)
        visiting.pop()
        visited.add(node)

    for task_id in graph:
        visit(task_id)
    return cycles


def validate_task_set(tasks: Sequence[Mapping[str, Any]]) -> ValidationResult:
    result = ValidationResult()
    ids = {str(task.get("taskId")) for task in tasks if task.get("taskId")}
    for task in tasks:
        task_id = str(task.get("taskId"))
        for dependency in as_list(task.get("dependencies")):
            if str(dependency) not in ids:
                result.error("dependency.missing", f"{task_id} depends on missing task {dependency}")
    for cycle in dependency_cycles(tasks):
        result.error("dependency.cycle", "Dependency cycle: " + " -> ".join(cycle))
    for left_index, left in enumerate(tasks):
        for right in tasks[left_index + 1 :]:
            for left_path in as_list(left.get("ownedPaths")):
                for right_path in as_list(right.get("ownedPaths")):
                    if paths_overlap(str(left_path), str(right_path)):
                        result.error(
                            "paths.overlap",
                            f"{left.get('taskId')}:{left_path} overlaps {right.get('taskId')}:{right_path}",
                        )
            if left.get("branch") and left.get("branch") == right.get("branch"):
                result.error("branch.duplicate", f"Tasks share branch {left.get('branch')}")
            if left.get("worktree") and Path(str(left["worktree"])).resolve() == Path(str(right.get("worktree"))).resolve():
                result.error("worktree.duplicate", f"Tasks share worktree {left.get('worktree')}")
    return result


def validate_dispatch(
    manifest: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    phase: Mapping[str, Any] | None = None,
) -> ValidationResult:
    result = ValidationResult()
    if phase:
        result.extend(validate_phase(phase))
        if manifest.get("baselineSha") != phase.get("reviewedBaseSha"):
            result.error("dispatch.baseline", "Dispatch baseline differs from phase reviewed baseline")
    entries = manifest.get("tasks")
    if not isinstance(entries, list) or not entries:
        result.error("dispatch.tasks", "Dispatch manifest requires a non-empty tasks list")
        return result
    by_id = {str(task.get("taskId")): task for task in tasks}
    material = (
        "branch",
        "worktree",
        "preferredModel",
        "approvedFallback",
        "selectedModel",
        "baselineSha",
        "integrationOrder",
    )
    path_fields = ("ownedPaths", "sharedPaths", "forbiddenPaths", "dependencies")
    for entry in entries:
        task_id = str(entry.get("taskId"))
        task = by_id.get(task_id)
        if not task:
            result.error("dispatch.unknown_task", f"Manifest references unknown task {task_id}")
            continue
        if task.get("state") != "Ready":
            result.error("dispatch.state", f"{task_id} must be Ready for dispatch")
        for name in material:
            if entry.get(name) != task.get(name):
                result.error("dispatch.drift", f"{task_id} field {name} differs between task and manifest")
        for name in path_fields:
            if {str(value) for value in as_list(entry.get(name))} != {
                str(value) for value in as_list(task.get(name))
            }:
                result.error("dispatch.drift", f"{task_id} field {name} differs between task and manifest")
        for gate in as_list(entry.get("resourceGates")):
            if isinstance(gate, dict) and gate.get("open") is not True:
                result.error("dispatch.resource_gate", f"{task_id} has closed resource gate {gate.get('name')}")
    result.extend(validate_task_set(entries))
    max_agents = manifest.get("maxAgents")
    max_writers = manifest.get("maxWriters")
    if isinstance(max_agents, int) and len(entries) > max_agents:
        result.error("dispatch.max_agents", "Manifest exceeds maxAgents")
    writers = sum(entry.get("role", "worker") not in {"reviewer", "explorer", "validator"} for entry in entries)
    if isinstance(max_writers, int) and writers > max_writers:
        result.error("dispatch.max_writers", "Manifest exceeds maxWriters")
    return result


def _commit_values(artifact: Mapping[str, Any], singular: str, plural: str) -> list[str]:
    ignored = {"none", "n/a", "pending", "<none>", "<sha list or none>"}
    return [
        str(value)
        for value in as_list(artifact.get(plural, artifact.get(singular)))
        if value and str(value).strip().casefold() not in ignored
    ]


def validate_cross_artifacts(
    tasks: Sequence[Mapping[str, Any]],
    *,
    manifests: Sequence[Mapping[str, Any]] = (),
    handoffs: Sequence[Mapping[str, Any]] = (),
    reviews: Sequence[Mapping[str, Any]] = (),
    roadmap_text: str | None = None,
    repo: Path | None = None,
) -> ValidationResult:
    """Validate duplicated execution facts without treating snapshots as live state."""

    result = ValidationResult()
    by_id = {str(task.get("taskId")): task for task in tasks if task.get("taskId")}
    if roadmap_text is not None:
        for task_id in sorted(by_id):
            if not re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(task_id)}(?![A-Za-z0-9_.-])", roadmap_text):
                result.error("roadmap.missing_task", f"Roadmap does not contain task ID {task_id}")

    material = (
        "branch",
        "worktree",
        "preferredModel",
        "approvedFallback",
        "selectedModel",
        "baselineSha",
        "integrationOrder",
    )
    set_fields = ("ownedPaths", "sharedPaths", "forbiddenPaths", "dependencies")
    for manifest in manifests:
        entries = manifest.get("tasks")
        if not isinstance(entries, list):
            result.error("manifest.tasks", "Manifest requires a tasks list")
            continue
        for entry in entries:
            task_id = str(entry.get("taskId"))
            live = by_id.get(task_id)
            if not live:
                result.error("manifest.unknown_task", f"Manifest references unknown task {task_id}")
                continue
            # Dispatch is a historical Ready snapshot. A live task is expected
            # to advance; only a non-Ready snapshot is invalid.
            if entry.get("state") not in (None, "Ready"):
                result.error("manifest.snapshot_state", f"{task_id} manifest snapshot must be Ready")
            for name in material:
                if entry.get(name) != live.get(name):
                    result.error("manifest.drift", f"{task_id} manifest field {name} differs from live task")
            for name in set_fields:
                if {str(value) for value in as_list(entry.get(name))} != {
                    str(value) for value in as_list(live.get(name))
                }:
                    result.error("manifest.drift", f"{task_id} manifest field {name} differs from live task")

    for handoff in handoffs:
        task_id = str(handoff.get("taskId"))
        task = by_id.get(task_id)
        if not task:
            result.error("handoff.unknown_task", f"Handoff references unknown task {task_id}")
            continue
        actual = handoff.get("actualModel")
        if not actual:
            result.error("handoff.actual_model", f"{task_id} handoff requires actualModel")
        elif _key(str(task.get("actualModel") or "")) != _key(str(actual)):
            result.error("handoff.model_drift", f"{task_id} handoff actualModel differs from task")
        handoff_candidates = _commit_values(handoff, "candidateCommit", "candidateCommits")
        task_candidates = _commit_values(task, "candidateCommit", "candidateCommits")
        if handoff_candidates != task_candidates:
            result.error("handoff.commit_drift", f"{task_id} candidate commits differ between handoff and task")
        handoff_fixes = _commit_values(handoff, "fixCommit", "fixCommits")
        task_fixes = _commit_values(task, "fixCommit", "fixCommits")
        if handoff_fixes != task_fixes:
            result.error("handoff.commit_drift", f"{task_id} fix commits differ between handoff and task")

    for review in reviews:
        task_id = str(review.get("taskId"))
        task = by_id.get(task_id)
        if not task:
            result.error("review.unknown_task", f"Review references unknown task {task_id}")
            continue
        commit_range = str(review.get("commitRange") or "")
        commits = [
            *_commit_values(task, "candidateCommit", "candidateCommits"),
            *_commit_values(task, "fixCommit", "fixCommits"),
        ]
        expected_head = commits[-1] if commits else None
        explicit_range = task.get("reviewCommitRange") or task.get("commitRange")
        if not commit_range:
            result.error("review.commit_range", f"{task_id} review requires the actual commit range")
        elif explicit_range and commit_range != explicit_range:
            result.error("review.commit_drift", f"{task_id} review commit range differs from task")
        elif expected_head and commit_range.split("..")[-1].strip() != expected_head:
            result.error("review.commit_drift", f"{task_id} review range head is not the latest candidate/fix commit")
        verdict = review.get("verdict", review.get("reviewVerdict"))
        task_verdict = task.get("reviewVerdict")
        if not verdict:
            result.error("review.verdict", f"{task_id} review requires a verdict")
        elif task_verdict and str(verdict).casefold() != str(task_verdict).casefold():
            result.error("review.verdict_drift", f"{task_id} review verdict differs from task")
        elif task.get("state") in {"Accepted", "Integrated"} and str(verdict).casefold() not in {
            "accepted",
            "approve",
            "approved",
        }:
            result.error("review.verdict_drift", f"{task_id} accepted task has non-accepting review verdict")

    bindings: list[dict[str, Any]] = []
    seen_branches: dict[str, str] = {}
    seen_worktrees: dict[str, str] = {}
    for task_id, task in by_id.items():
        branch = str(task.get("branch") or "")
        worktree_value = str(task.get("worktree") or "")
        worktree = Path(worktree_value).resolve() if worktree_value else None
        branch_exists: bool | None = None
        if repo and branch:
            branch_exists = run_git(repo, "rev-parse", "--verify", "--quiet", branch).returncode == 0
        worktree_exists = worktree.exists() if worktree else False
        terminal = task.get("state") in TERMINAL_STATES or task.get("state") == "Accepted"
        if branch:
            if branch in seen_branches:
                result.error("branch.duplicate", f"{task_id} and {seen_branches[branch]} share branch {branch}")
            seen_branches[branch] = task_id
            if branch_exists is False:
                (result.warn if terminal else result.error)(
                    "branch.missing", f"{task_id} branch does not exist: {branch}"
                )
        if worktree:
            worktree_key = str(worktree)
            if worktree_key in seen_worktrees:
                result.error(
                    "worktree.duplicate",
                    f"{task_id} and {seen_worktrees[worktree_key]} share worktree {worktree}",
                )
            seen_worktrees[worktree_key] = task_id
            if not worktree_exists:
                (result.warn if terminal else result.error)(
                    "worktree.missing", f"{task_id} worktree does not exist: {worktree}"
                )
        bindings.append(
            {
                "taskId": task_id,
                "branch": branch or None,
                "branchExists": branch_exists,
                "worktree": str(worktree) if worktree else None,
                "worktreeExists": worktree_exists,
            }
        )
    result.data["bindings"] = bindings
    return result


def topological_order(tasks: Sequence[Mapping[str, Any]]) -> list[str]:
    by_id = {str(task["taskId"]): task for task in tasks}
    pending = set(by_id)
    result: list[str] = []
    while pending:
        ready = sorted(
            task_id
            for task_id in pending
            if all(str(dep) in result for dep in as_list(by_id[task_id].get("dependencies")))
        )
        if not ready:
            raise ValueError("dependency graph contains a cycle or missing dependency")
        result.extend(ready)
        pending.difference_update(ready)
    return result


def build_dispatch(
    profile: Mapping[str, Any],
    phase: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    wave_id: str,
) -> tuple[dict[str, Any], ValidationResult]:
    result = validate_phase(phase)
    result.extend(validate_task_set(tasks))
    selected = [dict(task) for task in tasks if str(task.get("waveId")) == str(wave_id)]
    if not selected:
        result.error("wave.empty", f"No tasks found for wave {wave_id}")
    for task in selected:
        if task.get("state") != "Ready":
            result.error("wave.state", f"{task.get('taskId')} must be Ready")
        if task.get("baselineSha") != phase.get("reviewedBaseSha"):
            result.error("wave.baseline", f"{task.get('taskId')} baseline differs from phase")
    try:
        order = topological_order(tasks)
    except ValueError as error:
        result.error("dependency.order", str(error))
        order = []
    selected.sort(key=lambda task: order.index(str(task["taskId"])) if str(task["taskId"]) in order else 10**6)
    manifest = {
        "schemaVersion": 1,
        "phaseId": phase.get("phaseId"),
        "waveId": wave_id,
        "baselineSha": phase.get("reviewedBaseSha"),
        "maxAgents": profile.get("maxAgents"),
        "maxWriters": profile.get("maxWriters"),
        "integrationStrategy": profile.get("integrationStrategy"),
        "tasks": selected,
    }
    return manifest, result


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git_worktrees(repo: Path) -> list[dict[str, Any]]:
    process = run_git(repo, "worktree", "list", "--porcelain")
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or "git worktree list failed")
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in process.stdout.splitlines() + [""]:
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    for entry in entries:
        worktree = Path(str(entry["worktree"]))
        status = run_git(worktree, "status", "--porcelain")
        entry["clean"] = status.returncode == 0 and not status.stdout.strip()
        entry["exists"] = worktree.exists()
    return entries


def cleanup_inventory(repo: Path, tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    task_by_worktree = {
        str(Path(str(task["worktree"])).resolve()): task for task in tasks if task.get("worktree")
    }
    inventory: list[dict[str, Any]] = []
    for item in git_worktrees(repo):
        resolved = str(Path(str(item["worktree"])).resolve())
        task = task_by_worktree.get(resolved)
        state = task.get("state") if task else None
        stale = bool(task and state in {"Accepted", "Integrated", "Cancelled", "Superseded"} and item["clean"])
        row = {
            **item,
            "taskId": task.get("taskId") if task else None,
            "taskState": state,
            "stale": stale,
            "recommendation": None,
        }
        if stale:
            row["recommendation"] = f"git -C {repo} worktree remove -- {item['worktree']}"
        inventory.append(row)
    return inventory


def routing_scorecard(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    for task in tasks:
        model = str(task.get("actualModel") or task.get("selectedModel") or "unknown")
        bucket = by_model.setdefault(
            model,
            {
                "tasks": 0,
                "acceptedOrIntegrated": 0,
                "replans": 0,
                "fixCycles": 0,
                "tokens": 0,
                "latencyMs": 0,
                "paidCost": 0.0,
                "validationFailures": 0,
                "escapedDefects": 0,
                "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
            },
        )
        bucket["tasks"] += 1
        bucket["acceptedOrIntegrated"] += task.get("state") in {"Accepted", "Integrated"}
        bucket["replans"] += task.get("state") == "Replan Required"
        bucket["fixCycles"] += int(task.get("reviewCycle", 0) or 0)
        bucket["tokens"] += int(task.get("tokens", 0) or 0)
        bucket["latencyMs"] += int(task.get("latencyMs", 0) or 0)
        bucket["paidCost"] += float(task.get("paidCost", 0) or 0)
        bucket["validationFailures"] += int(task.get("validationFailures", 0) or 0)
        bucket["escapedDefects"] += int(task.get("escapedDefects", 0) or 0)
        for priority, count in (task.get("findings") or {}).items():
            if priority in bucket["findings"]:
                bucket["findings"][priority] += int(count)
    return {"models": by_model}
