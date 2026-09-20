"""Manager-directed review operations; tools own bindings, agents own verdicts."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .budget import new_task_budget
from .contracts import (
    as_list, candidate_head, canonical_evidence_path, git, path_matches, plan_digest, read_object,
    review_digest, validate_review, write_object,
)
from .effective import capture_effective
from .invocations import (
    _candidate_review_spec, _review_basis, _review_lane, accept_result, apply_review,
    find_invocation, normalize_result, record_invocation, result_findings, task_lock, validate_dispatch,
)
from .readiness import prepare_candidate, validate_candidate_readiness


class ReviewWorkflowError(ValueError):
    def __init__(self, message: str, kind: str = "artifact"):
        super().__init__(message)
        self.kind = kind

    def as_dict(self) -> dict[str, Any]:
        actions = {
            "artifact": "Correct the report fields and resubmit the same invocation; do not rerun code review.",
            "evidence": "Inspect the named changed input; refresh only invalid evidence. Do not rewrite saved bindings.",
            "conflict": "Read the saved operation result; do not overwrite immutable evidence or dispatch a duplicate.",
        }
        return {"ok": False, "kind": self.kind, "error": str(self), "nextAction": actions[self.kind]}


def start_review(repo: Path, task_path: Path, profile: Mapping[str, Any], *,
                 expected_head: str, review_lane: str | None = None) -> dict[str, Any]:
    """Prepare and reserve once; a repeat returns the original saved dispatch."""
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("state") != "Candidate" or candidate_head(task) != expected_head:
            raise ReviewWorkflowError("review start requires the specified current Candidate head", "evidence")
        before = plan_digest(task)
        capture_effective(repo, task, profile)
        if not task.get("budget"):
            task["budget"] = new_task_budget(profile)
        if plan_digest(task) != before:
            task["revision"] += 1
            write_object(task_path, task)
        prepared = prepare_candidate(repo, task_path, expected_revision=task["revision"], expected_head=expected_head)
        if not prepared["ok"]:
            return {"ok": False, "kind": "validation", "revision": prepared["revision"],
                    "findings": prepared.get("findings", []),
                    "nextAction": "Inspect stored candidateReadiness and fix the failing check before review."}
        task = read_object(task_path)
        lane = _review_lane(task, profile, explicit=review_lane)
        basis = _review_basis(_candidate_review_spec(task, lane), expected_head)
        execution = task.get("execution") or {}
        for prior in as_list(execution.get("invocations")):
            if (not isinstance(prior, Mapping) or prior.get("role") != "reviewer"
                    or prior.get("reviewSubjectKind") != "candidate" or prior.get("reviewLane") != lane
                    or prior.get("candidateHead") != expected_head
                    or _review_basis(prior.get("reviewSpec") or {}, expected_head) != basis):
                continue
            settled = any(item.get("invocationId") == prior["invocationId"]
                          for item in as_list(execution.get("agentResults")) + as_list(execution.get("routeFailures"))
                          if isinstance(item, Mapping))
            if not settled:
                validate_dispatch(repo, task, profile, prior, task_path=task_path)
            return {"ok": True, "reused": True, "status": "result-recorded" if settled else "reserved",
                    "revision": task["revision"], "invocationId": prior["invocationId"],
                    "invocation": dict(prior), "nextAction": "Use the saved result." if settled else "Resume the saved invocation; do not dispatch it twice."}
        attempt = 1 + sum(1 for item in as_list(execution.get("invocations"))
                          if isinstance(item, Mapping) and item.get("role") == "reviewer")
        invocation = record_invocation(repo, task_path, profile, "reviewer", attempt, task["revision"],
                                       subject_kind="candidate", review_lane=lane, freeze=True)
        return {"ok": True, "reused": False, "status": "reserved", "revision": invocation["taskRevision"],
                "invocationId": invocation["invocationId"], "invocation": invocation}


def _result_content(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "usage"}


def submit_review(repo: Path, task_path: Path, profile: Mapping[str, Any], report: Mapping[str, Any],
                  review_path: Path, *, host_receipt: Mapping[str, Any] | None = None,
                  invocation_id: str | None = None) -> dict[str, Any]:
    """Build immutable evidence and apply a verdict without model-authored bindings.

    Shape errors have no effects. After interruption, the same input resumes from
    the persisted Review/AgentResult without charging or applying twice.
    """
    with task_lock(task_path):
        task = read_object(task_path)
        invocation = find_invocation(task, invocation_id or str(report.get("invocationId") or ""))
        if not invocation or invocation.get("role") != "reviewer" or invocation.get("reviewSubjectKind") != "candidate":
            raise ReviewWorkflowError("report requires a saved candidate reviewer invocationId")
        spec = invocation.get("reviewSpec")
        if not isinstance(spec, Mapping):
            raise ReviewWorkflowError("saved invocation has no reviewSpec", "evidence")
        report = normalize_result(repo, invocation, report)
        if report.get("invocationId") != invocation["invocationId"]:
            raise ReviewWorkflowError("reported invocationId differs from the selected invocation", "evidence")
        expected_host = invocation.get("assignedHost") or spec.get("reviewerHost") or "native"
        expected_model = invocation.get("assignedModel") or spec.get("reviewerModel") or "current-host/default"
        if report.get("hostId") not in (None, expected_host):
            raise ReviewWorkflowError("review report hostId differs from the saved reviewer invocation", "evidence")
        if report.get("reviewerModel") not in (None, expected_model):
            raise ReviewWorkflowError("review report reviewerModel differs from the saved reviewer invocation", "evidence")
        report["hostId"] = expected_host
        report["reviewerModel"] = expected_model
        if isinstance(report.get("findings"), list):
            findings = []
            for item in report["findings"]:
                if not isinstance(item, Mapping):
                    findings.append(item)
                    continue
                finding = dict(item)
                if finding.get("id") and finding.get("findingId") and finding["id"] != finding["findingId"]:
                    raise ReviewWorkflowError("finding id and findingId disagree")
                finding.setdefault("origin", "implementation")
                identity = json.dumps(finding, sort_keys=True, ensure_ascii=False).encode()
                finding.setdefault("findingId", finding.get("id") or "finding-" + hashlib.sha256(identity).hexdigest()[:16])
                finding.pop("id", None)
                findings.append(finding)
            report["findings"] = findings
        relative, target = canonical_evidence_path(repo, review_path)
        if not relative or target == task_path.resolve():
            raise ReviewWorkflowError("review output must be inside the repository and distinct from Task")
        allowed = as_list((task.get("validation") or {}).get("allowedOutputs"))
        if not any(path_matches(relative, str(rule)) for rule in allowed) and git(repo, "check-ignore", "-q", "--", relative).returncode:
            raise ReviewWorkflowError("choose a Review output in an existing allowed or Git-ignored artifact directory")
        result = {key: copy.deepcopy(value) for key, value in report.items()
                  if key not in {"verdict", "hostId", "reviewerModel", "findingDispositions"}}
        if result.get("status") != "succeeded":
            raise ReviewWorkflowError("submit a completed review; execution failures use invocation fail")
        if report.get("verdict") not in {"Accepted", "ChangesRequested"}:
            raise ReviewWorkflowError("report.verdict must be Accepted or ChangesRequested")
        if report.get("verdict") == "Accepted" and result.get("blockers"):
            raise ReviewWorkflowError("Accepted report cannot contain blockers")
        if result.get("candidateHead") not in (None, spec.get("candidateHead")):
            raise ReviewWorkflowError("reported candidate head differs from the saved invocation", "evidence")
        review = {
            "schemaVersion": 4, "revision": 0,
            "reviewId": "review-" + hashlib.sha256(str(invocation["invocationId"]).encode()).hexdigest()[:24],
            "subject": {"kind": "candidate", "taskId": task["taskId"],
                        "baseSha": spec.get("fullBaseSha"), "headSha": spec.get("candidateHead")},
            "status": report["verdict"], "hostId": expected_host, "reviewerModel": expected_model,
            "cycle": len(as_list((task.get("execution") or {}).get("repairHistory"))) + 1,
            "reviewSpec": dict(spec), "findings": copy.deepcopy(report.get("findings")),
            "validation": copy.deepcopy(report.get("validationEvidence")),
            "findingDispositions": copy.deepcopy(report.get("findingDispositions", {})),
        }
        checked_review = {**review, "_evidencePath": relative}
        if spec.get("mode") == "delta":
            previous_ref, previous_path = canonical_evidence_path(repo, spec.get("previousReviewRef"))
            if not previous_ref or not previous_path.is_file():
                raise ReviewWorkflowError("immutable previous review is missing", "evidence")
            checked_review["previousReview"] = read_object(previous_path)
        checked = validate_review(checked_review, task, profile=profile, applying=True)
        if not checked.ok:
            finding = checked.findings[0]
            artifact_codes = {"review.findings", "review.finding", "review.finding_priority", "review.finding_origin",
                              "review.finding_summary", "review.finding_id", "review.finding_duplicate", "review.validation",
                              "review.model", "review.spec_identity", "review.acceptance", "review.blocker_required",
                              "review.delta_dispositions", "review.delta_unresolved", "review.delta_findings"}
            raise ReviewWorkflowError(finding.message, "artifact" if finding.code in artifact_codes else "evidence")
        execution = task.get("execution") or {}
        active = execution.get("activeRepair") or {}
        if active.get("status") == "open":
            dispositions = review["findingDispositions"]
            if not isinstance(dispositions, Mapping) or set(active.get("targetFindingIds") or []) - set(dispositions):
                raise ReviewWorkflowError("report needs a disposition for every active repair target")
            for finding_id in active.get("targetFindingIds") or []:
                value = dispositions[finding_id]
                if isinstance(value, Mapping):
                    value = value.get("disposition") or value.get("status") or value.get("state")
                value = str(value or "").strip().lower()
                if value not in {"resolved", "remaining", "regressed"} or (review["status"] == "Accepted" and value != "resolved"):
                    raise ReviewWorkflowError("active repair dispositions must be resolved, remaining, or regressed; acceptance requires resolved")
        digest = review_digest(review)
        if target.exists() and read_object(target) != review:
            raise ReviewWorkflowError("review output already contains different immutable evidence", "conflict")
        applications = as_list(execution.get("reviewApplications"))
        if any((item.get("reviewSpec") or {}).get("invocationId") == invocation["invocationId"]
               and item.get("reviewRef") != relative for item in applications):
            raise ReviewWorkflowError("this invocation is already applied at another reviewRef; reuse that path", "conflict")
        applied = next((item for item in applications if item.get("reviewRef") == relative and item.get("digest") == digest), None)
        recorded = [item for item in as_list(execution.get("agentResults")) if item.get("invocationId") == invocation["invocationId"]]
        if recorded and (len(recorded) != 1 or _result_content(recorded[0]) != _result_content(result)):
            raise ReviewWorkflowError("invocation already has a different recorded result", "conflict")
        if applied:
            if not target.is_file() or not recorded:
                raise ReviewWorkflowError("applied review evidence is missing", "evidence")
            return {"ok": True, "reused": True, "state": task["state"], "revision": task["revision"], "reviewRef": relative}
        # Keep formatting corrections cheap, but never rebind a stale candidate.
        readiness = validate_candidate_readiness(repo, task, task_path=task_path)
        if not readiness.ok:
            raise ReviewWorkflowError(readiness.findings[0].message, "evidence")
        if not recorded:
            checked = result_findings(repo, task, profile, result)
            if not checked.ok:
                finding = checked.findings[0]
                kind = "artifact" if finding.code in {"result.schema", "result.attempt", "result.status", "result.shape", "result.usage"} else "evidence"
                raise ReviewWorkflowError(finding.message, kind)
        # Validate everything above before creating the immutable artifact.
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(review, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
        if not recorded:
            accept_result(repo, task_path, profile, result, task["revision"], trusted_usage=host_receipt)
            task = read_object(task_path)
        return apply_review(repo, task_path, target, expected_revision=task["revision"], profile=profile)
