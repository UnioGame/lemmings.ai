"""Explicit, digest-confirmed schema-v4 to schema-v5 artifact migration."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .budget import new_task_budget
from .contracts import SCHEMA_VERSION, as_list, read_object, runtime_marker, write_object

LEGACY_SCHEMA_VERSION=4


def _digest(value: Mapping[str,Any]) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def _relative(repo:Path,path:Path)->str:
    try:return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as error:raise ValueError("migration sources must be inside the repository") from error


def _active(owner:Mapping[str,Any])->str|None:
    execution=owner.get("execution") if isinstance(owner.get("execution"),Mapping) else {}
    settled={item.get("invocationId") for name in ("agentResults","routeFailures") for item in as_list(execution.get(name)) if isinstance(item,Mapping)}
    if any(item.get("invocationId") not in settled for item in as_list(execution.get("invocations")) if isinstance(item,Mapping)):return "active invocation"
    if as_list((owner.get("budget") or {}).get("reservations")):return "active budget reservation"
    if any(item.get("active",True) for item in as_list(owner.get("leases")) if isinstance(item,Mapping)):return "active lease"
    workspace=owner.get("workspace") if isinstance(owner.get("workspace"),Mapping) else {}
    if workspace.get("workspaceId") and owner.get("state") not in {"Integrated","Cancelled","Superseded"}:return "active workspace"
    return None


def _refs(value:Mapping[str,Any])->list[str]:
    refs=[]
    if value.get("phaseId"):
        refs.extend(str(x) for x in as_list(value.get("taskRefs")))
        refs.extend(str(x.get("taskRef")) for x in as_list(value.get("taskDag")) if isinstance(x,Mapping) and x.get("taskRef"))
        baseline=value.get("baselineReviewRef")
        if isinstance(baseline,str) and not baseline.startswith("phase-gate:"):refs.append(baseline.split("#",1)[0])
    if value.get("taskId"):
        refs.extend(str(x) for x in as_list(value.get("reviewHistory")))
        refs.extend(str(x) for x in [value.get("reviewRef"),*as_list(value.get("crossReviewRefs"))] if x)
        plan=value.get("planReviewRef")
        if plan:refs.append(str(plan))
    return list(dict.fromkeys(ref.split("#",1)[0] for ref in refs if ref))


def propose_migration(repo:Path,owner_path:Path)->dict[str,Any]:
    repo=repo.resolve();owner_path=owner_path.resolve();owner=read_object(owner_path)
    if owner.get("schemaVersion")!=LEGACY_SCHEMA_VERSION:raise ValueError("migrate propose requires a schemaVersion 4 Task or Phase")
    marker=runtime_marker(repo)
    if marker.is_file():
        state=read_object(marker); source=_relative(repo,owner_path)
        if source in as_list(state.get("taskPaths")) or source==state.get("phasePath"):raise ValueError("migration is blocked while the source owner is active in the runtime marker")
    queue=[owner_path];seen=set();entries=[]
    while queue:
        path=queue.pop(0).resolve()
        if path in seen:continue
        seen.add(path)
        if not path.is_file():raise ValueError(f"migration reference is missing: {_relative(repo,path)}")
        value=read_object(path)
        if value.get("schemaVersion")!=LEGACY_SCHEMA_VERSION:raise ValueError("mixed-version artifact graph is not migratable")
        reason=_active(value)
        if reason:raise ValueError(f"migration blocked by {reason}: {_relative(repo,path)}")
        kind="phase" if value.get("phaseId") else "task" if value.get("taskId") else "review" if value.get("reviewId") else None
        if kind is None:raise ValueError(f"unknown schema-v4 artifact kind: {_relative(repo,path)}")
        relative=_relative(repo,path);entries.append({"path":relative,"target":relative,"kind":kind,"digest":_digest(value)})
        for ref in _refs(value):queue.append((repo/ref).resolve())
    proposal={"proposalVersion":1,"sourceSchemaVersion":4,"targetSchemaVersion":SCHEMA_VERSION,"owner":_relative(repo,owner_path),"entries":sorted(entries,key=lambda x:x["path"])}
    proposal["digest"]=_digest(proposal)
    return proposal


def _migrate_value(value:Mapping[str,Any],kind:str)->dict[str,Any]:
    result=copy.deepcopy(dict(value));result["schemaVersion"]=SCHEMA_VERSION
    if kind=="task":
        task_id=result.get("taskId");result.setdefault("owner",{"kind":"task","id":task_id,"revision":result.get("revision",0)});result.setdefault("reviewBindings",[]);result.setdefault("accountingCapabilities",{"hosts":{}})
        execution=result.setdefault("execution",{})
        for invocation in as_list(execution.get("invocations")):
            if isinstance(invocation,dict):invocation.update(schemaVersion=SCHEMA_VERSION,ownerKind="task",ownerId=task_id,ownerRevision=invocation.get("taskRevision",0),outputSchemaVersion=SCHEMA_VERSION)
        for name in ("agentResults","routeFailures"):
            for item in as_list(execution.get(name)):
                if isinstance(item,dict):item["schemaVersion"]=SCHEMA_VERSION
    elif kind=="phase":
        phase_id=result.get("phaseId");result.setdefault("owner",{"kind":"phase","id":phase_id,"revision":result.get("revision",0)});result.setdefault("state","Active");result.setdefault("reviewBindings",[]);result.setdefault("execution",{"invocations":[],"agentResults":[],"routeFailures":[],"phaseGate":{"status":"Accepted" if result.get("baselineReviewRef") else "pending"}});result.setdefault("budget",new_task_budget({},accounting_mode="invocation-v1"));result.setdefault("accountingCapabilities",{"hosts":{}});result.setdefault("taskRefs",[x.get("taskRef") for x in as_list(result.get("taskDag")) if isinstance(x,Mapping) and x.get("taskRef")]);result.setdefault("validation",{"commands":[]});result.setdefault("maxConcurrentWriters",1)
    else:
        subject=result.get("subject") if isinstance(result.get("subject"),dict) else {}
        if subject.get("kind")=="baseline":subject["kind"]="phase-gate"
        elif subject.get("kind")=="plan":subject["kind"]="task-plan"
    return result



def _rewrite_refs(value: Any, remap: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_refs(item, remap) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_refs(item, remap) for item in value]
    if isinstance(value, str):
        base, marker, fragment = value.partition("#")
        if base in remap:
            return remap[base] + (marker + fragment if marker else "")
    return value

def apply_migration(repo:Path,proposal:Mapping[str,Any],confirmation:str,output_root:Path)->dict[str,Any]:
    expected=_digest({k:v for k,v in proposal.items() if k!="digest"})
    if proposal.get("digest")!=expected or confirmation!=expected:raise ValueError("migration confirmation digest does not match the proposal")
    if proposal.get("sourceSchemaVersion")!=4 or proposal.get("targetSchemaVersion")!=SCHEMA_VERSION:raise ValueError("migration proposal versions are invalid")
    owner_ref = proposal.get("owner")
    if not isinstance(owner_ref, str) or not owner_ref:
        raise ValueError("migration proposal owner is missing")
    canonical = propose_migration(repo.resolve(), (repo / owner_ref).resolve())
    if canonical.get("digest") != expected or canonical.get("entries") != proposal.get("entries"):
        raise ValueError("migration proposal does not contain the complete canonical artifact graph")
    repo=repo.resolve();output_root=output_root.resolve();planned=[]
    try:
        output_root.relative_to(repo)
    except ValueError as error:
        raise ValueError("migration output root must be inside the repository") from error
    remap = {str(entry["path"]): _relative(repo, output_root / str(entry["target"])) for entry in proposal.get("entries") or []}
    # Validate the complete graph and every target before writing the first file.
    for entry in proposal.get("entries") or []:
        source=(repo/entry["path"]).resolve();value=read_object(source)
        if value.get("schemaVersion")!=4 or _digest(value)!=entry.get("digest"):raise ValueError(f"migration source changed: {entry.get('path')}")
        if _active(value):raise ValueError(f"migration source became active: {entry.get('path')}")
        target=(output_root/entry["target"]).resolve()
        try:target.relative_to(output_root)
        except ValueError as error:raise ValueError("migration target escapes output root") from error
        if target.exists():raise ValueError(f"migration never overwrites an existing target: {target}")
        migrated = _rewrite_refs(_migrate_value(value, str(entry["kind"])), remap)
        planned.append((target, migrated))
    written=[]
    for target,migrated in planned:
        write_object(target,migrated);written.append(str(target))
    return {"ok":True,"schemaVersion":SCHEMA_VERSION,"proposalDigest":expected,"written":written,"markerSwitched":False}