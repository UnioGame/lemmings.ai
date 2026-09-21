"""Create, list, and safely remove isolated task workspaces."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .gitutil import HelperError, git, load_config, state_dir, toplevel

GIB = 1024 ** 3
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
DEFAULT_ROOT = "../lemmings-worktrees"
DEFAULT_LARGE_GIB = 10.0
LOCK_STALE_SECONDS = 60


def find_unity_project(repo: Path, config: dict[str, Any] | None = None) -> Path | None:
    configured = ((config or {}).get("game") or {}).get("projectPath")
    candidates = [repo / configured] if configured else []
    candidates += [repo, *(path.parent.parent for path in repo.glob("*/ProjectSettings/ProjectVersion.txt"))]
    for candidate in candidates:
        if (candidate / "ProjectSettings").is_dir() and (candidate / "Assets").is_dir():
            return candidate.resolve()
    return None


def _dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def estimate(repo: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tracked checkout bytes plus the engine cache a fresh copy will regenerate."""
    tracked = 0
    for item in git(repo, "ls-files", "-z").stdout.split("\0"):
        if item:
            try:
                tracked += (repo / item).stat().st_size
            except OSError:
                pass
    unity = find_unity_project(repo, config)
    cache = _dir_size(unity / "Library") if unity and (unity / "Library").is_dir() else 0
    limit = float(((config or {}).get("workspace") or {}).get("largeThresholdGiB", DEFAULT_LARGE_GIB))
    total = (tracked + cache) / GIB
    return {"trackedBytes": tracked, "engineCacheBytes": cache,
            "trackedGiB": round(tracked / GIB, 3), "engineCacheGiB": round(cache / GIB, 3),
            "estimatedGiB": round(total, 3), "limitGiB": limit, "approvalRequired": total > limit,
            "unityProject": str(unity) if unity else None}


def _registry_path(repo: Path) -> Path:
    return state_dir(repo) / "workspaces.json"


@contextmanager
def _registry(repo: Path) -> Iterator[dict[str, Any]]:
    path = _registry_path(repo)
    lock = path.with_suffix(".lock")
    deadline = time.monotonic() + 10
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise HelperError(f"workspace registry is locked: {lock}")
            time.sleep(0.1)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"entries": []}
        yield data
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        lock.unlink(missing_ok=True)


def _root(repo: Path, config: dict[str, Any]) -> Path:
    value = (config.get("workspace") or {}).get("root") or config.get("worktreeRoot") or DEFAULT_ROOT
    path = Path(value)
    return (path if path.is_absolute() else repo / path).resolve()


def create(repo: Path, slug: str, *, clone: bool = False, base: str = "HEAD", branch: str | None = None,
           approve_large: bool = False) -> dict[str, Any]:
    repo = toplevel(repo)
    if not SLUG.fullmatch(slug):
        raise HelperError("slug must be lowercase words separated by hyphens")
    config = load_config(repo)
    size = estimate(repo, config)
    if size["approvalRequired"] and not approve_large:
        raise HelperError(f"workspace estimate {size['estimatedGiB']} GiB exceeds {size['limitGiB']} GiB; "
                          "ask the user, then pass --approve-large")
    path = _root(repo, config) / slug
    if path.exists():
        raise HelperError(f"workspace path already exists: {path}")
    base_sha = git(repo, "rev-parse", "--verify", base + "^{commit}").stdout.strip()
    existing = branch is not None
    branch = branch or f"task/{slug}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if clone:
        git(repo, "clone", "--quiet", "--no-checkout", str(repo), str(path))
        git(path, "checkout", "--quiet", *( [branch] if existing else ["-b", branch, base_sha] ))
    else:
        git(repo, "worktree", "add", "--quiet", *( [str(path), branch] if existing else ["-b", branch, str(path), base_sha] ))
    entry = {"slug": slug, "path": str(path), "kind": "clone" if clone else "worktree", "branch": branch,
             "createdBranch": not existing, "base": base_sha,
             "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with _registry(repo) as registry:
        registry["entries"] = [item for item in registry["entries"] if item.get("slug") != slug] + [entry]
    return {**entry, "estimate": size}


def _status(entry: dict[str, Any]) -> dict[str, Any]:
    path = Path(entry["path"])
    if not path.is_dir():
        return {**entry, "exists": False}
    process = git(path, "status", "--porcelain", "-z", "--untracked-files=all", check=False)
    head = git(path, "rev-parse", "HEAD", check=False).stdout.strip()
    return {**entry, "exists": True, "head": head, "dirty": bool(process.stdout.strip("\0")) or process.returncode != 0}


def list_workspaces(repo: Path) -> list[dict[str, Any]]:
    repo = toplevel(repo)
    with _registry(repo) as registry:
        entries = list(registry["entries"])
    return [_status(entry) for entry in entries]


def _force_remove(function, path, _):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove(repo: Path, slug: str, *, keep_branch: bool = False) -> dict[str, Any]:
    repo = toplevel(repo)
    with _registry(repo) as registry:
        matches = [item for item in registry["entries"] if item.get("slug") == slug]
    if not matches:
        raise HelperError(f"no Lemmings workspace named {slug}")
    entry = _status(matches[0])
    path = Path(entry["path"])
    removed_branch = False
    if entry["exists"]:
        if entry["dirty"]:
            raise HelperError(f"workspace has uncommitted or untracked changes: {path}")
        if entry["kind"] == "clone":
            if toplevel(path) != path.resolve():
                raise HelperError(f"clone path is not a repository root: {path}")
            for line in git(path, "for-each-ref", "--format=%(objectname)", "refs/heads").stdout.split():
                if git(repo, "cat-file", "-e", line + "^{commit}", check=False).returncode:
                    raise HelperError(f"clone has commits missing from the primary repository; fetch them first: {line}")
            if git(path, "stash", "list").stdout.strip():
                raise HelperError("clone has stashed changes")
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_force_remove)
            else:
                shutil.rmtree(path, onerror=_force_remove)
        else:
            git(repo, "worktree", "remove", str(path))
    if entry["kind"] == "worktree" and entry.get("createdBranch") and not keep_branch:
        branch = git(repo, "branch", "-d", entry["branch"], check=False)
        removed_branch = branch.returncode == 0
        if not removed_branch:
            with _registry(repo) as registry:
                registry["entries"] = [item for item in registry["entries"] if item.get("slug") != slug]
            return {"slug": slug, "removed": True, "branchRetained": entry["branch"],
                    "reason": "branch is not merged into the current HEAD; kept for inspection"}
    with _registry(repo) as registry:
        registry["entries"] = [item for item in registry["entries"] if item.get("slug") != slug]
    return {"slug": slug, "removed": True, "branchDeleted": removed_branch}
