"""Task journal: a small index table (docs/tasks/TASKS.md) plus one Markdown file per task.

The manager is the only writer. The index holds one short row and the current status per task;
each task file holds the brief, a timestamped log of status changes, and evidence. These helpers
make the edits deterministic; the same files can be maintained by hand without Python.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .gitutil import HelperError, git

DEFAULT_DIR = "docs/tasks"
INDEX = "TASKS.md"
COLUMNS = ("ID", "Task", "Where", "Who", "Depends on", "Status")
STATUSES = ("Not started", "In progress", "In review", "Repair", "Escalated", "Done", "Deferred", "Blocked")
FINISHED = ("Done",)
ACTIVE = ("In progress", "In review", "Repair", "Escalated")
TASK_ID = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
STATUS = re.compile(r"(Not started|In progress|In review|Repair(?: \d+)?|Escalated|Done|Deferred|Blocked)\b")
COMMIT = re.compile(r"`([0-9a-f]{7,40})`")
LOG_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) " + STATUS.pattern + r"(?: — (.*))?$")
NONE_MARKS = {"", "—", "-", "–", "none"}


def tasks_dir(repo: Path, config: dict[str, Any]) -> Path:
    return repo / ((config.get("tasks") or {}).get("dir") or DEFAULT_DIR)


def _cells(line: str) -> list[str]:
    body = line.strip()
    if not (body.startswith("|") and body.endswith("|")):
        return []
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body[1:-1])]


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def status_word(cell: str) -> str | None:
    """The status vocabulary word a cell starts with ("Repair 2" -> "Repair")."""
    match = STATUS.match(cell.strip())
    if not match:
        return None
    return "Repair" if match.group(1).startswith("Repair") else match.group(1)


class Journal:
    def __init__(self, directory: Path):
        self.dir = directory
        self.index = directory / INDEX
        self.lines: list[str] = self.index.read_text(encoding="utf-8").splitlines() if self.index.is_file() else []
        self.header_at: int | None = None
        self.rows: list[dict[str, Any]] = []
        for number, line in enumerate(self.lines):
            cells = _cells(line)
            if self.header_at is None:
                if tuple(cells) == COLUMNS:
                    self.header_at = number
                continue
            if number == self.header_at + 1:
                continue  # separator row
            if not cells:
                break
            if len(cells) != len(COLUMNS):
                raise HelperError(f"{self.index}:{number + 1}: expected {len(COLUMNS)} columns")
            self.rows.append({"line": number, **dict(zip(("id", "task", "where", "who", "depends", "status"), cells))})

    # -- reading ---------------------------------------------------------------------------------
    def find(self, task_id: str) -> dict[str, Any]:
        matches = [row for row in self.rows if row["id"] == task_id]
        if not matches:
            raise HelperError(f"no task {task_id} in {self.index}")
        return matches[0]

    def dependencies(self, row: dict[str, Any]) -> list[str]:
        """Dependency ids; a short form like `05` means the same prefix as the row (NCORE-05)."""
        ids = {item["id"] for item in self.rows}
        prefix = row["id"].rsplit("-", 1)[0] + "-" if "-" in row["id"] else ""
        result = []
        for token in re.split(r"[,\s]+", row["depends"]):
            if token.lower() in NONE_MARKS:
                continue
            if token not in ids and prefix and prefix + token in ids:
                token = prefix + token
            result.append(token)
        return result

    def task_file(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.md"

    def log(self, task_id: str) -> list[dict[str, str]]:
        path = self.task_file(task_id)
        if not path.is_file():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            match = LOG_LINE.match(line.strip())
            if match:
                entries.append({"time": match.group(1), "status": match.group(2), "note": match.group(3) or ""})
        return entries

    # -- writing ---------------------------------------------------------------------------------
    def _write(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index.write_text("\n".join(self.lines) + "\n", encoding="utf-8", newline="\n")

    def _ensure_table(self) -> None:
        if self.header_at is not None:
            return
        if not self.lines:
            self.lines = ["# Tasks", "",
                          "One row per task. Status starts with: " + ", ".join(STATUSES) + ". Details live in the linked task file.", ""]
        elif self.lines[-1].strip():
            self.lines.append("")
        self.header_at = len(self.lines)
        self.lines += ["| " + " | ".join(COLUMNS) + " |", "| " + " | ".join("---" for _ in COLUMNS) + " |"]

    def add(self, task_id: str, title: str, where: str, who: str, depends: str, brief: str) -> dict[str, Any]:
        if not TASK_ID.fullmatch(task_id):
            raise HelperError("task id must look like PROJ-12 or PROJ-12a")
        if any(row["id"] == task_id for row in self.rows):
            raise HelperError(f"task {task_id} already exists")
        path = self.task_file(task_id)
        if path.exists():
            raise HelperError(f"{path} already exists")
        self._ensure_table()
        status = f"Not started — [details]({task_id}.md)"
        row = "| " + " | ".join(_escape(value) for value in (task_id, title, where, who, depends or "—", status)) + " |"
        insert_at = (self.rows[-1]["line"] + 1) if self.rows else self.header_at + 2
        self.lines.insert(insert_at, row)
        self._write()
        body = brief.strip() or "Goal:\nAcceptance:\n- \nOwned paths:\nChecks:\nRisks:\nContext:"
        path.write_text(f"# {task_id} · {title}\n\n## Brief\n\n{body}\n\n## Log\n\n- {_now()} Not started — planned\n\n"
                        "## Evidence\n", encoding="utf-8", newline="\n")
        return {"id": task_id, "index": str(self.index), "file": str(path)}

    def update(self, task_id: str, status: str, note: str = "", commit: str | None = None,
               who: str | None = None) -> dict[str, Any]:
        match = STATUS.fullmatch(status.strip())
        if not match:
            raise HelperError(f"status must be one of: {', '.join(STATUSES)} (Repair may carry a round number)")
        if commit and not re.fullmatch(r"[0-9a-f]{7,40}", commit):
            raise HelperError("commit must be a hex SHA")
        row = self.find(task_id)
        cell = status + (f" (`{commit}`)" if commit else "") + (f": {note}" if note else "") + f" — [details]({task_id}.md)"
        cells = [row["id"], row["task"], row["where"], who or row["who"], row["depends"], cell]
        self.lines[row["line"]] = "| " + " | ".join(_escape(value) for value in cells) + " |"
        self._write()
        path = self.task_file(task_id)
        text = path.read_text(encoding="utf-8") if path.is_file() else f"# {task_id} · {row['task']}\n\n## Log\n"
        entry = f"- {_now()} {status}" + (f" — {note}" if note or commit else "")
        if commit:
            entry += (" " if note else "") + f"`{commit}`"
        text = _append_to_log(text, entry)
        path.write_text(text, encoding="utf-8", newline="\n")
        return {"id": task_id, "status": status, "log": entry}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _append_to_log(text: str, entry: str) -> str:
    lines = text.rstrip("\n").split("\n")
    if "## Log" not in [line.strip() for line in lines]:
        return "\n".join(lines) + f"\n\n## Log\n\n{entry}\n"
    start = [line.strip() for line in lines].index("## Log")
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    last = max((index for index in range(start + 1, end) if lines[index].startswith("- ")), default=start)
    insert_at = last + 1 if last > start else start + 1
    if last == start:
        lines.insert(insert_at, "")
        insert_at += 1
    lines.insert(insert_at, entry)
    return "\n".join(lines) + "\n"


def check(journal: Journal, repo: Path) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if journal.header_at is None:
        return {"errors": [f"{journal.index} has no task table with columns {' | '.join(COLUMNS)}"], "warnings": []}
    seen: set[str] = set()
    for row in journal.rows:
        task_id = row["id"]
        if not TASK_ID.fullmatch(task_id):
            errors.append(f"{task_id}: invalid id")
        if task_id in seen:
            errors.append(f"{task_id}: duplicate id")
        seen.add(task_id)
    ids = {row["id"] for row in journal.rows}
    graph: dict[str, list[str]] = {}
    for row in journal.rows:
        task_id, status = row["id"], status_word(row["status"])
        if status is None:
            errors.append(f"{task_id}: status must start with one of {', '.join(STATUSES)}")
        deps = journal.dependencies(row)
        graph[task_id] = deps
        for dep in deps:
            if dep not in ids:
                errors.append(f"{task_id}: unknown dependency {dep}")
        if status in ACTIVE + FINISHED:
            waiting = [dep for dep in deps if dep in ids and status_word(journal.find(dep)["status"]) not in FINISHED]
            if waiting:
                warnings.append(f"{task_id}: {status} while dependencies are not Done: {', '.join(waiting)}")
        if status in FINISHED:
            commits = COMMIT.findall(row["status"])
            if not commits:
                errors.append(f"{task_id}: Done needs at least one commit in backticks")
            elif not any(git(repo, "cat-file", "-e", sha + "^{commit}", check=False).returncode == 0 for sha in commits):
                warnings.append(f"{task_id}: none of {', '.join(commits)} is in this repository (another repository?)")
        for target in re.findall(r"\]\(([^)#\s]+)\)", row["status"]):
            if not target.startswith(("http://", "https://")) and not (journal.dir / target).exists():
                errors.append(f"{task_id}: link target {target} does not exist")
        entries = journal.log(task_id)
        if journal.task_file(task_id).is_file() and entries and status and \
                status_word(entries[-1]["status"]) != status:
            errors.append(f"{task_id}: index says {status} but the last log entry says {entries[-1]['status']}")
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            errors.append("dependency cycle: " + " -> ".join(trail[trail.index(node):] + [node]))
            return
        state[node] = 1
        for dep in graph.get(node, []):
            if dep in graph:
                visit(dep, trail + [node])
        state[node] = 2

    for node in graph:
        visit(node, [])
    return {"errors": errors, "warnings": warnings}


def ready(journal: Journal) -> list[dict[str, Any]]:
    """Not-started tasks whose dependencies are all Done: the next wave."""
    statuses = {row["id"]: status_word(row["status"]) for row in journal.rows}
    wave = []
    for row in journal.rows:
        if statuses[row["id"]] != "Not started":
            continue
        deps = journal.dependencies(row)
        if all(statuses.get(dep) in FINISHED for dep in deps):
            wave.append({"id": row["id"], "task": row["task"], "where": row["where"], "who": row["who"], "depends": deps})
    return wave


def summary(journal: Journal) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in journal.rows:
        word = status_word(row["status"]) or "Unknown"
        counts[word] = counts.get(word, 0) + 1
    return {"counts": counts,
            "tasks": [{"id": row["id"], "task": row["task"], "who": row["who"], "status": status_word(row["status"]),
                       "depends": journal.dependencies(row)} for row in journal.rows]}
