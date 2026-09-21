"""Summarize dispatch run logs and the task journal (docs/tasks) without calling any model."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

TOKEN_KEYS = {
    "inputTokens": ("input_tokens", "inputTokens", "input", "prompt_tokens"),
    "cachedInputTokens": ("cached_input_tokens", "cache_read_input_tokens", "cacheReadInputTokens", "cache_read"),
    "outputTokens": ("output_tokens", "outputTokens", "output", "completion_tokens"),
    "reasoningTokens": ("reasoning_output_tokens", "reasoning_tokens", "reasoning"),
}


def normalize_usage(raw: Mapping[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(raw, Mapping):
        return None
    result = {}
    for name, keys in TOKEN_KEYS.items():
        value = next((raw[key] for key in keys if isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool)), None)
        if value is not None:
            result[name] = int(value)
    return result or None


def runs_dir(repo: Path) -> Path:
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=repo,
                            capture_output=True, text=True, check=True).stdout.strip()
    return Path(common) / "lemmings" / "runs"


def load_runs(directory: Path, since: datetime | None = None) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(directory.glob("*/result.json")):
        stamp = path.parent.name.split("-", 1)[0]
        try:
            started = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if since and started < since:
            continue
        runs.append({**value, "started": started.isoformat()})
    return runs


def summarize(runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "runs": 0, "completed": 0, "failed": 0, "modelMismatch": 0, "fallbacks": 0,
        "accepted": 0, "changesRequested": 0, "elapsedSeconds": 0.0, "tokens": defaultdict(int), "usageMissing": 0})
    for run in runs:
        if run.get("status") == "dry-run":
            continue
        key = f"{run.get('role')}|{run.get('host') or '-'}|{run.get('requestedModel') or 'default'}"
        group = groups[key]
        group["runs"] += 1
        group["completed"] += run.get("status") == "completed"
        group["failed"] += run.get("status") in {"failed", "empty-report"}
        group["modelMismatch"] += run.get("status") == "model-mismatch"
        group["fallbacks"] += bool(run.get("fallbackUsed"))
        group["accepted"] += run.get("verdict") == "Accepted"
        group["changesRequested"] += run.get("verdict") == "ChangesRequested"
        group["elapsedSeconds"] += float(run.get("elapsedSeconds") or 0)
        usage = normalize_usage(run.get("usage"))
        if usage is None:
            group["usageMissing"] += 1
        for name, value in (usage or {}).items():
            group["tokens"][name] += value
    rows = []
    for key, group in sorted(groups.items()):
        role, host, model = key.split("|")
        rows.append({"role": role, "host": host, "model": model, **group, "tokens": dict(group["tokens"]),
                     "elapsedSeconds": round(group["elapsedSeconds"], 1)})
    return {"routes": rows, "totalRuns": sum(row["runs"] for row in rows)}


STATUS = re.compile(r"(Not started|In progress|In review|Repair|Escalated|Done|Deferred|Blocked)")
LOG = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (Not started|In progress|In review|Repair(?: \d+)?|Escalated|Done|Deferred|Blocked)")


def summarize_tasks(directory: Path) -> dict[str, Any] | None:
    """Status counts, repair and escalation totals, and lead time from the task journal."""
    index = directory / "TASKS.md"
    if not index.is_file():
        return None
    counts: dict[str, int] = defaultdict(int)
    lead_hours: list[float] = []
    repairs = escalations = 0
    in_table = False
    for line in index.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip()[1:-1])] if line.strip().startswith("|") else []
        if cells[:1] == ["ID"]:
            in_table = True
            continue
        if not in_table or not cells or set(cells[0]) <= {"-", " "}:
            if in_table and not cells:
                in_table = False
            continue
        match = STATUS.match(cells[-1])
        counts[match.group(1) if match else "Unknown"] += 1
        task_file = directory / f"{cells[0]}.md"
        if not task_file.is_file():
            continue
        started = finished = None
        for entry in task_file.read_text(encoding="utf-8").splitlines():
            found = LOG.match(entry.strip())
            if not found:
                continue
            moment = datetime.strptime(found.group(1), "%Y-%m-%d %H:%M")
            status = found.group(2)
            repairs += status.startswith("Repair")
            escalations += status == "Escalated"
            if status == "In progress" and started is None:
                started = moment
            if status == "Done":
                finished = moment
        if started and finished and finished >= started:
            lead_hours.append((finished - started).total_seconds() / 3600)
    return {"counts": dict(counts), "tasks": sum(counts.values()), "repairRounds": repairs, "escalations": escalations,
            "medianLeadHours": round(sorted(lead_hours)[len(lead_hours) // 2], 1) if lead_hours else None}


def render_markdown(summary: Mapping[str, Any]) -> str:
    lines = ["| Role | Host | Model | Runs | Completed | Failed | Mismatch | Fallback | Accepted | Changes | Seconds | Input | Output |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in summary["routes"]:
        tokens = row["tokens"]
        lines.append(f"| {row['role']} | {row['host']} | {row['model']} | {row['runs']} | {row['completed']} | "
                     f"{row['failed']} | {row['modelMismatch']} | {row['fallbacks']} | {row['accepted']} | "
                     f"{row['changesRequested']} | {row['elapsedSeconds']} | {tokens.get('inputTokens', '-')} | "
                     f"{tokens.get('outputTokens', '-')} |")
    journal = summary.get("taskJournal")
    if journal:
        counts = ", ".join(f"{name}: {count}" for name, count in sorted(journal["counts"].items()))
        lines += ["", f"Tasks: {journal['tasks']} ({counts}). Repair rounds: {journal['repairRounds']}. "
                  f"Escalations: {journal['escalations']}. Median lead time: {journal['medianLeadHours']} h."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lemmings-telemetry", description="Offline report over Lemmings dispatch runs")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--runs", help="explicit runs directory (default: <git-common-dir>/lemmings/runs)")
    parser.add_argument("--days", type=int, help="only include runs from the last N days")
    parser.add_argument("--tasks-dir", default="docs/tasks", help="task journal directory, relative to --repo")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args(argv)
    directory = Path(args.runs) if args.runs else runs_dir(Path(args.repo))
    since = datetime.now(timezone.utc) - timedelta(days=args.days) if args.days else None
    summary = summarize(load_runs(directory, since)) if directory.is_dir() else summarize([])
    summary["taskJournal"] = summarize_tasks(Path(args.repo) / args.tasks_dir)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(summary, indent=2) if args.format == "json" else render_markdown(summary), end="" if args.format == "markdown" else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
