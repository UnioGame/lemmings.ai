"""High-level, idempotent delivery flow for schema-v5 owners.

The facade composes the low-level primitives.  It makes only deterministic
lifecycle decisions; scope, routes, verdicts and acceptance stay manager-owned.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .budget import new_task_budget, reserve_tool_calls, settle_tool_calls
from .contracts import SCHEMA_VERSION, as_list, git, plan_digest, read_object, review_digest, resolve_auto_mode, validate_phase, validate_review, validate_task, validate_wave, write_object
from .invocations import find_invocation, record_invocation, record_route_failure, start_repair, task_lock
from .review_workflow import start_review, submit_review
from .readiness import prepare_candidate, validation_digest
from .task_workflow import _validate_brief, prepare_task, submit_candidate

PHASE_BRIEF_VERSION = 1
ACTION_TYPES = {"manager-implement", "dispatch-worker", "dispatch-reviewer", "integrate", "manager-decision", "user-input", "replan-required", "blocked", "complete"}
TERMINAL = {"Integrated", "Cancelled", "Superseded"}


def _relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("owner path must be inside the repository") from error


def _action(kind: str, **values: Any) -> dict[str, Any]:
    if kind not in ACTION_TYPES:
        raise ValueError(f"unknown flow action: {kind}")
    return {"type": kind, **values}


def _result(owner: Mapping[str, Any], actions: list[dict[str, Any]], *, ok: bool = True) -> dict[str, Any]:
    return {"ok": ok, "status": owner.get("state"), "revision": owner.get("revision"), "actions": actions}


def _pending(task: Mapping[str, Any], role: str | None = None) -> list[Mapping[str, Any]]:
    execution = task.get("execution") if isinstance(task.get("execution"), Mapping) else {}
    settled = {item.get("invocationId") for name in ("agentResults", "routeFailures") for item in as_list(execution.get(name)) if isinstance(item, Mapping)}
    return [item for item in as_list(execution.get("invocations")) if isinstance(item, Mapping) and item.get("invocationId") not in settled and (role is None or item.get("role") == role)]


def _dispatch(invocation: Mapping[str, Any]) -> dict[str, Any]:
    role = str(invocation.get("role"))
    return _action("dispatch-reviewer" if role == "reviewer" else "dispatch-worker", invocationId=invocation.get("invocationId"), invocation=dict(invocation))


def _write_task_state(path: Path, task: dict[str, Any], state: str) -> dict[str, Any]:
    task["previousState"] = task.get("state")
    task["state"] = state
    task["revision"] = int(task.get("revision", 0)) + 1
    task.setdefault("owner", {}).update({"kind": "task", "id": task.get("taskId"), "revision": task["revision"]})
    write_object(path, task)
    return task


def _review_lanes(task: Mapping[str, Any]) -> list[str | None]:
    if task.get("reviewPolicy") != "cross":
        return [None]
    assignments = task.get("roleAssignments") if isinstance(task.get("roleAssignments"), Mapping) else {}
    routes = [assignments.get("reviewer"), task.get("reviewerRecovery")]
    lanes: list[str] = []
    for route in routes:
        if isinstance(route, Mapping):
            host = str(route.get("hostId") or "native")
            model = "/".join(str(route.get(name)) for name in ("providerId", "modelId") if route.get(name)) or "current-host/default"
            lane = f"{host}::{model}"
            if lane not in lanes:
                lanes.append(lane)
    return lanes[:2] if len(lanes) >= 2 else lanes[:1]


def advance_task(repo: Path, task_path: Path, profile: Mapping[str, Any], *, candidate_head: str | None = None) -> dict[str, Any]:
    task = read_object(task_path)
    pending = _pending(task)
    if pending:
        return _result(task, [_dispatch(item) for item in pending])
    state = task.get("state")
    if state == "Ready":
        if not task.get("workerRequired", True):
            if not candidate_head:
                return _result(task, [_action("manager-implement", taskId=task.get("taskId"))])
            resolved = git(repo, "rev-parse", "--verify", f"{candidate_head}^{{commit}}")
            live = git(repo, "rev-parse", "HEAD").stdout.strip()
            if resolved.returncode or resolved.stdout.strip() != live:
                raise ValueError("manager candidate must be the exact current HEAD")
            with task_lock(task_path):
                current = read_object(task_path)
                current.setdefault("commits", {})["candidate"] = live
                current = _write_task_state(task_path, current, "Candidate")
            prepared = prepare_candidate(repo, task_path, expected_revision=int(current["revision"]), expected_head=live)
            if not prepared.get("ok"):
                return {**prepared, "actions": [_action("manager-decision", reason="manager candidate readiness failed")]}
            return advance_task(repo, task_path, profile, candidate_head=live)
        with task_lock(task_path):
            task = _write_task_state(task_path, read_object(task_path), "Active")
        state = "Active"
    if state == "Active":
        worker_results = [item for item in as_list((task.get("execution") or {}).get("agentResults")) if isinstance(item, Mapping)]
        worker_invocations = [item for item in as_list((task.get("execution") or {}).get("invocations")) if isinstance(item, Mapping) and item.get("role") == "worker"]
        if worker_results and worker_invocations:
            return _result(task, [_action("blocked", reason="active worker result was recorded without candidate promotion")], ok=False)
        attempt = 1 + len(worker_invocations)
        invocation = record_invocation(repo, task_path, profile, "worker", attempt, int(task["revision"]), dispatch_kind="initial", freeze=True)
        task = read_object(task_path)
        return _result(task, [_dispatch(invocation)])
    if state == "Repair":
        active = ((task.get("execution") or {}).get("activeRepair"))
        if not isinstance(active, Mapping) or active.get("status") != "open":
            return _result(task, [_action("replan-required", reason="repair has no authorized target set")], ok=False)
        attempt = 1 + sum(1 for item in as_list((task.get("execution") or {}).get("invocations")) if isinstance(item, Mapping) and item.get("role") == "worker")
        invocation = record_invocation(repo, task_path, profile, "worker", attempt, int(task["revision"]), dispatch_kind="repair", repair_cycle=int(active["cycle"]), freeze=True)
        task = read_object(task_path)
        return _result(task, [_dispatch(invocation)])
    if state == "Candidate":
        head = candidate_head or ((task.get("commits") or {}).get("fix") or [None])[-1] or (task.get("commits") or {}).get("candidate")
        if not task.get("reviewRequired") and task.get("resolvedMode") != "strict":
            with task_lock(task_path):
                task = _write_task_state(task_path, read_object(task_path), "Accepted")
            return _result(task, [_action("integrate", taskId=task.get("taskId"), candidateHead=head)])
        lanes = _review_lanes(task)
        existing = {item.get("reviewLane") for item in as_list((task.get("execution") or {}).get("invocations")) if isinstance(item, Mapping) and item.get("role") == "reviewer" and item.get("candidateHead") == head}
        actions: list[dict[str, Any]] = []
        for lane in lanes:
            if lane in existing:
                continue
            started = start_review(repo, task_path, profile, expected_head=str(head), review_lane=lane)
            if started.get("invocation"):
                actions.append(_dispatch(started["invocation"]))
        task = read_object(task_path)
        if actions:
            return _result(task, actions)
        if task.get("reviewPolicy") == "cross" and len(lanes) < 2:
            task.setdefault("capabilityDegradations", []).append("cross-review-unavailable")
            task["reviewPolicy"] = "single"
            task["revision"] += 1
            write_object(task_path, task)
            return advance_task(repo, task_path, profile, candidate_head=str(head))
        return _result(task, [_action("manager-decision", reason="candidate review wave is recorded; inspect its results")])
    if state == "Accepted":
        return _result(task, [_action("integrate", taskId=task.get("taskId"), candidateHead=((task.get("commits") or {}).get("fix") or [None])[-1] or (task.get("commits") or {}).get("candidate"))])
    if state == "Integrated":
        return _result(task, [_action("complete", taskId=task.get("taskId"))])
    if state == "Replan Required":
        return _result(task, [_action("replan-required", reason=((task.get("execution") or {}).get("repairDecision") or {}).get("reason") or "owner requires manager replan")], ok=False)
    return _result(task, [_action("blocked", reason=f"owner state {state} cannot advance")], ok=False)


def _review_output(repo: Path, task_path: Path, invocation_id: str) -> Path:
    path = task_path.parent / "reviews" / f"{task_path.stem}-{invocation_id}.json"
    relative = _relative(repo, path)
    task = read_object(task_path)
    outputs = task.setdefault("validation", {}).setdefault("allowedOutputs", [])
    rule = str(Path(relative).parent.as_posix()) + "/**"
    if rule not in outputs:
        raise ValueError("review output directory was not frozen before reviewer dispatch")
    return path


def submit_task(repo: Path, task_path: Path, profile: Mapping[str, Any], invocation_id: str, payload: Mapping[str, Any], *, failure: bool = False, host_receipt: Mapping[str, Any] | None = None) -> dict[str, Any]:
    task = read_object(task_path)
    invocation = find_invocation(task, invocation_id)
    if invocation is None:
        raise ValueError("flow submit requires an invocation bound to this owner")
    if failure:
        output = record_route_failure(task_path, failure_value=payload, expected_revision=int(task["revision"]), host_receipt=host_receipt)
        task = read_object(task_path)
        return {**_result(task, [_action("manager-decision", reason="route failure recorded")], ok=False), "operation": output}
    if invocation.get("role") == "worker":
        output = submit_candidate(repo, task_path, profile, payload, invocation_id=invocation_id, host_receipt=host_receipt)
        if not output.get("ok"):
            return {**output, "actions": [_action("manager-decision", reason=output.get("nextAction"))]}
        return {**advance_task(repo, task_path, profile), "operation": output}
    if invocation.get("role") != "reviewer":
        raise ValueError("flow submit supports worker and reviewer results")
    review_path = _review_output(repo, task_path, invocation_id)
    output = submit_review(repo, task_path, profile, payload, review_path, host_receipt=host_receipt, invocation_id=invocation_id)
    task = read_object(task_path)
    if task.get("state") == "Repair" and not isinstance((task.get("execution") or {}).get("activeRepair"), Mapping):
        review = read_object(review_path)
        review["_evidencePath"] = _relative(repo, review_path)
        output["repair"] = start_repair(task_path, expected_revision=int(task["revision"]), progress="", plan="Repair the blocking findings from the immutable review.", review=review, review_ref=review["_evidencePath"])
    return {**advance_task(repo, task_path, profile), "operation": output}


def finish_task(repo: Path, task_path: Path) -> dict[str, Any]:
    integration_failure = None
    failure_revision = None
    with task_lock(task_path):
        task = read_object(task_path)
        if task.get("state") == "Integrated":
            return _result(task, [_action("complete", taskId=task.get("taskId"))])
        if task.get("state") != "Accepted":
            raise ValueError("flow finish requires an Accepted Task")
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        candidate = ((task.get("commits") or {}).get("fix") or [None])[-1] or (task.get("commits") or {}).get("candidate")
        if not head or not candidate or git(repo, "merge-base", "--is-ancestor", str(candidate), head).returncode:
            raise ValueError("integration HEAD must contain the accepted candidate")
        evidence = []
        for command in as_list((task.get("validation") or {}).get("commands")):
            process = subprocess.run(str(command), cwd=repo, shell=True, capture_output=True, text=True, check=False)
            evidence.append({"headSha": head, "command": str(command), "passed": process.returncode == 0, "exitCode": process.returncode, "diagnostics": {"tail": (process.stdout + process.stderr)[-4096:]}})
        if not evidence or not all(item["passed"] for item in evidence):
            failure = evidence or [{"passed": False, "reason": "no validation commands"}]
            evidence_digest = hashlib.sha256(json.dumps(failure, sort_keys=True).encode()).hexdigest()
            finding_id = "integration-" + evidence_digest[:12]
            readiness = (task.get("execution") or {}).get("candidateReadiness") or {}
            predecessor = {
                "schemaVersion": SCHEMA_VERSION, "revision": 0,
                "reviewId": "review-integration-" + evidence_digest[:16],
                "subject": {"kind": "candidate", "taskId": task.get("taskId"), "baseSha": task.get("baseSha"), "headSha": head},
                "status": "ChangesRequested", "hostId": "runtime", "reviewerModel": "deterministic/integration-validator",
                "cycle": len(as_list((task.get("execution") or {}).get("repairHistory"))) + 1,
                "reviewSpec": {"mode": "full", "fullBaseSha": task.get("baseSha"), "candidateHead": head,
                               "readinessDigest": readiness.get("digest"), "planDigest": None,
                               "validationDigest": validation_digest(task), "previousReviewRef": None,
                               "previousReviewDigest": None, "previousHead": None, "findingIds": []},
                "findingDispositions": {},
                "findings": [{"findingId": finding_id, "priority": "P1", "origin": "implementation", "summary": "Integration validation failed"}],
                "validation": copy.deepcopy(failure),
            }
            review_path = task_path.parent / "reviews" / f"{task_path.stem}-integration-{evidence_digest[:16]}.json"
            if review_path.exists() and read_object(review_path) != predecessor:
                raise ValueError("integration review artifact already contains different immutable evidence")
            if not review_path.exists():
                write_object(review_path, predecessor)
            predecessor_digest = review_digest(predecessor)
            integration_failure = {"headSha": head, "digest": predecessor_digest, "reviewRef": _relative(repo, review_path), "targetFindingIds": [finding_id], "evidence": failure, "evidenceDigest": evidence_digest}
            task.setdefault("execution", {})["integrationFailure"] = integration_failure
            task["revision"] += 1
            failure_revision = task["revision"]
            write_object(task_path, task)
        else:
            task.setdefault("close", {}).update({"mergeCommit": head, "integrationEvidence": evidence, "workspaceDisposition": {"releaseAction": "current" if (task.get("workspace") or {}).get("backend") == "current" else "retained", "releaseReason": "integration validation passed"}})
            task = _write_task_state(task_path, task, "Integrated")
            return _result(task, [_action("complete", taskId=task.get("taskId"))])
    repair = start_repair(task_path, expected_revision=int(failure_revision), progress="", plan="Repair the failing integration validation.", integration_failure=integration_failure)
    task = read_object(task_path)
    action = _action("replan-required", reason="integration repair ceiling exhausted") if not repair.get("ok") else _action("manager-decision", reason="integration validation failed; repair opened")
    return {**_result(task, [action], ok=False), "repair": repair}

def _phase_invocation(phase: Mapping[str, Any]) -> dict[str, Any]:
    revision = int(phase.get("revision", 0)) + 1
    identity = hashlib.sha256(f"{phase.get('phaseId')}:{revision}:phase-gate".encode()).hexdigest()[:24]
    route = ((phase.get("roleAssignments") or {}).get("reviewer") or {})
    return {"schemaVersion": SCHEMA_VERSION, "runId": phase.get("phaseId"), "ownerKind": "phase", "ownerId": phase.get("phaseId"), "ownerRevision": revision, "taskId": phase.get("phaseId"), "taskRevision": revision, "invocationId": identity, "attempt": 1, "role": "reviewer", "baseSha": phase.get("baselineSha"), "profileDigest": phase.get("profileDigest", "phase-v1"), "taskDigest": phase.get("planDigest", "phase-v1"), "contextDigest": "phase-gate", "objective": "Review the frozen phase plan and baseline", "acceptanceCriteria": [], "ownedPaths": [], "forbiddenPaths": [], "contextRefs": [], "validationCommands": [], "candidateHead": phase.get("baselineSha"), "usageAccounting": ((phase.get("budget") or {}).get("policy") or {}).get("accountingMode", "invocation-v1"), "dispatchKind": "review", "reviewSubjectKind": "phase-gate", "assignedHost": route.get("hostId", "native"), "assignedModel": "/".join(str(route.get(k)) for k in ("providerId", "modelId") if route.get(k)) or "current-host/default", "limits": {"maxTurns": 8, "maxToolCalls": 16, "deadlineSeconds": 1200}, "outputSchemaVersion": SCHEMA_VERSION}


def _phase_reviewer(value: Any) -> dict[str, str]:
    if isinstance(value, Mapping):
        route={key:str(value.get(key) or "").strip() for key in ("hostId","providerId","modelId")}
        if all(route.values()): return route
    if isinstance(value,str) and value.strip():
        host,model=(value.split("::",1) if "::" in value else ("native",value))
        provider,model_id=(model.split("/",1) if "/" in model else ("native",model))
        if host and provider and model_id:return {"hostId":host,"providerId":provider,"modelId":model_id}
    raise ValueError("PhaseBrief managerDecision.roleAssignments.reviewer requires a route")


def prepare_phase(repo: Path, output: Path, brief: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
    if brief.get("schemaVersion") != PHASE_BRIEF_VERSION or not brief.get("phaseId") or not isinstance(brief.get("tasks"), list) or not brief["tasks"]:
        raise ValueError("PhaseBrief v1 requires phaseId and non-empty tasks")
    if output.exists():
        raise ValueError("Phase already exists and will not be overwritten")
    decision = brief.get("managerDecision") if isinstance(brief.get("managerDecision"), Mapping) else {}
    accounting = str(decision.get("accountingMode") or "invocation-v1")
    caps = copy.deepcopy(decision.get("hostCapabilities") or {})
    reviewer = _phase_reviewer(((decision.get("roleAssignments") or {}).get("reviewer")))
    if accounting == "host-v1" and (caps.get(reviewer["hostId"]) or {}).get("usageAccounting") is not True:
        raise ValueError(f"host-v1 requires trusted usageAccounting capability for executing host: {reviewer['hostId']}")
    capability_snapshot = {"hosts": caps}
    capability_snapshot["digest"] = hashlib.sha256(json.dumps(capability_snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    child_dir = output.parent / "tasks"
    task_refs = []
    dag = []
    ids = set()
    for child in brief["tasks"]:
        if not isinstance(child, Mapping):
            raise ValueError("PhaseBrief tasks must contain TaskBrief objects")
        _, child_decision, _ = _validate_brief(child)
        if child_decision.get("resolvedMode") != "strict":
            raise ValueError("every Phase TaskBrief must resolve to Strict mode")
        task_id = str(child.get("taskId"))
        if task_id in ids:
            raise ValueError("PhaseBrief taskIds must be unique")
        ids.add(task_id)
        child_path = child_dir / f"{task_id}.task.json"
        if child_path.exists():
            raise ValueError(f"Phase child Task already exists: {child_path}")
        task_refs.append(_relative(repo, child_path))
        dag.append({"taskId": task_id, "dependencies": list(child.get("dependencies") or []), "taskRef": _relative(repo, child_path)})
    baseline = str(brief.get("baselineSha") or git(repo, "rev-parse", "HEAD").stdout.strip())
    phase = {"schemaVersion": SCHEMA_VERSION, "revision": 0, "phaseId": str(brief["phaseId"]), "owner": {"kind": "phase", "id": str(brief["phaseId"]), "revision": 0}, "state": "Ready", "baselineSha": baseline, "integrationHead": baseline, "integrationBranch": str(brief.get("integrationBranch") or f"task/{str(brief['phaseId']).lower()}"), "contractsFrozen": True, "contracts": copy.deepcopy(brief.get("contracts") or []), "baselineReviewRef": "phase-gate:pending", "taskDag": dag, "taskRefs": task_refs, "leases": [], "roleAssignments": {"reviewer": reviewer}, "accountingCapabilities": capability_snapshot, "budget": new_task_budget(profile, accounting_mode=accounting), "reviewBindings": [], "execution": {"invocations": [], "agentResults": [], "routeFailures": [], "phaseGate": {"status": "pending"}}, "maxConcurrentWriters": int(brief.get("maxConcurrentWriters", 1)), "validation": copy.deepcopy(brief.get("validation") or {"commands": []}), "close": {"mergeCommits": [], "phaseValidation": [], "workspaceDispositions": []}}
    checked = validate_phase(phase)
    if not checked.ok:
        raise ValueError(checked.findings[0].message)
    created = []
    try:
        for child, ref in zip(brief["tasks"], task_refs):
            child_path = repo / ref
            prepare_task(repo, child_path, child, profile=profile)
            created.append(child_path)
        write_object(output, phase)
    except Exception:
        for child_path in reversed(created):
            child_path.unlink(missing_ok=True)
        raise
    return advance_phase(repo, output, profile)

def advance_phase(repo: Path, phase_path: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    phase=read_object(phase_path); gate=((phase.get("execution") or {}).get("phaseGate") or {})
    pending=_pending(phase)
    if pending: return _result(phase,[_dispatch(item) for item in pending])
    if gate.get("status")=="pending":
        route=((phase.get("roleAssignments") or {}).get("reviewer") or {}); host=str(route.get("hostId") or "native"); mode=((phase.get("budget") or {}).get("policy") or {}).get("accountingMode")
        snapshot = phase.get("accountingCapabilities") or {}
        material = {"hosts": snapshot.get("hosts") if isinstance(snapshot.get("hosts"), Mapping) else {}}
        if mode == "host-v1" and snapshot.get("digest") != hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest(): raise ValueError("host-v1 capability snapshot digest is missing or invalid")
        if mode=="host-v1" and ((material["hosts"].get(host) or {}).get("usageAccounting") is not True): raise ValueError(f"host-v1 requires trusted usageAccounting capability for executing host: {host}")
        invocation=_phase_invocation(phase); grant=reserve_tool_calls(phase["budget"],"reviewer",invocation["invocationId"])
        if grant<1: raise ValueError("phase reviewer invocation budget exhausted")
        invocation["limits"]["maxToolCalls"]=grant; phase["revision"]+=1; invocation["ownerRevision"]=phase["revision"]; invocation["taskRevision"]=phase["revision"]; phase["execution"]["invocations"].append(invocation); write_object(phase_path,phase)
        return _result(phase,[_dispatch(invocation)])
    if gate.get("status")!="Accepted": return _result(phase,[_action("replan-required",reason="phase gate rejected")],ok=False)
    nodes = list(phase.get("taskDag") or [])
    tasks = {node["taskId"]: read_object(repo / node["taskRef"]) for node in nodes}
    states = {task_id: task.get("state") for task_id, task in tasks.items()}
    if all(value == "Integrated" for value in states.values()):
        return _result(phase, [_action("integrate", phaseId=phase.get("phaseId"), reason="run phase validation")])
    dependency_ready = [node for node in nodes if states[node["taskId"]] not in TERMINAL and all(states.get(dep) == "Integrated" for dep in node.get("dependencies") or [])]
    actions = []
    # Resume already-started work before opening another writer lane.
    continuing = [node for node in dependency_ready if states[node["taskId"]] != "Ready"]
    for node in continuing:
        result = advance_task(repo, repo / node["taskRef"], profile)
        actions.extend({**item, "owner": node["taskRef"]} for item in result["actions"])
    if actions:
        return _result(read_object(phase_path), actions)
    selected = []
    rejection = None
    limit = max(1, int(phase.get("maxConcurrentWriters", 1)))
    for node in [item for item in dependency_ready if states[item["taskId"]] == "Ready"]:
        if len(selected) >= limit:
            break
        projected = {task_id: copy.deepcopy(task) for task_id, task in tasks.items()}
        for chosen in [*selected, node]:
            projected[chosen["taskId"]]["previousState"] = projected[chosen["taskId"]].get("state")
            projected[chosen["taskId"]]["state"] = "Active"
        checked = validate_wave(repo, projected.values(), phase, profile, complete=True)
        if checked.ok:
            selected.append(node)
        elif rejection is None:
            rejection = checked.findings[0].message
    for node in selected:
        result = advance_task(repo, repo / node["taskRef"], profile)
        actions.extend({**item, "owner": node["taskRef"]} for item in result["actions"])
    reason = rejection or "phase has no dependency-ready task"
    return _result(read_object(phase_path), actions or [_action("blocked", reason=reason)], ok=bool(actions))

def submit_phase(repo: Path, phase_path: Path, profile: Mapping[str, Any], invocation_id: str, payload: Mapping[str, Any], *, failure: bool = False, host_receipt: Mapping[str, Any] | None=None) -> dict[str, Any]:
    phase=read_object(phase_path); invocation=next((item for item in (phase.get("execution") or {}).get("invocations",[]) if item.get("invocationId")==invocation_id),None)
    if not invocation: raise ValueError("flow submit requires an invocation bound to this Phase")
    if any(item.get("invocationId")==invocation_id for name in ("agentResults","routeFailures") for item in (phase.get("execution") or {}).get(name,[])):
        return advance_phase(repo,phase_path,profile)
    mode=((phase.get("budget") or {}).get("policy") or {}).get("accountingMode","invocation-v1")
    usage=host_receipt
    if mode=="host-v1":
        grants=[item for item in (phase.get("budget") or {}).get("grants",[]) if item.get("invocationId")==invocation_id]
        grant=grants[0].get("amount") if len(grants)==1 else None
        if not isinstance(host_receipt,Mapping) or host_receipt.get("trusted") is not True or host_receipt.get("source") not in {"host-v1","host","runner"} or host_receipt.get("invocationId")!=invocation_id or host_receipt.get("grant")!=grant:
            usage=None
    if failure:
        settle_tool_calls(phase["budget"],invocation_id,usage)
        phase["execution"].setdefault("routeFailures",[]).append({**copy.deepcopy(dict(payload)),"invocationId":invocation_id})
        phase["revision"]+=1;write_object(phase_path,phase)
        return _result(phase,[_action("manager-decision",reason="phase route failure recorded")],ok=False)
    verdict=payload.get("verdict"); findings=payload.get("findings") or []
    if verdict not in {"Accepted","ChangesRequested"}: raise ValueError("phase-gate result requires Accepted or ChangesRequested verdict")
    if verdict=="Accepted" and any(item.get("priority") in {"P0","P1","P2"} for item in findings if isinstance(item,Mapping)): raise ValueError("Accepted phase-gate cannot contain blocking findings")
    review={"schemaVersion":SCHEMA_VERSION,"revision":0,"reviewId":"review-"+hashlib.sha256(invocation_id.encode()).hexdigest()[:24],"subject":{"kind":"phase-gate","phaseId":phase.get("phaseId"),"sha":phase.get("baselineSha"),"planDigest":plan_digest(phase)},"status":verdict,"hostId":invocation.get("assignedHost","native"),"reviewerModel":invocation.get("assignedModel","current-host/default"),"cycle":1,"findings":copy.deepcopy(findings),"validation":copy.deepcopy(payload.get("validationEvidence") or [])}
    checked=validate_review(review,phase=phase)
    if not checked.ok: raise ValueError(checked.findings[0].message)
    review_path=phase_path.parent/"reviews"/f"{phase_path.stem}-{invocation_id}.json"
    if review_path.exists() and read_object(review_path)!=review: raise ValueError("phase review artifact already contains different immutable evidence")
    if not review_path.exists(): write_object(review_path,review)
    relative=_relative(repo,review_path);digest=review_digest(review)
    settle_tool_calls(phase["budget"],invocation_id,usage)
    phase["execution"]["agentResults"].append({"schemaVersion":SCHEMA_VERSION,"invocationId":invocation_id,"attempt":invocation.get("attempt"),"status":"succeeded","findings":copy.deepcopy(findings),"verdict":verdict})
    phase["execution"]["phaseGate"]={"status":verdict,"invocationId":invocation_id,"reviewRef":relative}
    phase["baselineReviewRef"]=relative;phase["reviewBindings"].append({"subject":"phase-gate","invocationId":invocation_id,"reviewRef":relative,"digest":digest,"status":verdict})
    phase["revision"]+=1;phase["state"]="Active" if verdict=="Accepted" else "Replan Required";write_object(phase_path,phase)
    return advance_phase(repo,phase_path,profile)


def finish_phase(repo: Path, phase_path: Path) -> dict[str, Any]:
    phase=read_object(phase_path); tasks=[read_object(repo/ref) for ref in phase.get("taskRefs") or []]
    if not tasks or any(task.get("state")!="Integrated" for task in tasks): raise ValueError("flow finish requires every phase Task to be Integrated")
    head=git(repo,"rev-parse","HEAD").stdout.strip()
    merge_commits = [(task.get("close") or {}).get("mergeCommit") for task in tasks]
    if any(not commit or git(repo, "merge-base", "--is-ancestor", str(commit), head).returncode for commit in merge_commits):
        raise ValueError("phase integration HEAD must contain every child mergeCommit")
    evidence=[]
    for command in as_list((phase.get("validation") or {}).get("commands")):
        proc=subprocess.run(str(command),cwd=repo,shell=True,capture_output=True,text=True,check=False); evidence.append({"headSha":head,"command":str(command),"passed":proc.returncode==0,"exitCode":proc.returncode})
    phase["close"]["mergeCommits"]=merge_commits; phase["close"]["phaseValidation"]=evidence; phase["close"]["workspaceDispositions"]=[(task.get("close") or {}).get("workspaceDisposition") for task in tasks if (task.get("close") or {}).get("workspaceDisposition")]
    phase["revision"]+=1
    if not evidence or not all(item["passed"] for item in evidence): phase["state"]="Replan Required"; write_object(phase_path,phase); return _result(phase,[_action("replan-required",reason="phase validation failed")],ok=False)
    phase["state"]="Integrated"; phase["integrationHead"]=head; write_object(phase_path,phase); return _result(phase,[_action("complete",phaseId=phase.get("phaseId"))])


def start_flow(repo: Path, input_path: Path, output: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    brief=read_object(input_path)
    if "phaseId" in brief and "tasks" in brief: return prepare_phase(repo,output,brief,profile)
    validation=brief.get("validation") if isinstance(brief.get("validation"),dict) else None
    if validation is not None:
        relative=_relative(repo,output.parent/"reviews")+"/**"; validation.setdefault("allowedOutputs",[])
        if relative not in validation["allowedOutputs"]: validation["allowedOutputs"].append(relative)
    prepare_task(repo,output,brief,profile=profile)
    return advance_task(repo,output,profile)


def status_flow(repo: Path, owner_path: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    """Inspect an owner without reserving budget or changing lifecycle state."""
    owner=read_object(owner_path)
    pending=_pending(owner)
    if pending:
        return _result(owner,[_dispatch(item) for item in pending])
    state=owner.get("state")
    if state=="Integrated":
        return _result(owner,[_action("complete",ownerId=owner.get("taskId") or owner.get("phaseId"))])
    if state=="Accepted" or (owner.get("phaseId") and state=="Active" and all(read_object(repo/ref).get("state")=="Integrated" for ref in owner.get("taskRefs") or [])):
        return _result(owner,[_action("integrate",ownerId=owner.get("taskId") or owner.get("phaseId"))])
    if state=="Replan Required":
        return _result(owner,[_action("replan-required",reason="owner requires manager replan")],ok=False)
    if state in {"Blocked","Cancelled","Superseded"}:
        return _result(owner,[_action("blocked",reason=f"owner state is {state}")],ok=False)
    return _result(owner,[_action("manager-decision",reason="run flow advance to perform the next deterministic transition")])


def submit_flow(repo: Path, owner_path: Path, profile: Mapping[str, Any], invocation_id: str, payload: Mapping[str, Any], *, failure: bool=False, host_receipt: Mapping[str,Any]|None=None) -> dict[str,Any]:
    owner=read_object(owner_path)
    if owner.get("phaseId"):
        if any(item.get("invocationId")==invocation_id for item in (owner.get("execution") or {}).get("invocations",[])): return submit_phase(repo,owner_path,profile,invocation_id,payload,failure=failure,host_receipt=host_receipt)
        for ref in owner.get("taskRefs") or []:
            child=repo/ref
            if find_invocation(read_object(child),invocation_id): return submit_task(repo,child,profile,invocation_id,payload,failure=failure,host_receipt=host_receipt)
        raise ValueError("invocation is not bound to the Phase or any child Task")
    return submit_task(repo,owner_path,profile,invocation_id,payload,failure=failure,host_receipt=host_receipt)


def replan_flow(repo: Path, owner_path: Path, amendment: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    owner = read_object(owner_path)
    if owner.get("state") != "Replan Required":
        raise ValueError("flow replan requires an owner in Replan Required state")
    if _pending(owner):
        raise ValueError("cannot replan while an invocation is active")
    allowed = {"goal", "acceptance", "risks", "validation", "ownership", "workspace", "requestedMode", "riskClass", "workerRequired", "reviewRequired", "modeReasons", "contracts", "taskDag", "maxConcurrentWriters"}
    unknown = set(amendment) - allowed
    if unknown:
        raise ValueError("unsupported replan fields: " + ", ".join(sorted(unknown)))
    proposed = copy.deepcopy(owner)
    for key, value in amendment.items():
        proposed[key] = copy.deepcopy(value)
    execution = proposed.setdefault("execution", {})
    active = execution.get("activeRepair")
    if isinstance(active, Mapping) and active.get("status") == "open":
        closed = dict(active); closed["status"] = "superseded-by-replan"
        execution["activeRepair"] = closed
    execution["candidateReadiness"] = None
    execution["fullReviewRequired"] = True
    proposed["reviewRef"] = None
    proposed["crossReviewRefs"] = []
    proposed["reviewSpec"] = None
    proposed["revision"] = int(owner.get("revision", 0)) + 1
    if proposed.get("taskId"):
        scope_fields = {"goal", "acceptance", "ownership", "workspace", "requestedMode", "riskClass", "workerRequired", "reviewRequired", "modeReasons"}
        if set(amendment).intersection(scope_fields):
            proposed.pop("effectiveConfig", None)
            proposed.setdefault("models", {})["actual"] = None
        resolved = resolve_auto_mode({"riskClass": proposed.get("riskClass"), "modeReasons": proposed.get("modeReasons"),
                                      "ownership": proposed.get("ownership"), "workspace": proposed.get("workspace"),
                                      "writerCount": proposed.get("writerCount"), "ownershipDomainCount": proposed.get("ownershipDomainCount"),
                                      "workerRequired": proposed.get("workerRequired"), "reviewRequired": proposed.get("reviewRequired")},
                                     requested=str(proposed.get("requestedMode") or "auto"))
        proposed["resolvedMode"] = resolved["resolvedMode"]
        proposed["modeReasons"] = resolved["reasons"]
        proposed["modeFloor"] = resolved["resolvedMode"]
        proposed["state"] = "Ready"
        proposed.setdefault("commits", {})["candidate"] = None
        proposed["commits"]["fix"] = []
        checked = validate_task(proposed, profile or {})
    else:
        proposed["state"] = "Ready"
        proposed["baselineReviewRef"] = "phase-gate:pending"
        execution["phaseGate"] = {"status": "pending"}
        checked = validate_phase(proposed)
    execution["replan"] = {"revision": proposed["revision"], "fields": sorted(amendment)}
    if not checked.ok:
        raise ValueError(checked.findings[0].message)
    write_object(owner_path, proposed)
    return _result(proposed, [_action("manager-decision", reason="replan recorded; advance the owner")])

def finish_flow(repo: Path, owner_path: Path) -> dict[str,Any]:
    owner=read_object(owner_path)
    return finish_phase(repo,owner_path) if owner.get("phaseId") else finish_task(repo,owner_path)