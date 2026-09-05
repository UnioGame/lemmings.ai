"""Tracked implementation-quality metrics derived from Task and Review artifacts."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    SCHEMA_VERSION,
    FINDING_ORIGINS,
    FINDING_PRIORITIES,
    REVIEW_STATES,
    read_object,
    resolve_path,
)


def _inside_repo(repo: Path, reference: str) -> Path | None:
    target = resolve_path(repo, reference)
    if target is None:
        return None
    try:
        target.relative_to(repo.resolve())
    except ValueError:
        return None
    return target


def _load_reviews(repo: Path, task: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    references = task.get("reviewHistory")
    references = references if isinstance(references, list) else []
    reviews: list[dict[str, Any]] = []
    missing: list[str] = []
    for reference in references:
        if not isinstance(reference, str):
            missing.append(str(reference))
            continue
        target = _inside_repo(repo, reference)
        if target is None or not target.is_file():
            missing.append(reference)
            continue
        try:
            reviews.append(read_object(target))
        except (OSError, ValueError):
            missing.append(reference)
    return reviews, missing


def _load_review(repo: Path, reference: Any) -> dict[str, Any] | None:
    if not isinstance(reference, str):
        return None
    target = _inside_repo(repo, reference)
    if target is None or not target.is_file():
        return None
    try:
        return read_object(target)
    except (OSError, ValueError):
        return None


def _empty_findings() -> dict[str, dict[str, int]]:
    return {
        origin: {"total": 0, **{priority: 0 for priority in sorted(FINDING_PRIORITIES)}}
        for origin in sorted(FINDING_ORIGINS)
    }


def summarize_quality(repo: Path, task: Mapping[str, Any], outcome: str | None = None) -> dict[str, Any]:
    execution = task.get("execution") or {}
    attempts = execution.get("attempts") if isinstance(execution, Mapping) else None
    attempts = attempts if isinstance(attempts, list) else []
    reviews, missing_reviews = _load_reviews(repo, task)
    findings = _empty_findings()
    unattributed = 0
    for review in reviews:
        for finding in review.get("findings") or []:
            if not isinstance(finding, Mapping):
                unattributed += 1
                continue
            origin = finding.get("origin")
            priority = finding.get("priority")
            if origin not in FINDING_ORIGINS or priority not in FINDING_PRIORITIES:
                unattributed += 1
                continue
            findings[str(origin)][str(priority)] += 1
            findings[str(origin)]["total"] += 1

    models = [str(item.get("actualModel")) for item in attempts if isinstance(item, Mapping) and item.get("actualModel")]
    validation_failures = sum(
        int(item.get("validationFailures") or 0)
        for item in attempts
        if isinstance(item, Mapping) and isinstance(item.get("validationFailures"), int)
    )
    review_statuses = [review.get("status") for review in reviews]
    review_groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for review in reviews:
        subject = review.get("subject") if isinstance(review.get("subject"), Mapping) else {}
        key = (str(subject.get("headSha") or ""), int(review.get("cycle") or 0))
        review_groups[key].append(review)
    incomplete: list[str] = []
    if not attempts:
        incomplete.append("execution.attempts")
    if not task.get("reviewHistory"):
        incomplete.append("reviewHistory")
    if missing_reviews:
        incomplete.append("missing review artifacts")
    if unattributed:
        incomplete.append("unclassified findings")
    for index, attempt in enumerate(attempts, 1):
        if not isinstance(attempt, Mapping) or not all(
            attempt.get(name) not in (None, "")
            for name in ("attempt", "kind", "actualModel", "headSha", "reviewRef", "reviewStatus")
        ):
            incomplete.append(f"attempt {index}")
            continue
        if attempt.get("reviewStatus") not in REVIEW_STATES:
            incomplete.append(f"attempt {index} review status")
    history = task.get("reviewHistory") or []
    current_review_refs = [task.get("reviewRef"), *(task.get("crossReviewRefs") or [])]
    if any(reference and reference not in history for reference in current_review_refs):
        incomplete.append("current review missing from reviewHistory")

    changes = sum(left != right for left, right in zip(models, models[1:]))
    repair_cycles = sum(
        item.get("kind") == "fix" for item in attempts if isinstance(item, Mapping)
    )
    first_group = next(iter(review_groups.values()), [])
    first_pass = bool(first_group) and all(review.get("status") == "Accepted" for review in first_group)
    return {
        "complete": not incomplete,
        "incompleteReasons": list(dict.fromkeys(incomplete)),
        "reviewCycles": len(review_groups),
        "repeatedReviews": max(0, len(review_groups) - 1),
        "repairCycles": repair_cycles,
        "firstPassAccepted": first_pass,
        "workerModelChanges": changes,
        "validationFailures": validation_failures,
        "findings": findings,
        "unclassifiedFindings": unattributed,
        "initialWorkerModel": models[0] if models else None,
        "finalWorkerModel": models[-1] if models else None,
        "workerModelsByAttempt": [
            {
                "attempt": item.get("attempt"),
                "kind": item.get("kind"),
                "actualModel": item.get("actualModel"),
            }
            for item in attempts if isinstance(item, Mapping)
        ],
        "workerRouteEscalated": any(left != right for left, right in zip(models, models[1:])),
        "outcome": outcome or (task.get("qualitySummary") or {}).get("outcome"),
        "finalState": task.get("state"),
    }


def _task_paths(repo: Path, profile: Mapping[str, Any]) -> list[Path]:
    patterns = profile.get("taskGlobs") or ["docs/tasks/**/*.json"]
    if isinstance(patterns, str):
        patterns = [patterns]
    paths: set[Path] = set()
    for pattern in patterns:
        if Path(str(pattern)).is_absolute():
            continue
        paths.update(path for path in repo.glob(str(pattern)) if path.is_file())
    return sorted(paths)


def build_quality_report(repo: Path, profile: Mapping[str, Any], task_id: str | None = None) -> dict[str, Any]:
    task_rows: list[dict[str, Any]] = []
    attempt_models: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"attempts": 0, "reviewedAttempts": 0, "implementationFindings": 0, **{priority: 0 for priority in sorted(FINDING_PRIORITIES)}}
    )
    route_models: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"reviewedTasks": 0, "integratedTasks": 0, "firstPassAccepted": 0, "reviewCycles": 0, "repairCycles": 0, "validationFailures": 0, "tasksWithValidationFailures": 0, "escalations": 0}
    )
    comparison_counts: dict[tuple[str, str], int] = defaultdict(int)

    for path in _task_paths(repo, profile):
        try:
            task = read_object(path)
        except (OSError, ValueError):
            continue
        if task.get("schemaVersion") != SCHEMA_VERSION or not task.get("taskId") or not isinstance(task.get("models"), Mapping):
            continue
        if task_id and str(task.get("taskId")) != task_id:
            continue
        summary = summarize_quality(repo, task)
        attempts = (task.get("execution") or {}).get("attempts") or []
        for attempt in attempts:
            if not isinstance(attempt, Mapping) or not attempt.get("actualModel"):
                continue
            model = str(attempt["actualModel"])
            bucket = attempt_models[model]
            bucket["attempts"] += 1
            review = _load_review(repo, attempt.get("reviewRef"))
            if review:
                bucket["reviewedAttempts"] += 1
                for finding in review.get("findings") or []:
                    if isinstance(finding, Mapping) and finding.get("origin") == "implementation" and finding.get("priority") in FINDING_PRIORITIES:
                        bucket["implementationFindings"] += 1
                        bucket[str(finding["priority"])] += 1
        initial = summary.get("initialWorkerModel")
        if initial:
            route = route_models[str(initial)]
            if summary["reviewCycles"]:
                route["reviewedTasks"] += 1
            if task.get("state") == "Integrated":
                route["integratedTasks"] += 1
            route["firstPassAccepted"] += int(bool(summary["firstPassAccepted"]))
            route["reviewCycles"] += int(summary["reviewCycles"])
            route["repairCycles"] += int(summary["repairCycles"])
            route["validationFailures"] += int(summary["validationFailures"])
            route["tasksWithValidationFailures"] += int(summary["validationFailures"] > 0)
            route["escalations"] += int(bool(summary["workerRouteEscalated"]))
            cohort = task.get("telemetryCohort")
            if task.get("state") == "Integrated" and cohort and summary["complete"] and not summary["unclassifiedFindings"]:
                comparison_counts[(str(cohort), str(initial))] += 1
        task_rows.append({"taskId": task.get("taskId"), "path": path.relative_to(repo).as_posix(), **summary})

    for bucket in attempt_models.values():
        reviewed = bucket["reviewedAttempts"]
        bucket["averageImplementationFindings"] = bucket["implementationFindings"] / reviewed if reviewed else None
    for bucket in route_models.values():
        reviewed = bucket["reviewedTasks"]
        bucket["firstPassAcceptanceRate"] = bucket["firstPassAccepted"] / reviewed if reviewed else None
        bucket["averageReviewCycles"] = bucket["reviewCycles"] / reviewed if reviewed else None
        bucket["averageRepairCycles"] = bucket["repairCycles"] / reviewed if reviewed else None
        bucket["validationFailureRate"] = bucket["tasksWithValidationFailures"] / reviewed if reviewed else None
        bucket["escalationRate"] = bucket["escalations"] / reviewed if reviewed else None

    comparable: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (cohort, model), count in sorted(comparison_counts.items()):
        comparable[cohort].append({"model": model, "integratedTasks": count, "eligible": count >= 5})
    recommendations = [
        {"cohort": cohort, "models": [item["model"] for item in items if item["eligible"]], "recommendation": "compare-routing"}
        for cohort, items in comparable.items()
        if sum(item["eligible"] for item in items) >= 2
    ]
    return {
        "tasks": task_rows,
        "incompleteTasks": sum(not item["complete"] for item in task_rows),
        "attemptsByModel": dict(sorted(attempt_models.items())),
        "routesByInitialModel": dict(sorted(route_models.items())),
        "comparison": {
            "minimumIntegratedTasksPerModel": 5,
            "cohorts": dict(comparable),
            "recommendations": recommendations,
        },
    }
