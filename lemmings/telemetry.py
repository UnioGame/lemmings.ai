"""Optional, local, privacy-bounded timing telemetry for Lemmings."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

from .contracts import MODES, REVIEW_STATES, SCHEMA_VERSION, TASK_STATES, candidate_head, git, git_common_dir, read_object

TELEMETRY_MODES = {"off", "basic", "full"}
LIFECYCLE_STAGES = ("discover", "plan", "refine", "implement", "verify")
FINISH_OUTCOMES = {"completed", "blocked", "cancelled", "replan"}
QUALITY_CATEGORIES = {"tests", "coverage", "analyzer", "complexity", "performance", "size", "cost"}
QUALITY_DIRECTIONS = {"higher-better", "lower-better", "target"}
QUALITY_STATUSES = {"pass", "fail", "unknown"}
ANNOTATION_KINDS = {"escaped-defect", "rollback", "revert", "regression-resolved"}
REGRESSION_RELATIONS = {"confirmed", "suspected"}
SEVERITIES = {"P0", "P1", "P2", "P3"}
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_LOCAL_MIB = 100


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def looks_absolute_path(value: str) -> bool:
    return Path(value).is_absolute() or value.startswith(("/", "\\\\")) or (len(value) > 2 and value[1] == ":" and value[2] in {"/", "\\"})


def contains_sensitive_text(value: str) -> bool:
    lowered = value.lower()
    patterns = ("bearer ", "-----begin private key", "password=", "passwd=", "api_key=", "apikey=", "ghp_", "github_pat_", "sk-proj-")
    if any(pattern in lowered for pattern in patterns):
        return True
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://[^/@\s]+:[^/@\s]+@", value, re.I))


def safe_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if looks_absolute_path(text) or contains_sensitive_text(text):
        return None
    return text[:256]


def telemetry_root(repo: Path) -> Path:
    # v1 data is intentionally left untouched and never mixed into v2 reports.
    return git_common_dir(repo) / "lemmings" / "telemetry-v2"


def settings_path(repo: Path) -> Path:
    return telemetry_root(repo) / "settings.json"


def default_settings() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "off",
        "retentionDays": DEFAULT_RETENTION_DAYS,
        "maxLocalMiB": DEFAULT_MAX_LOCAL_MIB,
    }


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_settings(repo: Path) -> dict[str, Any]:
    path = settings_path(repo)
    if not path.is_file():
        return default_settings()
    value = read_object(path)
    merged = default_settings()
    merged.update(value)
    if merged.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(f"unsupported telemetry settings schemaVersion: {merged.get('schemaVersion')!r}; expected 2")
    if merged.get("mode") not in TELEMETRY_MODES:
        raise ValueError("telemetry mode must be off, basic, or full")
    return merged


def set_telemetry_mode(repo: Path, mode: str) -> dict[str, Any]:
    if mode not in TELEMETRY_MODES:
        raise ValueError(f"unsupported telemetry mode: {mode}")
    value = load_settings(repo)
    value["mode"] = mode
    atomic_write_json(settings_path(repo), value)
    return value


def telemetry_enabled(repo: Path, minimum: str = "basic") -> bool:
    mode = load_settings(repo).get("mode", "off")
    ranks = {"off": 0, "basic": 1, "full": 2}
    return ranks[str(mode)] >= ranks[minimum]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def repository_id(repo: Path) -> str:
    remote = git(repo, "config", "--get", "remote.origin.url")
    seed = remote.stdout.strip() if not remote.returncode and remote.stdout.strip() else repo.name
    return _hash(seed)[:16]


def _worktree_key(cwd: Path) -> str:
    return _hash(os.path.normcase(str(resolve_worktree_root(cwd))))[:24]


def resolve_worktree_root(cwd: Path) -> Path:
    process = git(cwd, "rev-parse", "--show-toplevel")
    return Path(process.stdout.strip()).resolve() if not process.returncode and process.stdout.strip() else cwd.resolve()


def binding_path(repo: Path, cwd: Path) -> Path:
    return telemetry_root(repo) / "bindings" / f"{_worktree_key(cwd)}.json"


def read_binding(repo: Path, cwd: Path) -> dict[str, Any] | None:
    path = binding_path(repo, cwd)
    return read_object(path) if path.is_file() else None


def bind_run(
    repo: Path,
    cwd: Path,
    *,
    task_id: str | None = None,
    phase_id: str | None = None,
    role: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    cohort: str | None = None,
    branch: str | None = None,
    task_path: str | None = None,
) -> dict[str, Any]:
    worktree = resolve_worktree_root(cwd)
    existing = read_binding(repo, worktree) or {}
    if existing.get("finished"):
        existing = {}
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": existing.get("runId") or str(uuid.uuid4()),
        "worktree": str(worktree),
        "createdAt": existing.get("createdAt") or iso_timestamp(),
        "finished": False,
    }
    for key, item in {
        "taskId": task_id,
        "phaseId": phase_id,
        "role": role,
        "model": model,
        "mode": mode,
        "telemetryCohort": cohort,
        "branch": branch,
        "taskPath": task_path,
    }.items():
        if item is not None:
            value[key] = item
        elif key in existing:
            value[key] = existing[key]
    for key in ("currentStage", "stageEnteredAt"):
        if key in existing:
            value[key] = existing[key]
    atomic_write_json(binding_path(repo, worktree), value)
    return value


def find_task_binding(repo: Path, task_id: str) -> dict[str, Any] | None:
    root = telemetry_root(repo) / "bindings"
    if not root.is_dir():
        return None
    matches: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        if not path.is_file():
            continue
        item = read_object(path)
        if item.get("taskId") == task_id:
            matches.append(item)
    active = [item for item in matches if not item.get("finished")]
    candidates = active or matches
    task_paths = {str(item.get("taskPath")) for item in candidates if item.get("taskPath")}
    if len(task_paths) > 1:
        raise ValueError(f"multiple telemetry bindings exist for task {task_id}; pass the task packet path")
    candidates.sort(key=lambda item: str(item.get("finishedAt") or item.get("createdAt") or ""), reverse=True)
    return candidates[0] if candidates else None


def _event_identity(parts: Iterable[Any]) -> str:
    return _hash("\x1f".join("" if part is None else str(part) for part in parts))


def record_event(
    repo: Path,
    event_type: str,
    *,
    source: str,
    cwd: Path | None = None,
    binding: Mapping[str, Any] | None = None,
    timestamp: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    phase_id: str | None = None,
    stage: str | None = None,
    role: str | None = None,
    model: str | None = None,
    data: Mapping[str, Any] | None = None,
    dedupe_parts: Iterable[Any] | None = None,
    allow_finished_binding: bool = False,
) -> dict[str, Any] | None:
    if not telemetry_enabled(repo):
        return None
    working = (cwd or repo).resolve()
    current = dict(binding or read_binding(repo, working) or {})
    if current.get("finished") and not allow_finished_binding:
        current = {}
    unbound = not current and not task_id
    run_id = str(current.get("runId") or _hash(f"{repository_id(repo)}:{session_id or turn_id or _worktree_key(working)}")[:32])
    event_timestamp = timestamp or iso_timestamp()
    event_id = str(uuid.uuid4())
    identity = list(dedupe_parts or (event_type, session_id, turn_id, agent_id, task_id or current.get("taskId"), event_timestamp))
    dedupe_key = _event_identity(identity)
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "eventId": event_id,
        "dedupeKey": dedupe_key,
        "timestampUtc": event_timestamp,
        "source": source,
        "type": event_type,
        "repoId": repository_id(repo),
        "runId": run_id,
    }
    optional = {
        "sessionId": session_id,
        "turnId": turn_id,
        "agentId": agent_id,
        "taskId": task_id or current.get("taskId"),
        "phaseId": phase_id or current.get("phaseId"),
        "stage": stage or current.get("currentStage"),
        "role": role or current.get("role"),
        "model": model or current.get("model"),
    }
    payload.update({key: safe for key, value in optional.items() if (safe := safe_label(value)) is not None})
    event_data = dict(data or {})
    if unbound:
        event_data["unbound"] = True
    if event_data:
        payload["data"] = event_data
    day = parse_timestamp(event_timestamp).strftime("%Y-%m-%d")
    path = telemetry_root(repo) / "events" / day / f"{dedupe_key}.json"
    if path.is_file():
        return read_object(path)
    atomic_write_json(path, payload)
    return payload


def _review_for_task(repo: Path, task: Mapping[str, Any] | None) -> dict[str, Any] | None:
    reference = (task or {}).get("reviewRef")
    if not reference:
        return None
    target = Path(str(reference))
    target = target if target.is_absolute() else repo / target
    try:
        target.resolve().relative_to(repo.resolve())
        return read_object(target) if target.is_file() else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def summarize_task(task: Mapping[str, Any] | None, review: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not task:
        return {}
    models = task.get("models") or {}
    validation = task.get("validation") or {}
    execution = task.get("execution") or {}
    review = review or {}
    commits = task.get("commits") or {}
    close = task.get("close") or {}
    evidence = execution.get("validationEvidence") or []
    validation_failures = sum(1 for item in evidence if isinstance(item, Mapping) and item.get("passed") is False)
    state = task.get("state") if task.get("state") in TASK_STATES else None
    mode = task.get("mode") if task.get("mode") in MODES else None
    review_status = review.get("status") if review.get("status") in REVIEW_STATES else None
    final_head = close.get("mergeCommit") if state == "Integrated" and close.get("mergeCommit") else candidate_head(task)
    return {
        "state": state,
        "mode": mode,
        "telemetryCohort": safe_label(task.get("telemetryCohort")),
        "requestedModel": safe_label(models.get("requested")),
        "assignedModel": safe_label(models.get("assigned")),
        "actualModel": safe_label(models.get("actual")),
        "candidatePresent": bool(commits.get("candidate")),
        "fixCount": len(commits.get("fix") or []),
        "reviewStatus": review_status,
        "reviewCycle": int(review.get("cycle") or 0),
        "validationEvidenceCount": len(evidence),
        "validationFailureCount": validation_failures,
        "validationDebtCount": len(validation.get("debt") or []),
        "integrationValidationPassed": bool(close.get("integrationValidationPassed")),
        "finalHead": safe_label(final_head),
    }


def git_change_summary(repo: Path, task: Mapping[str, Any] | None) -> dict[str, int] | None:
    if not task:
        return None
    base = task.get("baseSha")
    head = candidate_head(task)
    if not base or not head or base == head:
        return None
    process = git(repo, "diff", "--numstat", str(base), str(head))
    if process.returncode:
        return None
    files = additions = deletions = binaries = 0
    for line in process.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        files += 1
        if parts[0] == "-" or parts[1] == "-":
            binaries += 1
        else:
            additions += int(parts[0])
            deletions += int(parts[1])
    return {"files": files, "additions": additions, "deletions": deletions, "binaryFiles": binaries}


def record_task_observation(repo: Path, cwd: Path, task: Mapping[str, Any], source: str = "cli") -> dict[str, Any] | None:
    review = _review_for_task(repo, task)
    data: dict[str, Any] = {"task": summarize_task(task, review)}
    changes = git_change_summary(repo, task)
    if changes:
        data["git"] = changes
    return record_event(
        repo, "task.observed", source=source, cwd=cwd, task_id=task.get("taskId"),
        data=data,
        dedupe_parts=("task.observed", task.get("taskId"), task.get("state"), candidate_head(task), (review or {}).get("cycle")),
    )


def enter_stage(
    repo: Path,
    cwd: Path,
    stage: str,
    *,
    task: Mapping[str, Any] | None = None,
    phase: Mapping[str, Any] | None = None,
    task_path: str | None = None,
) -> dict[str, Any]:
    if stage not in LIFECYCLE_STAGES:
        raise ValueError(f"stage must be one of {', '.join(LIFECYCLE_STAGES)}")
    if not telemetry_enabled(repo):
        return {"ok": True, "recorded": False, "mode": "off"}
    models = (task or {}).get("models") or {}
    binding = bind_run(
        repo,
        cwd,
        task_id=(task or {}).get("taskId"),
        phase_id=(phase or {}).get("phaseId"),
        role=(task or {}).get("role") or "orchestrator",
        model=models.get("actual") or models.get("assigned"),
        mode=(task or {}).get("mode"),
        cohort=(task or {}).get("telemetryCohort"),
        task_path=task_path,
    )
    if binding.get("currentStage") == stage:
        return {"ok": True, "recorded": False, "idempotent": True, "runId": binding["runId"], "stage": stage}
    now = iso_timestamp()
    previous = binding.get("currentStage")
    if previous and binding.get("stageEnteredAt"):
        duration = max(0.0, (parse_timestamp(now) - parse_timestamp(str(binding["stageEnteredAt"]))).total_seconds())
        record_event(
            repo, "stage.exited", source="cli", cwd=cwd, binding=binding, timestamp=now,
            stage=str(previous), data={"durationSeconds": duration},
            dedupe_parts=(binding["runId"], previous, binding["stageEnteredAt"], "exit"),
        )
    binding["currentStage"] = stage
    binding["stageEnteredAt"] = now
    atomic_write_json(binding_path(repo, cwd), binding)
    data = {"task": summarize_task(task, _review_for_task(repo, task))} if task else {}
    record_event(
        repo, "stage.entered", source="cli", cwd=cwd, binding=binding, timestamp=now,
        stage=stage, data=data, dedupe_parts=(binding["runId"], stage, now, "enter"),
    )
    return {"ok": True, "recorded": True, "runId": binding["runId"], "stage": stage, "previousStage": previous}


def finish_run(
    repo: Path,
    cwd: Path,
    outcome: str,
    *,
    task: Mapping[str, Any] | None = None,
    event_type: str = "task.finished",
) -> dict[str, Any]:
    if outcome not in FINISH_OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(sorted(FINISH_OUTCOMES))}")
    if not telemetry_enabled(repo):
        return {"ok": True, "recorded": False, "mode": "off"}
    binding = read_binding(repo, cwd)
    if not binding:
        binding = bind_run(repo, cwd, task_id=(task or {}).get("taskId"), role=(task or {}).get("role") or "orchestrator")
    if binding.get("finished"):
        return {"ok": True, "recorded": False, "idempotent": True, "runId": binding["runId"]}
    now = iso_timestamp()
    if binding.get("currentStage") and binding.get("stageEnteredAt"):
        duration = max(0.0, (parse_timestamp(now) - parse_timestamp(str(binding["stageEnteredAt"]))).total_seconds())
        record_event(
            repo, "stage.exited", source="cli", cwd=cwd, binding=binding, timestamp=now,
            stage=str(binding["currentStage"]), data={"durationSeconds": duration},
            dedupe_parts=(binding["runId"], binding["currentStage"], binding["stageEnteredAt"], "exit"),
        )
    data = {"outcome": outcome, "task": summarize_task(task, _review_for_task(repo, task))}
    changes = git_change_summary(repo, task)
    if changes:
        data["git"] = changes
    record_event(
        repo, event_type, source="cli", cwd=cwd, binding=binding, timestamp=now,
        data=data, dedupe_parts=(binding["runId"], event_type, outcome),
    )
    binding["finished"] = True
    binding["finishedAt"] = now
    binding["outcome"] = outcome
    binding.pop("currentStage", None)
    binding.pop("stageEnteredAt", None)
    atomic_write_json(binding_path(repo, cwd), binding)
    return {"ok": True, "recorded": True, "runId": binding["runId"], "outcome": outcome}


def iter_events(repo: Path) -> Iterable[dict[str, Any]]:
    root = telemetry_root(repo) / "events"
    if not root.is_dir():
        return []
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in root.rglob("*.json"):
        try:
            item = read_object(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        key = str(item.get("dedupeKey") or item.get("eventId") or path)
        if key in seen:
            continue
        seen.add(key)
        events.append(item)
    events.sort(key=lambda item: str(item.get("timestampUtc", "")))
    return events


def parse_period(value: str) -> timedelta:
    if len(value) < 2 or not value[:-1].isdigit() or value[-1].lower() not in {"d", "h", "m"}:
        raise ValueError("period must use Nd, Nh, or Nm syntax")
    number = int(value[:-1])
    return {"d": timedelta(days=number), "h": timedelta(hours=number), "m": timedelta(minutes=number)}[value[-1].lower()]


def normalize_quality_observation(value: Mapping[str, Any], expected_task_id: str | None = None, task: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if value.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(f"unsupported quality observation schemaVersion: {value.get('schemaVersion')!r}; expected 2")
    for field in ("taskId", "baseSha", "headSha", "recordedAt", "signals"):
        if not value.get(field):
            raise ValueError(f"quality observation requires {field}")
    if expected_task_id and value.get("taskId") != expected_task_id:
        raise ValueError("quality observation taskId does not match --task")
    if value.get("baseSha") == value.get("headSha"):
        raise ValueError("quality observation baseSha and headSha must differ")
    for field in ("taskId", "baseSha", "headSha"):
        text = str(value[field])
        if len(text) > 256 or looks_absolute_path(text) or contains_sensitive_text(text):
            raise ValueError(f"quality observation {field} must be a safe identifier")
    parse_timestamp(str(value["recordedAt"]))
    if task:
        if task.get("taskId") != value.get("taskId"):
            raise ValueError("quality observation taskId does not match task packet")
        if task.get("baseSha") and task.get("baseSha") != value.get("baseSha"):
            raise ValueError("quality observation baseSha does not match task packet")
        allowed_heads = {candidate_head(task), (task.get("close") or {}).get("mergeCommit")}
        allowed_heads.discard(None)
        if allowed_heads and value.get("headSha") not in allowed_heads:
            raise ValueError("quality observation headSha does not match the current candidate/fix head or integration merge")
    if not isinstance(value.get("signals"), list):
        raise ValueError("quality observation signals must be an array")
    normalized_signals: list[dict[str, Any]] = []
    for index, signal in enumerate(value["signals"]):
        if not isinstance(signal, Mapping):
            raise ValueError(f"signals[{index}] must be an object")
        for field in ("name", "category", "value", "unit", "direction", "status", "sourceRef"):
            if field not in signal or signal[field] in (None, ""):
                raise ValueError(f"signals[{index}] requires {field}")
        if signal["category"] not in QUALITY_CATEGORIES:
            raise ValueError(f"signals[{index}] has unknown category")
        if signal["direction"] not in QUALITY_DIRECTIONS:
            raise ValueError(f"signals[{index}] has unknown direction")
        if signal["status"] not in QUALITY_STATUSES:
            raise ValueError(f"signals[{index}] has unknown status")
        if isinstance(signal["value"], bool) or not isinstance(signal["value"], (int, float)):
            raise ValueError(f"signals[{index}].value must be numeric")
        for optional_number in ("baseline", "threshold"):
            if optional_number in signal and signal[optional_number] is not None and (isinstance(signal[optional_number], bool) or not isinstance(signal[optional_number], (int, float))):
                raise ValueError(f"signals[{index}].{optional_number} must be numeric when present")
        if looks_absolute_path(str(signal["sourceRef"])):
            raise ValueError(f"signals[{index}].sourceRef must not be an absolute path")
        string_values = [str(item) for item in signal.values() if isinstance(item, str)]
        if any(len(item) > 1024 or looks_absolute_path(item) or contains_sensitive_text(item) for item in string_values):
            raise ValueError(f"signals[{index}] contains secret-like text")
        normalized = {
            key: signal[key]
            for key in ("name", "category", "value", "unit", "direction", "status", "sourceRef", "baseline", "threshold")
            if key in signal
        }
        normalized_signals.append(normalized)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "taskId": str(value["taskId"]),
        "baseSha": str(value["baseSha"]),
        "headSha": str(value["headSha"]),
        "recordedAt": str(value["recordedAt"]),
        "signals": normalized_signals,
    }


def validate_quality_observation(value: Mapping[str, Any], expected_task_id: str | None = None, task: Mapping[str, Any] | None = None) -> None:
    normalize_quality_observation(value, expected_task_id, task)


def import_quality(repo: Path, cwd: Path, value: Mapping[str, Any], task_id: str, task: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not telemetry_enabled(repo, "full"):
        raise ValueError("quality import requires full telemetry")
    normalized = normalize_quality_observation(value, task_id, task)
    binding = read_binding(repo, cwd)
    if not binding or binding.get("taskId") != task_id:
        binding = find_task_binding(repo, task_id)
    if not binding:
        binding = {
            "runId": str(uuid.uuid4()),
            "taskId": task_id,
            "telemetryCohort": (task or {}).get("telemetryCohort"),
        }
    event = record_event(
        repo, "quality.imported", source="ci", cwd=cwd, binding=binding,
        task_id=task_id, timestamp=str(normalized["recordedAt"]), data={"observation": normalized},
        dedupe_parts=("quality", task_id, normalized["baseSha"], normalized["headSha"], json.dumps(normalized["signals"], sort_keys=True)),
        allow_finished_binding=True,
    )
    return {"ok": True, "recorded": event is not None, "eventId": (event or {}).get("eventId")}


def integrated_timestamp(repo: Path, task_id: str) -> str | None:
    for event in reversed(list(iter_events(repo))):
        if event.get("taskId") == task_id and event.get("type") in {"task.integrated", "task.finished"}:
            data = event.get("data") or {}
            task = data.get("task") or {}
            if event.get("type") == "task.integrated" or task.get("state") == "Integrated":
                return str(event.get("timestampUtc"))
    return None


def annotate_regression(
    repo: Path,
    cwd: Path,
    *,
    task_id: str,
    kind: str,
    severity: str,
    relation: str,
    reference: str,
    detected_at: str | None = None,
    resolved_at: str | None = None,
    fix_commit: str | None = None,
) -> dict[str, Any]:
    if not telemetry_enabled(repo, "full"):
        raise ValueError("regression annotations require full telemetry")
    if kind not in ANNOTATION_KINDS:
        raise ValueError("unknown regression annotation kind")
    if severity not in SEVERITIES:
        raise ValueError("severity must be P0, P1, P2, or P3")
    if relation not in REGRESSION_RELATIONS:
        raise ValueError("relation must be confirmed or suspected")
    if looks_absolute_path(reference):
        raise ValueError("regression reference must not be an absolute path")
    if len(reference) > 1024 or contains_sensitive_text(reference):
        raise ValueError("regression reference contains secret-like text")
    integrated_at = integrated_timestamp(repo, task_id)
    if not integrated_at:
        raise ValueError("regression annotation requires an Integrated task event")
    if kind == "regression-resolved":
        matching = [
            event for event in iter_events(repo)
            if event.get("taskId") == task_id
            and event.get("type") == "regression.annotated"
            and (event.get("data") or {}).get("reference") == reference
            and (event.get("data") or {}).get("kind") != "regression-resolved"
        ]
        if not matching:
            raise ValueError("regression resolution requires an existing matching regression")
    observed_at = resolved_at if kind == "regression-resolved" else detected_at
    timestamp = observed_at or iso_timestamp()
    if parse_timestamp(timestamp) < parse_timestamp(integrated_at):
        raise ValueError("regression timestamp cannot precede integration")
    data = {
        "kind": kind,
        "severity": severity,
        "relation": relation,
        "reference": reference,
        "integratedAt": integrated_at,
    }
    if detected_at:
        data["detectedAt"] = detected_at
    if resolved_at:
        data["resolvedAt"] = resolved_at
    if fix_commit:
        if len(fix_commit) > 256 or looks_absolute_path(fix_commit) or contains_sensitive_text(fix_commit):
            raise ValueError("fix commit must be a safe identifier")
        data["fixCommit"] = safe_label(fix_commit)
    event = record_event(
        repo, "regression.annotated", source="manual", cwd=cwd, task_id=task_id,
        timestamp=timestamp, data=data,
        dedupe_parts=("regression", task_id, kind, relation, reference, timestamp),
    )
    return {"ok": True, "recorded": event is not None, "eventId": (event or {}).get("eventId")}


def _seconds_between(start: Mapping[str, Any], end: Mapping[str, Any]) -> float:
    return max(0.0, (parse_timestamp(str(end["timestampUtc"])) - parse_timestamp(str(start["timestampUtc"]))).total_seconds())


def build_report(
    repo: Path,
    *,
    task_id: str | None = None,
    phase_id: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    events = list(iter_events(repo))
    if since:
        cutoff = utc_now() - parse_period(since)
        events = [item for item in events if parse_timestamp(str(item["timestampUtc"])) >= cutoff]
    if task_id:
        events = [item for item in events if item.get("taskId") == task_id]
    if phase_id:
        events = [item for item in events if item.get("phaseId") == phase_id]

    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        runs[str(event.get("runId"))].append(event)
    stage_seconds: Counter[str] = Counter()
    agent_starts: dict[str, dict[str, Any]] = {}
    turn_starts: dict[str, dict[str, Any]] = {}
    tool_starts: dict[str, dict[str, Any]] = {}
    active_agent_seconds = 0.0
    agent_seconds_by_role: Counter[str] = Counter()
    tool_seconds = 0.0
    tool_seconds_by_name: Counter[str] = Counter()
    tool_calls = 0
    turn_seconds = 0.0
    completed_turns = 0
    lead_times: list[float] = []
    open_runs = 0
    incomplete_lifecycle_runs = 0
    outcomes: Counter[str] = Counter()
    task_summaries: dict[str, dict[str, Any]] = {}
    state_timestamps: dict[str, dict[str, str]] = defaultdict(dict)
    quality_signals: list[dict[str, Any]] = []
    quality_pass_heads: set[tuple[str, str]] = set()
    regressions: list[dict[str, Any]] = []
    git_summaries: dict[str, dict[str, int]] = {}
    unbound = 0
    unbound_events: list[dict[str, Any]] = []
    implement_intervals: list[tuple[datetime, datetime]] = []
    for event in events:
        data = event.get("data") or {}
        is_unbound = bool(data.get("unbound"))
        unbound += int(is_unbound)
        if is_unbound:
            unbound_events.append(event)
        if event.get("type") == "stage.exited":
            duration = float(data.get("durationSeconds") or 0)
            current_stage = str(event.get("stage"))
            stage_seconds[current_stage] += duration
            if current_stage == "implement":
                end = parse_timestamp(str(event["timestampUtc"]))
                implement_intervals.append((end - timedelta(seconds=duration), end))
        correlation = str(data.get("correlationId") or "")
        if event.get("type") == "agent.started" and correlation:
            agent_starts[correlation] = event
        elif event.get("type") == "agent.finished" and correlation in agent_starts:
            started = agent_starts.pop(correlation)
            duration = _seconds_between(started, event)
            active_agent_seconds += duration
            agent_seconds_by_role[str(started.get("role") or "unknown")] += duration
        elif event.get("type") == "tool.started" and correlation:
            tool_starts[correlation] = event
        elif event.get("type") == "tool.finished":
            tool_calls += 1
            if correlation in tool_starts:
                started = tool_starts.pop(correlation)
                duration = _seconds_between(started, event)
                tool_seconds += duration
                tool_seconds_by_name[str((started.get("data") or {}).get("tool") or "unknown")] += duration
        elif event.get("type") == "turn.started" and correlation:
            turn_starts[correlation] = event
        elif event.get("type") == "turn.finished" and correlation in turn_starts:
            turn_seconds += _seconds_between(turn_starts.pop(correlation), event)
            completed_turns += 1
        if isinstance(data.get("task"), Mapping) and event.get("taskId"):
            current_task_id = str(event["taskId"])
            task_summaries[current_task_id] = dict(data["task"])
            current_state = data["task"].get("state")
            if current_state and current_state not in state_timestamps[current_task_id]:
                state_timestamps[current_task_id][str(current_state)] = str(event["timestampUtc"])
            if isinstance(data.get("git"), Mapping):
                git_summaries[current_task_id] = {key: int(value) for key, value in data["git"].items()}
        if event.get("type") == "quality.imported":
            observation = data.get("observation") or {}
            imported_signals = observation.get("signals") or []
            quality_signals.extend(imported_signals)
            if event.get("taskId") and observation.get("headSha"):
                if any(item.get("status") == "pass" for item in imported_signals if isinstance(item, Mapping)):
                    quality_pass_heads.add((str(event["taskId"]), str(observation["headSha"])))
        if event.get("type") == "regression.annotated":
            regressions.append({"taskId": event.get("taskId"), "timestampUtc": event.get("timestampUtc"), **data})

    for run_events in runs.values():
        ordered = sorted(run_events, key=lambda item: str(item.get("timestampUtc", "")))
        entries = [item for item in ordered if item.get("type") == "stage.entered"]
        finishes = [item for item in ordered if item.get("type") in {"task.finished", "task.integrated"}]
        if entries and finishes:
            lead_times.append(_seconds_between(entries[0], finishes[-1]))
            outcome = str((finishes[-1].get("data") or {}).get("outcome") or "completed")
            outcomes[outcome] += 1
            if not set(LIFECYCLE_STAGES).issubset({str(item.get("stage")) for item in entries}):
                incomplete_lifecycle_runs += 1
        elif finishes:
            incomplete_lifecycle_runs += 1
        elif entries:
            open_runs += 1

    resolutions = {(str(item.get("taskId")), str(item.get("reference"))): item for item in regressions if item.get("kind") == "regression-resolved"}
    enriched: list[dict[str, Any]] = []
    for item in regressions:
        if item.get("kind") == "regression-resolved":
            continue
        current = dict(item)
        detected = str(current.get("detectedAt") or current.get("timestampUtc"))
        integrated_at = current.get("integratedAt")
        if integrated_at:
            current["timeToDetectionSeconds"] = max(0.0, (parse_timestamp(detected) - parse_timestamp(str(integrated_at))).total_seconds())
        resolution = resolutions.get((str(current.get("taskId")), str(current.get("reference"))))
        if resolution:
            resolved = str(resolution.get("resolvedAt") or resolution.get("timestampUtc"))
            current["resolvedAt"] = resolved
            current["timeToResolutionSeconds"] = max(0.0, (parse_timestamp(resolved) - parse_timestamp(detected)).total_seconds())
            if resolution.get("fixCommit"):
                current["fixCommit"] = resolution.get("fixCommit")
        enriched.append(current)
    confirmed = [item for item in enriched if item.get("relation") == "confirmed"]
    suspected = [item for item in enriched if item.get("relation") == "suspected"]
    high_severity = [item for item in confirmed if item.get("severity") in {"P0", "P1"}]
    quality_failures = sum(1 for item in quality_signals if item.get("status") == "fail")
    integrated_items = [(task_key, item) for task_key, item in task_summaries.items() if item.get("state") == "Integrated"]
    integrated = [item for _, item in integrated_items]
    cohorts = {str(item.get("telemetryCohort")) for item in integrated if item.get("telemetryCohort")}
    every_task_has_cohort = bool(integrated) and all(item.get("telemetryCohort") for item in integrated)
    quality_complete = bool(integrated_items) and all(
        bool(summary.get("finalHead")) and (task_key, str(summary["finalHead"])) in quality_pass_heads
        for task_key, summary in integrated_items
    )
    bootstrap_correlations = {
        str((event.get("data") or {}).get("correlationId"))
        for event in unbound_events
        if (event.get("data") or {}).get("bootstrap") and (event.get("data") or {}).get("correlationId")
    }
    material_unbound = sum(
        1 for event in unbound_events
        if not (event.get("data") or {}).get("bootstrap")
        and not (
            event.get("type") == "tool.finished"
            and str((event.get("data") or {}).get("correlationId") or "") in bootstrap_correlations
        )
    )
    # Bootstrap hooks required to establish the first binding remain visible as
    # completeness loss; other unbound tool/agent evidence blocks comparison.
    lifecycle_complete = open_runs == 0 and incomplete_lifecycle_runs == 0 and material_unbound == 0
    eligible = len(integrated) >= 5 and every_task_has_cohort and len(cohorts) == 1 and quality_complete and lifecycle_complete and not high_severity and not quality_failures
    completeness_missing: list[str] = []
    if open_runs:
        completeness_missing.append("unfinished runs")
    if unbound:
        completeness_missing.append("unbound events")
    if incomplete_lifecycle_runs:
        completeness_missing.append("incomplete lifecycle stages")
    if load_settings(repo).get("mode") == "full" and not quality_complete:
        completeness_missing.append("quality evidence for each Integrated task")
    first_pass = sum(1 for item in task_summaries.values() if item.get("state") in {"Accepted", "Integrated"} and int(item.get("reviewCycle") or 0) <= 1)
    reviewed = sum(1 for item in task_summaries.values() if item.get("state") in {"Accepted", "Integrated"})
    candidate_to_accepted = [
        (parse_timestamp(states["Accepted"]) - parse_timestamp(states["Candidate"])).total_seconds()
        for states in state_timestamps.values() if "Candidate" in states and "Accepted" in states
    ]
    accepted_to_integrated = [
        (parse_timestamp(states["Integrated"]) - parse_timestamp(states["Accepted"])).total_seconds()
        for states in state_timestamps.values() if "Accepted" in states and "Integrated" in states
    ]
    observations = [
        {
            "schemaVersion": SCHEMA_VERSION,
            "taskId": item,
            "cohort": summary.get("telemetryCohort"),
            "mode": summary.get("mode"),
            "model": summary.get("actualModel") or summary.get("assignedModel"),
            "fixCycles": summary.get("fixCount", 0),
            "reviewCycles": summary.get("reviewCycle", 0),
            "validationFailures": summary.get("validationFailureCount", 0),
            "validationDebt": summary.get("validationDebtCount", 0),
            "integrated": summary.get("state") == "Integrated",
        }
        for item, summary in sorted(task_summaries.items())
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": iso_timestamp(),
        "filters": {"taskId": safe_label(task_id), "phaseId": safe_label(phase_id), "since": safe_label(since)},
        "capabilities": {"tokenUsage": "unsupported", "modelApiLatency": "unsupported"},
        "completeness": {"complete": not completeness_missing, "missing": completeness_missing, "unboundEvents": unbound, "openRuns": open_runs, "incompleteLifecycleRuns": incomplete_lifecycle_runs},
        "performance": {
            "runs": len(runs),
            "finishedRuns": len(lead_times),
            "medianLeadTimeSeconds": median(lead_times) if lead_times else None,
            "stageSeconds": dict(stage_seconds),
            "activeAgentSeconds": active_agent_seconds,
            "agentSecondsByRole": dict(agent_seconds_by_role),
            "parallelImplementSpanSeconds": (
                (max(end for _, end in implement_intervals) - min(start for start, _ in implement_intervals)).total_seconds()
                if implement_intervals else 0.0
            ),
            "toolCalls": tool_calls,
            "toolSeconds": tool_seconds,
            "toolSecondsByName": dict(tool_seconds_by_name),
            "completedTurns": completed_turns,
            "turnSeconds": turn_seconds,
            "outcomes": dict(outcomes),
        },
        "delivery": {
            "tasks": len(task_summaries),
            "integratedTasks": len(integrated),
            "fixCycles": sum(int(item.get("fixCount") or 0) for item in task_summaries.values()),
            "reviewCycles": sum(int(item.get("reviewCycle") or 0) for item in task_summaries.values()),
            "firstPassAcceptanceCount": first_pass,
            "firstPassAcceptanceRate": (first_pass / reviewed) if reviewed else None,
            "medianCandidateToAcceptedSeconds": median(candidate_to_accepted) if candidate_to_accepted else None,
            "medianAcceptedToIntegratedSeconds": median(accepted_to_integrated) if accepted_to_integrated else None,
            "validationFailures": sum(int(item.get("validationFailureCount") or 0) for item in task_summaries.values()),
            "validationDebt": sum(int(item.get("validationDebtCount") or 0) for item in task_summaries.values()),
            "gitChangeContext": {
                "files": sum(item.get("files", 0) for item in git_summaries.values()),
                "additions": sum(item.get("additions", 0) for item in git_summaries.values()),
                "deletions": sum(item.get("deletions", 0) for item in git_summaries.values()),
                "binaryFiles": sum(item.get("binaryFiles", 0) for item in git_summaries.values()),
            },
        },
        "quality": {"signals": quality_signals, "failedSignals": quality_failures},
        "regressions": {"confirmed": confirmed, "suspected": suspected, "confirmedP0P1": len(high_severity)},
        "analysis": {
            "status": "eligible_for_review" if eligible else "descriptive_only",
            "reason": None if eligible else "requires at least five comparable Integrated tasks with one cohort, complete lifecycle and passing quality evidence, and no blocking quality regressions",
        },
        "observations": observations,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    performance = report["performance"]
    delivery = report["delivery"]
    completeness = report["completeness"]
    lines = [
        "# Lemmings delivery report",
        "",
        f"Generated: {report['generatedAt']}",
        f"Completeness: {'complete' if completeness['complete'] else 'incomplete'}",
        f"Analysis: {report['analysis']['status']}",
        "",
        "## Performance",
        "",
        f"- Runs: {performance['runs']} ({performance['finishedRuns']} finished)",
        f"- Median lead time: {performance['medianLeadTimeSeconds']} seconds",
        f"- Active agent time: {performance['activeAgentSeconds']} seconds",
        f"- Tool calls: {performance['toolCalls']} ({performance['toolSeconds']} seconds)",
        "",
        "## Delivery and quality",
        "",
        f"- Integrated tasks: {delivery['integratedTasks']}",
        f"- Fix cycles: {delivery['fixCycles']}",
        f"- Review cycles: {delivery['reviewCycles']}",
        f"- Validation failures: {delivery['validationFailures']}",
        f"- Validation debt: {delivery['validationDebt']}",
        f"- Failed quality signals: {report['quality']['failedSignals']}",
        f"- Confirmed regressions: {len(report['regressions']['confirmed'])}",
        f"- Suspected regressions: {len(report['regressions']['suspected'])}",
        "",
        "Token usage and model API latency are unsupported in this version.",
    ]
    if completeness["missing"]:
        lines.extend(("", "Missing evidence: " + ", ".join(completeness["missing"])))
    tracked = report.get("taskQuality") or {}
    if tracked:
        lines.extend((
            "",
            "## Tracked implementation quality",
            "",
            f"- Tasks: {len(tracked.get('tasks') or [])}",
            f"- Incomplete tasks: {tracked.get('incompleteTasks', 0)}",
        ))
        for model, values in (tracked.get("attemptsByModel") or {}).items():
            lines.append(
                f"- {model}: {values.get('reviewedAttempts', 0)} reviewed attempts, "
                f"{values.get('implementationFindings', 0)} implementation findings"
            )
    if report.get("benchmark"):
        benchmark = report["benchmark"]
        lines.extend((
            "",
            "## Benchmark",
            "",
            f"- Status: {benchmark['status']}",
            f"- Comparable observations: {benchmark['observations']}",
            f"- Eligible for routing review: {benchmark['eligible']}",
        ))
    return "\n".join(lines) + "\n"


def telemetry_status(repo: Path) -> dict[str, Any]:
    settings = load_settings(repo)
    root = telemetry_root(repo)
    event_files = list((root / "events").rglob("*.json")) if (root / "events").is_dir() else []
    total_bytes = sum(path.stat().st_size for path in event_files)
    warnings: list[str] = []
    if total_bytes > int(settings["maxLocalMiB"]) * 1024 * 1024:
        warnings.append("telemetry storage exceeds maxLocalMiB")
    cutoff = utc_now() - timedelta(days=int(settings["retentionDays"]))
    stale = sum(1 for path in event_files if _event_file_timestamp(path) < cutoff)
    if stale:
        warnings.append(f"{stale} event files exceed retentionDays")
    return {
        "ok": True,
        "mode": settings["mode"],
        "root": str(root),
        "events": len(event_files),
        "sizeBytes": total_bytes,
        "retentionDays": settings["retentionDays"],
        "maxLocalMiB": settings["maxLocalMiB"],
        "warnings": warnings,
        "lastError": settings.get("lastError"),
        "capabilities": {"tokenUsage": "unsupported", "modelApiLatency": "unsupported"},
    }


def cleanup_events(repo: Path, older_than: str, execute: bool = False) -> dict[str, Any]:
    root = telemetry_root(repo) / "events"
    cutoff = utc_now() - parse_period(older_than)
    candidates = [path for path in root.rglob("*.json") if _event_file_timestamp(path) < cutoff] if root.is_dir() else []
    bytes_total = sum(path.stat().st_size for path in candidates)
    if execute:
        for path in candidates:
            path.unlink()
        for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True) if root.is_dir() else []:
            if not any(directory.iterdir()):
                directory.rmdir()
    return {"ok": True, "executed": execute, "olderThan": older_than, "files": len(candidates), "bytes": bytes_total}


def _event_file_timestamp(path: Path) -> datetime:
    try:
        return parse_timestamp(str(read_object(path)["timestampUtc"]))
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def record_hook_event(repo: Path, payload: Mapping[str, Any], policy_result: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    if not telemetry_enabled(repo):
        return None
    hook_event = str(payload.get("hook_event_name") or payload.get("event") or "")
    mapping = {
        "SessionStart": "session.started",
        "UserPromptSubmit": "turn.started",
        "Stop": "turn.finished",
        "PreToolUse": "tool.started",
        "PostToolUse": "tool.finished",
        "SubagentStart": "agent.started",
        "SubagentStop": "agent.finished",
    }
    event_type = mapping.get(hook_event)
    if not event_type:
        return None
    cwd = Path(str(payload.get("cwd") or os.getcwd())).resolve()
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    correlation = payload.get("tool_use_id") or payload.get("toolUseId") or payload.get("agent_id") or payload.get("agentId")
    if hook_event in {"UserPromptSubmit", "Stop"}:
        correlation = payload.get("turn_id") or payload.get("turnId")
    data: dict[str, Any] = {}
    if tool:
        data["tool"] = tool
    if correlation:
        data["correlationId"] = str(correlation)
    if policy_result:
        data["policyDecision"] = policy_result.get("decision")
        if hook_event == "SubagentStart" and isinstance(policy_result.get("contextPacket"), Mapping):
            packet = policy_result["contextPacket"]
            encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            context_task = payload.get("task") if isinstance(payload.get("task"), Mapping) else {}
            working = context_task.get("workingSet") or []
            data["context"] = {
                "bytes": len(encoded),
                "sections": len(packet),
                "workingSetCount": len(working),
                "expansions": int(payload.get("expansionsUsed", 0) or 0) + (1 if payload.get("contextExpansion") else 0),
                "warningCount": int(policy_result.get("warningCount") or 0),
            }
    if hook_event in {"SessionStart", "UserPromptSubmit"}:
        data["bootstrap"] = True
    elif hook_event == "PreToolUse":
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        command = str(tool_input.get("command") or "") if isinstance(tool_input, Mapping) else str(tool_input)
        normalized = " ".join(command.lower().split())
        if re.search(r"\blemmings(?:\.exe)?\s+(?:metrics\s+stage\b|wave\s+plan\b|on\b.*--task\b)", normalized):
            data["bootstrap"] = True
    response = payload.get("tool_response") or payload.get("toolResponse")
    if hook_event == "PostToolUse" and isinstance(response, Mapping):
        data["success"] = not bool(response.get("isError") or response.get("error"))
    task_value = payload.get("_telemetryTask") or payload.get("task")
    task = task_value if isinstance(task_value, Mapping) else None
    if task:
        data["task"] = summarize_task(task, payload.get("review") if isinstance(payload.get("review"), Mapping) else _review_for_task(repo, task))
    return record_event(
        repo, event_type, source="hook", cwd=cwd,
        session_id=str(payload.get("session_id") or payload.get("sessionId") or "") or None,
        turn_id=str(payload.get("turn_id") or payload.get("turnId") or "") or None,
        agent_id=str(payload.get("agent_id") or payload.get("agentId") or "") or None,
        task_id=(task or {}).get("taskId"),
        role=str(payload.get("agent_type") or payload.get("agentType") or (task or {}).get("role") or "") or None,
        model=str(payload.get("model") or ((task or {}).get("models") or {}).get("actual") or "") or None,
        data=data,
        dedupe_parts=(hook_event, payload.get("session_id"), payload.get("turn_id"), correlation, event_type),
    )


def record_telemetry_error(repo: Path, error: Exception) -> None:
    try:
        value = load_settings(repo)
        value["lastError"] = {
            "timestampUtc": iso_timestamp(),
            "type": type(error).__name__,
            "message": "telemetry recording failed",
        }
        atomic_write_json(settings_path(repo), value)
    except Exception:
        pass
