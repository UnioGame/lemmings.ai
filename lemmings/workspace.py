"""Read-only workspace sizing, inspection, and optional-tool discovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import git, git_common_dir, read_object, resolve_path

GIB = 1024 ** 3
WORKSPACE_BACKENDS = {"auto", "current", "code-worktree", "package-worktree", "unity-clone"}


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for item in path.rglob("*"):
        if ".git" in item.relative_to(path).parts:
            continue
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def _tracked_size(repo: Path) -> int:
    process = git(repo, "ls-files", "-z")
    if process.returncode:
        return 0
    total = 0
    for value in process.stdout.split("\0"):
        if value:
            path = repo / value
            if path.is_file():
                total += _size(path)
    return total


def _submodule_paths(repo: Path) -> list[Path]:
    config = repo / ".gitmodules"
    if not config.is_file():
        return []
    process = git(repo, "config", "--file", str(config), "--get-regexp", r"^submodule\..*\.path$")
    if process.returncode:
        return []
    return [(repo / line.split(maxsplit=1)[1]).resolve() for line in process.stdout.splitlines() if len(line.split(maxsplit=1)) == 2]


def find_game_project(repo: Path, profile: Mapping[str, Any] | None = None) -> Path | None:
    configured = ((profile or {}).get("game") or {}).get("projectPath")
    if configured:
        candidate = resolve_path(repo, str(configured))
        if candidate and (candidate / "ProjectSettings").is_dir() and (candidate / "Assets").is_dir():
            return candidate
    candidates = [repo, repo / "GameClient"]
    candidates.extend(path.parent for path in repo.glob("*/ProjectSettings/ProjectVersion.txt"))
    for candidate in candidates:
        if (candidate / "ProjectSettings").is_dir() and (candidate / "Assets").is_dir():
            return candidate.resolve()
    return None


def _package_root(repo: Path, profile: Mapping[str, Any] | None = None) -> Path | None:
    configured = ((profile or {}).get("tooling") or {}).get("root")
    candidate = resolve_path(repo, configured)
    if candidate and candidate.is_dir():
        return candidate
    process = git(repo, "ls-files", "*package.json")
    if process.returncode:
        return None
    for value in process.stdout.splitlines():
        path = repo / value
        try:
            package = read_object(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if package.get("name") == "unigame.ai.lemmings":
            return path.parent.resolve()
    return None


def resolve_tool_root(repo: Path, profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve optional tooling without making it a prerequisite for the skill."""
    environment = git_common_dir(repo) / "lemmings" / "environment.json"
    if environment.is_file():
        value = read_object(environment)
        root = resolve_path(repo, value.get("toolRoot")) if value.get("schemaVersion") == 1 else None
        if root and root.is_dir():
            return {"available": True, "root": str(root), "source": "git-common-environment"}
    package = _package_root(repo, profile)
    if package:
        source = "profile" if ((profile or {}).get("tooling") or {}).get("root") else "package-detection"
        return {"available": True, "root": str(package), "source": source}
    return {"available": False, "root": None, "source": "native-fallback"}


def estimate_workspace(
    repo: Path,
    profile: Mapping[str, Any] | None = None,
    backend: str = "auto",
) -> dict[str, Any]:
    if backend not in WORKSPACE_BACKENDS:
        raise ValueError(f"unknown workspace backend: {backend}")
    game = find_game_project(repo, profile)
    selected = "code-worktree" if backend == "auto" else backend
    tracked = _tracked_size(repo) if selected in {"code-worktree", "unity-clone"} else 0
    submodules = sum(_size(path) for path in _submodule_paths(repo)) if selected in {"code-worktree", "unity-clone"} else 0
    cache = _size(game / "Library") if game and selected == "unity-clone" else 0
    if selected == "current":
        estimate = 0
    elif selected == "package-worktree":
        estimate = _size(_package_root(repo, profile) or repo)
    elif selected == "code-worktree":
        estimate = tracked + submodules
    else:
        estimate = tracked + submodules + cache
    threshold = 10.0
    estimated_gib = estimate / GIB
    approval = selected != "current" and estimated_gib > threshold
    if approval:
        reason = f"Estimated workspace exceeds {threshold:g} GiB"
    elif selected != "current":
        reason = "Workspace estimate is within the configured limit"
    else:
        reason = "Current checkout does not require workspace approval"
    return {
        "backend": selected,
        "trackedGiB": round(tracked / GIB, 3),
        "submodulesGiB": round(submodules / GIB, 3),
        "expectedCacheGiB": round(cache / GIB, 3),
        "estimatedGiB": round(estimated_gib, 3),
        "approvalRequired": approval,
        "reason": reason,
    }


def inspect_workspaces(repo: Path, profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    process = git(repo, "worktree", "list", "--porcelain")
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    if not process.returncode:
        for line in [*process.stdout.splitlines(), ""]:
            if not line:
                if current:
                    worktrees.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value or True
    validation = ((((profile or {}).get("game") or {}).get("workspace") or {}).get("validationPath"))
    validation_path = resolve_path(repo, validation) if validation else None
    return {
        "ok": process.returncode == 0,
        "worktrees": worktrees,
        "validationClone": {
            "configured": bool(validation),
            "path": str(validation_path) if validation_path else None,
            "exists": bool(validation_path and validation_path.is_dir()),
        },
        "tooling": resolve_tool_root(repo, profile),
    }
