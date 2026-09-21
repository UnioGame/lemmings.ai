"""Check a candidate's real changed paths against owned and forbidden rules."""
from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path

from .gitutil import HelperError, git


def normalize(path: str) -> str:
    value = path.replace("\\", "/").strip()
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise HelperError(f"path must be repository-relative: {path}")
    value = posixpath.normpath(value)
    if value == ".." or value.startswith("../"):
        raise HelperError(f"path escapes the repository: {path}")
    return "" if value == "." else value


def _pattern(rule: str) -> re.Pattern[str]:
    rule = normalize(rule).rstrip("/")
    if not any(char in rule for char in "*?["):
        # A plain rule owns the file itself or everything below the directory.
        return re.compile(re.escape(rule) + r"(/.*)?", re.IGNORECASE if os.name == "nt" else 0)
    parts, index = [], 0
    while index < len(rule):
        if rule.startswith("**/", index):
            parts.append("(.*/)?"); index += 3
        elif rule.startswith("**", index):
            parts.append(".*"); index += 2
        elif rule[index] == "*":
            parts.append("[^/]*"); index += 1
        elif rule[index] == "?":
            parts.append("[^/]"); index += 1
        else:
            parts.append(re.escape(rule[index])); index += 1
    return re.compile("".join(parts), re.IGNORECASE if os.name == "nt" else 0)


def matches(path: str, rules: list[str]) -> bool:
    return any(_pattern(rule).fullmatch(path) for rule in rules)


def changed_paths(repo: Path, base: str, head: str = "HEAD", worktree: bool = False) -> list[str]:
    """Return every path touched between base and head (both sides of renames)."""
    args = ["diff", "-z", "--name-status", "--find-renames", base]
    if not worktree:
        args.append(head)
    fields = git(repo, *args).stdout.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        count = 2 if status[:1] in {"R", "C"} else 1
        paths.update(fields[index + 1:index + 1 + count])
        index += 1 + count
    if worktree:
        untracked = git(repo, "ls-files", "-z", "--others", "--exclude-standard").stdout.split("\0")
        paths.update(item for item in untracked if item)
    return sorted(normalize(path) for path in paths)


def check_scope(paths: list[str], owned: list[str], forbidden: list[str]) -> list[dict[str, str]]:
    violations = []
    for path in paths:
        if forbidden and matches(path, forbidden):
            violations.append({"path": path, "reason": "forbidden"})
        elif owned and not matches(path, owned):
            violations.append({"path": path, "reason": "outside owned paths"})
    return violations
