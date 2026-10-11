"""The brief format and an advisory size/shape check.

A brief is the only task contract a worker gets. Short briefs with references instead of prose keep every agent
request cheap, because the whole brief is re-read on each of them. The manager decides what to do with a warning;
nothing here blocks a dispatch.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

TARGET_BYTES = 4096
CEILING_BYTES = 16384
MAX_ACCEPTANCE = 5
MAX_CONTEXT = 10
MAX_CODE_LINES = 20
REQUIRED = ("Task", "Goal", "Acceptance", "Owned paths", "Checks", "Context")
RECOMMENDED = ("Agent", "Stop")
HEADER = re.compile(r"^(Task|Agent|Goal|Acceptance|Owned paths|Checks|Risks|Context|Stop):(.*)$")
POLLING = re.compile(r"\b(poll|echo waiting|sleep \d+|until grep|busy[- ]wait)\b", re.IGNORECASE)

TEMPLATE = """Task: {task_id}
Agent: <agent name>
Goal: <one sentence>
Acceptance:
- <observable criterion, at most 5>
Owned paths: <paths or globs the writer may change>
Checks: <exact commands and the expected result>
Context: <at most 10 items, one per line as `path#symbol: why`; references, not prose>
Stop: <done when ...; return blocked when ...>"""


def template(task_id: str = "<ID>") -> str:
    return TEMPLATE.format(task_id=task_id)


def policy(config: Mapping[str, Any] | None) -> dict[str, int]:
    """Advisory sizes from `.agents/lemmings.json` → `contextPolicy` (targetBytes, ceilingBytes)."""
    raw = (config or {}).get("contextPolicy") or {}
    target = raw.get("targetBytes") if isinstance(raw.get("targetBytes"), int) else TARGET_BYTES
    ceiling = raw.get("ceilingBytes") if isinstance(raw.get("ceilingBytes"), int) else CEILING_BYTES
    return {"targetBytes": target, "ceilingBytes": max(ceiling, target)}


def check(text: str, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    sizes = policy(config)
    size = len(text.encode("utf-8"))
    blocks: dict[str, list[str]] = {}
    inline: dict[str, str] = {}
    current = None
    fenced = code_lines = 0
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = 1 - fenced
            continue
        if fenced:
            code_lines += 1
        match = None if fenced else HEADER.match(line)
        if match:
            current = match.group(1)
            inline[current] = match.group(2).strip()
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    warnings = []
    missing = [name for name in REQUIRED if name not in blocks]
    if missing:
        warnings.append("missing sections: " + ", ".join(missing))
    if "Task" in blocks and not inline["Task"]:
        warnings.append("Task needs the journal id, so usage can be tied to the task")
    if size > sizes["ceilingBytes"]:
        warnings.append(f"{size} bytes is over the {sizes['ceilingBytes']} ceiling: split the task or move content into files the brief references")
    elif size > sizes["targetBytes"]:
        warnings.append(f"{size} bytes is over the {sizes['targetBytes']} target: replace prose with `path#symbol` references")
    bullets = lambda name: [item for item in blocks.get(name, []) if item.lstrip().startswith(("- ", "* "))]  # noqa: E731
    if len(bullets("Acceptance")) > MAX_ACCEPTANCE:
        warnings.append(f"Acceptance has {len(bullets('Acceptance'))} criteria; keep at most {MAX_ACCEPTANCE} or split the task")
    context = len(bullets("Context")) or len([item for item in blocks.get("Context", []) if item.strip()])
    if context > MAX_CONTEXT:
        warnings.append(f"Context lists {context} items; keep at most {MAX_CONTEXT}")
    if code_lines > MAX_CODE_LINES:
        warnings.append(f"{code_lines} lines of code in the brief; reference the file instead of pasting it")
    if POLLING.search(text):
        warnings.append("the brief tells the agent to poll or wait; long commands run once and the result is read at the end")
    return {"ok": True, "bytes": size, **sizes, "sections": sorted(blocks), "warnings": warnings}
