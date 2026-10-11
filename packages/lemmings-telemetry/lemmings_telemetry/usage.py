"""Token usage of Claude Code subagents, grouped by Lemmings task and by agent type, from local transcripts.

Reads `~/.claude/projects/<project>/**/*.jsonl` offline and calls no model. A subagent transcript belongs to the task named
on the first `Task: <ID>` line of its brief; the agent type comes from the parent transcript's `Agent` call that started it.
Costs are estimates from a price table (USD per 1M tokens: input, output, cache read, cache write) that only ranks load;
real limits are shown by the host's usage view. It covers the machine it runs on.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PRICES = {"opus": (5.0, 25.0, 0.5, 6.25), "sonnet": (3.0, 15.0, 0.3, 3.75), "haiku": (1.0, 5.0, 0.1, 1.25)}
TASK_LINE = re.compile(r"^\s*Task:\s*([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)", re.MULTILINE)
AGENT_ID = re.compile(r"agentId:\s*([0-9a-f]+)")
LEMMINGS = "lemmings-"


def _path(path: Path) -> str:
    """Windows long-path form: subagent transcripts live deep in the projects directory."""
    value = os.path.abspath(path)
    return "\\\\?\\" + value if os.name == "nt" and not value.startswith("\\\\?\\") else value


def project_slug(repo: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve()))


def cost(model: str, usage: dict[str, Any], prices: dict[str, Any] | None = None) -> float:
    table = prices or PRICES
    rate = next((value for key, value in table.items() if key in model), table.get("sonnet", PRICES["sonnet"]))
    return (usage.get("input_tokens", 0) * rate[0] + usage.get("output_tokens", 0) * rate[1]
            + usage.get("cache_read_input_tokens", 0) * rate[2] + usage.get("cache_creation_input_tokens", 0) * rate[3]) / 1e6


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return ""


def _lines(path: Path) -> Iterable[dict[str, Any]]:
    with open(_path(path), encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def collect(projects: Path, repo: Path | None, since: datetime | None, prices: dict[str, Any] | None = None) -> dict[str, Any]:
    slug = project_slug(repo) if repo else ""
    directories = [item for item in projects.iterdir() if item.is_dir() and (not slug or item.name.startswith(slug))] if projects.is_dir() else []
    calls: dict[str, str] = {}      # Agent tool_use id -> subagent_type
    started: dict[str, str] = {}    # agentId -> subagent_type
    files = [path for directory in directories for path in directory.rglob("*.jsonl")]
    for path in files:
        if "subagents" in path.parts:
            continue
        for entry in _lines(path):
            content = (entry.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == "Agent":
                    calls[block.get("id", "")] = str((block.get("input") or {}).get("subagent_type") or "general-purpose")
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    found = AGENT_ID.search(_text(block.get("content")))
                    if found:
                        started[found.group(1)] = calls[block["tool_use_id"]]
    tasks: dict[str, dict[str, Any]] = defaultdict(lambda: {"agents": 0, "requests": 0, "peakContext": 0, "estimatedUsd": 0.0, "models": set()})
    kinds: dict[str, dict[str, Any]] = defaultdict(lambda: {"agents": 0, "requests": 0, "estimatedUsd": 0.0})
    seen: set[tuple[Any, Any]] = set()
    for path in files:
        if "subagents" not in path.parts:
            continue
        agent = path.stem.removeprefix("agent-")
        kind = started.get(agent, "unknown")
        task, requests, spend, peak, models = None, 0, 0.0, 0, set()
        for entry in _lines(path):
            message = entry.get("message") or {}
            if task is None and entry.get("type") == "user":
                found = TASK_LINE.search(_text(message.get("content"))[:4000])
                task = found.group(1) if found else ""
            usage, stamp = message.get("usage"), entry.get("timestamp")
            if not isinstance(usage, dict) or not stamp or message.get("model") == "<synthetic>":
                continue
            moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            key = (message.get("id"), entry.get("requestId"))
            if (since and moment < since) or (key[0] and key in seen):
                continue
            seen.add(key)
            requests += 1
            spend += cost(str(message.get("model", "")), usage, prices)
            peak = max(peak, usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0))
            models.add(str(message.get("model", "?")).removeprefix("claude-"))
        if not requests:
            continue
        row = tasks[task or "(no Task line)"]
        row["agents"] += 1
        row["requests"] += requests
        row["peakContext"] = max(row["peakContext"], peak)
        row["estimatedUsd"] += spend
        row["models"] |= models
        kinds[kind]["agents"] += 1
        kinds[kind]["requests"] += requests
        kinds[kind]["estimatedUsd"] += spend
    total = sum(item["estimatedUsd"] for item in kinds.values())
    outside = sum(item["estimatedUsd"] for name, item in kinds.items() if not name.startswith(LEMMINGS))
    outside_agents = sum(item["agents"] for name, item in kinds.items() if not name.startswith(LEMMINGS))
    return {"estimatedUsd": round(total, 2), "subagents": sum(item["agents"] for item in kinds.values()),
            "outsideLemmings": {"agents": outside_agents, "estimatedUsd": round(outside, 2),
                                "share": round(outside / total, 3) if total else 0.0},
            "byAgentType": {name: {**item, "estimatedUsd": round(item["estimatedUsd"], 2)} for name, item in sorted(kinds.items(), key=lambda pair: -pair[1]["estimatedUsd"])},
            "byTask": {name: {**item, "models": sorted(item["models"]), "estimatedUsd": round(item["estimatedUsd"], 2)}
                       for name, item in sorted(tasks.items(), key=lambda pair: -pair[1]["estimatedUsd"])}}


def render(report: dict[str, Any], top: int) -> str:
    lines = [f"Subagents: {report['subagents']}, estimated ${report['estimatedUsd']}. Outside lemmings-*: "
             f"{report['outsideLemmings']['agents']} agents, ${report['outsideLemmings']['estimatedUsd']} "
             f"({report['outsideLemmings']['share']:.0%}).", "", "| Task | Agents | Requests | Peak context | Models | Est. $ |",
             "| --- | ---: | ---: | ---: | --- | ---: |"]
    for name, row in list(report["byTask"].items())[:top]:
        lines.append(f"| {name} | {row['agents']} | {row['requests']} | {row['peakContext'] // 1000}k | {', '.join(row['models'])} | {row['estimatedUsd']} |")
    lines += ["", "| Agent type | Agents | Requests | Est. $ |", "| --- | ---: | ---: | ---: |"]
    for name, row in report["byAgentType"].items():
        lines.append(f"| {name} | {row['agents']} | {row['requests']} | {row['estimatedUsd']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lemmings-telemetry usage", description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=".", help="repository whose Claude Code projects to read (worktrees included)")
    parser.add_argument("--all-projects", action="store_true", help="every project under the projects directory")
    parser.add_argument("--projects-dir", default=str(Path.home() / ".claude" / "projects"))
    parser.add_argument("--since", help="ISO time, for example the weekly limit reset: 2026-10-09T16:00Z")
    parser.add_argument("--prices", help="JSON file: model family -> [input, output, cache read, cache write] USD per 1M tokens")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args(argv)
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    prices = json.loads(Path(args.prices).read_text(encoding="utf-8")) if args.prices else None
    report = collect(Path(args.projects_dir), None if args.all_projects else Path(args.repo), since, prices)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, indent=2) if args.format == "json" else render(report, args.top), end="" if args.format == "markdown" else "\n")
    return 0
