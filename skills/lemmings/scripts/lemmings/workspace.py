"""Workspace sizing plus explicit, manager-directed registry transactions."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .contracts import SCHEMA_VERSION, git, git_common_dir, path_matches, read_object, resolve_path, schema_error, write_object

GIB = 1024 ** 3
WORKSPACE_BACKENDS = {"auto", "current", "code-worktree", "package-worktree", "unity-clone"}
REGISTRY_STATES = {"active", "idle", "quarantined", "retiring"}
LIFETIMES = {"task", "phase", "project", "external"}
MANAGERS = {"lemmings", "user", "external"}
DEFAULT_POOL_POLICY = {"enabled": True, "maxIdle": 2, "maxIdleGiB": 10, "eviction": "lru"}


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
        if value.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError(schema_error("workspace environment", value))
        root = resolve_path(repo, value.get("toolRoot"))
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
    package_path: str | None = None,
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
        package = resolve_path(repo, package_path) if package_path else None
        if not package or not package.is_dir():
            raise ValueError("package-worktree requires an existing --package path")
        estimate = _size(package)
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
        "registry": inspect_registry(repo),
    }


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def registry_path(repo: Path) -> Path:
    return git_common_dir(repo) / "lemmings" / "workspaces-v4.json"


def common_dir_identity(repo: Path) -> str:
    value = str(git_common_dir(repo)).replace("\\", "/").casefold()
    return hashlib.sha256(value.encode()).hexdigest()


def _remove_readonly(function: Any, path: str, _: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _empty_registry() -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "revision": 0, "entries": []}


def load_registry(repo: Path) -> dict[str, Any]:
    path = registry_path(repo)
    if not path.is_file():
        return _empty_registry()
    value = read_object(path)
    if value.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(schema_error("workspace registry", value))
    if not isinstance(value.get("revision"), int) or not isinstance(value.get("entries"), list):
        raise ValueError(f"invalid workspace registry: {path}")
    return value


@contextmanager
def _registry_lock(repo: Path):
    path = registry_path(repo).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise ValueError(f"workspace registry is locked: {path}") from error
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode())
        os.close(descriptor)
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _save_registry(repo: Path, registry: dict[str, Any], expected_revision: int) -> None:
    current = load_registry(repo)
    if current["revision"] != expected_revision:
        raise ValueError(f"stale workspace registry revision: expected {expected_revision}, actual {current['revision']}")
    registry["schemaVersion"] = SCHEMA_VERSION
    registry["revision"] = expected_revision + 1
    write_object(registry_path(repo), registry)


def _entry(registry: Mapping[str, Any], workspace_id: str) -> dict[str, Any]:
    matches = [item for item in registry.get("entries", []) if isinstance(item, dict) and item.get("workspaceId") == workspace_id]
    if len(matches) != 1:
        raise ValueError(f"workspace registry entry is not unique: {workspace_id}")
    return matches[0]


def _registered_worktrees(repo: Path) -> list[Path]:
    process = git(repo, "worktree", "list", "--porcelain")
    if process.returncode:
        return []
    return [Path(line[9:]).resolve() for line in process.stdout.splitlines() if line.startswith("worktree ")]


def inspect_registered_workspace(repo: Path, path: Path, allowed_caches: list[str] | None = None) -> dict[str, Any]:
    target = path.resolve()
    exists = target.is_dir()
    worktrees = _registered_worktrees(repo)
    registered = target in worktrees
    primary = bool(worktrees and target == worktrees[0])
    status = git(target, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none") if exists else None
    branch = git(target, "branch", "--show-current") if exists else None
    head = git(target, "rev-parse", "HEAD") if exists else None
    top_level = git(target, "rev-parse", "--show-toplevel") if exists else None
    standalone_root = bool(top_level and not top_level.returncode and Path(top_level.stdout.strip()).resolve() == target)
    ignored_process = git(target, "ls-files", "--others", "--ignored", "--exclude-standard", "-z") if exists else None
    ignored = [item for item in (ignored_process.stdout.split("\0") if ignored_process and not ignored_process.returncode else []) if item]
    allow = allowed_caches or []
    unexpected_ignored = [item for item in ignored if not any(path_matches(item, rule) for rule in allow)]
    operations: list[str] = []
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply"):
        probe = git(target, "rev-parse", "--git-path", name) if exists else None
        if probe and not probe.returncode and Path(probe.stdout.strip()).exists():
            operations.append(name)
    return {
        "path": str(target),
        "exists": exists,
        "registered": registered,
        "standaloneGitRoot": standalone_root,
        "primary": primary,
        "clean": bool(status and not status.returncode and not status.stdout.strip()),
        "branch": branch.stdout.strip() if branch and not branch.returncode else None,
        "head": head.stdout.strip() if head and not head.returncode else None,
        "unfinishedOperations": operations,
        "unexpectedIgnored": unexpected_ignored,
    }


def inspect_registry(repo: Path) -> dict[str, Any]:
    registry = load_registry(repo)
    entries: list[dict[str, Any]] = []
    for item in registry["entries"]:
        if not isinstance(item, Mapping):
            continue
        current = dict(item)
        path = Path(str(item.get("path") or ""))
        if path:
            current["inspection"] = inspect_registered_workspace(repo, path, list(item.get("allowedCaches") or []))
        entries.append(current)
    return {"schemaVersion": SCHEMA_VERSION, "revision": registry["revision"], "entries": entries, "lockPresent": registry_path(repo).with_suffix(".lock").exists()}


def register_workspace(
    repo: Path,
    *,
    workspace_id: str,
    path: Path,
    backend: str,
    managed_by: str,
    lifetime: str,
    expected_revision: int,
    task_id: str | None = None,
    phase_id: str | None = None,
    estimated_gib: float = 0,
    approval: str = "not-required",
    kind: str = "writer",
    allowed_caches: list[str] | None = None,
) -> dict[str, Any]:
    if backend not in WORKSPACE_BACKENDS - {"auto", "current"} or managed_by not in MANAGERS or lifetime not in LIFETIMES:
        raise ValueError("invalid workspace backend, manager, or lifetime")
    target = path.resolve()
    info = inspect_registered_workspace(repo, target, allowed_caches)
    if not info["exists"] or (backend == "unity-clone" and not info["standaloneGitRoot"]) or (backend != "unity-clone" and not info["registered"]):
        raise ValueError("workspace must be an existing exact Git worktree or standalone Unity clone")
    with _registry_lock(repo):
        registry = load_registry(repo)
        if registry["revision"] != expected_revision:
            raise ValueError(f"stale workspace registry revision: expected {expected_revision}, actual {registry['revision']}")
        if any(isinstance(item, Mapping) and (item.get("workspaceId") == workspace_id or Path(str(item.get("path") or "")).resolve() == target) for item in registry["entries"]):
            raise ValueError("workspace id and path must be unique")
        now = utc_timestamp()
        entry = {
            "workspaceId": workspace_id,
            "path": str(target),
            "commonDirIdentity": common_dir_identity(repo),
            "backend": backend,
            "managedBy": managed_by,
            "lifetime": lifetime,
            "kind": kind,
            "state": "active" if task_id else "idle",
            "taskId": task_id,
            "phaseId": phase_id,
            "branch": info["branch"],
            "headSha": info["head"],
            "baseSha": None,
            "estimatedGiB": float(estimated_gib),
            "approval": approval,
            "everActive": False,
            "lastTaskState": None,
            "activeInvocationId": None,
            "leases": [],
            "processes": [],
            "allowedCaches": list(allowed_caches or []),
            "createdAt": now,
            "lastUsedAt": now,
            "quarantineReason": None,
        }
        registry["entries"].append(entry)
        _save_registry(repo, registry, expected_revision)
        return {"ok": True, "revision": expected_revision + 1, "entry": entry}


def _reuse_reasons(repo: Path, entry: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    info = inspect_registered_workspace(repo, Path(str(entry.get("path") or "")), list(entry.get("allowedCaches") or []))
    reasons: list[str] = []
    if entry.get("commonDirIdentity") != common_dir_identity(repo): reasons.append("git-common-dir-mismatch")
    if entry.get("managedBy") != "lemmings": reasons.append("not-lemmings-managed")
    if entry.get("state") != "idle": reasons.append("not-idle")
    if entry.get("everActive") and entry.get("lastTaskState") != "Integrated": reasons.append("previous-task-not-integrated")
    exact_workspace = info["registered"] or (entry.get("backend") == "unity-clone" and info["standaloneGitRoot"])
    if not info["exists"] or not exact_workspace or info["primary"]: reasons.append("not-an-exact-reusable-workspace")
    if not info["clean"]: reasons.append("dirty-or-untracked")
    if info["unfinishedOperations"]: reasons.append("unfinished-git-operation")
    if info["unexpectedIgnored"]: reasons.append("unexpected-ignored-files")
    if entry.get("activeInvocationId") or entry.get("leases") or entry.get("processes"): reasons.append("active-owner-or-resource")
    return reasons, info


def claim_workspace(
    repo: Path,
    *,
    workspace_id: str,
    task_id: str,
    base_sha: str,
    integration_head: str,
    branch: str,
    expected_revision: int,
    phase_id: str | None = None,
) -> dict[str, Any]:
    if base_sha != integration_head:
        raise ValueError("workspace base must equal the current integration head")
    with _registry_lock(repo):
        registry = load_registry(repo)
        if registry["revision"] != expected_revision:
            raise ValueError(f"stale workspace registry revision: expected {expected_revision}, actual {registry['revision']}")
        entry = _entry(registry, workspace_id)
        if entry.get("state") == "active" and entry.get("taskId") == task_id:
            info = inspect_registered_workspace(repo, Path(str(entry.get("path") or "")), list(entry.get("allowedCaches") or []))
            if not info["clean"] or info["head"] != base_sha or info["branch"] != branch:
                entry["state"] = "quarantined"
                entry["quarantineReason"] = "reserved-workspace-state-mismatch"
                _save_registry(repo, registry, expected_revision)
                raise ValueError("reserved workspace does not match the accepted base and branch")
            if entry.get("baseSha") == base_sha and entry.get("everActive"):
                return {"ok": True, "idempotent": True, "revision": expected_revision, "entry": entry}
            entry.update({"baseSha": base_sha, "headSha": base_sha, "everActive": True, "lastUsedAt": utc_timestamp()})
            _save_registry(repo, registry, expected_revision)
            return {"ok": True, "revision": expected_revision + 1, "entry": entry, "inspection": info}
        reasons, info = _reuse_reasons(repo, entry)
        if reasons:
            entry["state"] = "quarantined"
            entry["quarantineReason"] = ",".join(reasons)
            _save_registry(repo, registry, expected_revision)
            raise ValueError("workspace is not reusable: " + ", ".join(reasons))
        if git(repo, "rev-parse", "--verify", f"{base_sha}^{{commit}}").returncode:
            raise ValueError(f"base commit does not resolve: {base_sha}")
        target = Path(str(entry["path"]))
        switched = git(target, "switch", "-c", branch, base_sha)
        if switched.returncode:
            entry["state"] = "quarantined"
            entry["quarantineReason"] = "branch-switch-failed"
            _save_registry(repo, registry, expected_revision)
            raise ValueError(switched.stderr.strip() or "workspace branch switch failed")
        entry.update({
            "state": "active", "taskId": task_id, "phaseId": phase_id, "branch": branch,
            "headSha": base_sha, "baseSha": base_sha, "everActive": True,
            "lastUsedAt": utc_timestamp(), "quarantineReason": None,
        })
        _save_registry(repo, registry, expected_revision)
        return {"ok": True, "revision": expected_revision + 1, "entry": entry, "inspection": info}


def _remove_entry(repo: Path, registry: dict[str, Any], entry: dict[str, Any]) -> tuple[bool, str | None]:
    reasons, _ = _reuse_reasons(repo, {**entry, "state": "idle", "lastTaskState": "Integrated"})
    reasons = [reason for reason in reasons if reason not in {"previous-task-not-integrated", "not-idle"}]
    if entry.get("managedBy") != "lemmings" or entry.get("lifetime") == "project" or entry.get("kind") == "validation":
        reasons.append("protected-workspace")
    if reasons:
        entry["state"] = "quarantined"
        entry["quarantineReason"] = ",".join(sorted(set(reasons)))
        return False, entry["quarantineReason"]
    target = Path(str(entry["path"])).resolve()
    if entry.get("backend") == "unity-clone" and target not in _registered_worktrees(repo):
        info = inspect_registered_workspace(repo, target, list(entry.get("allowedCaches") or []))
        if not info["standaloneGitRoot"] or target == repo.resolve():
            entry["state"] = "quarantined"
            entry["quarantineReason"] = "standalone-clone-identity-mismatch"
            return False, entry["quarantineReason"]
        try:
            shutil.rmtree(target, onerror=_remove_readonly)
        except OSError as error:
            entry["state"] = "quarantined"
            entry["quarantineReason"] = str(error) or "clone-remove-failed"
            return False, entry["quarantineReason"]
    else:
        removed = git(repo, "worktree", "remove", str(target))
        if removed.returncode:
            entry["state"] = "quarantined"
            entry["quarantineReason"] = removed.stderr.strip() or "worktree-remove-failed"
            return False, entry["quarantineReason"]
    registry["entries"].remove(entry)
    return True, None


def _pool_policy(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    configured = (profile or {}).get("workspacePool")
    return {**DEFAULT_POOL_POLICY, **(dict(configured) if isinstance(configured, Mapping) else {})}


def _evict_pool(repo: Path, registry: dict[str, Any], profile: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    policy = _pool_policy(profile)
    idle = [item for item in registry["entries"] if isinstance(item, dict) and item.get("state") == "idle" and item.get("managedBy") == "lemmings" and item.get("lifetime") != "project" and item.get("kind") != "validation"]
    idle.sort(key=lambda item: str(item.get("lastUsedAt") or ""))
    removed: list[dict[str, Any]] = []
    def disk_usage() -> float:
        return sum(max(float(item.get("estimatedGiB") or 0), float(item.get("observedGiB") or 0)) for item in idle if not item.get("retentionApproved"))
    while len(idle) > int(policy["maxIdle"]) or disk_usage() > float(policy["maxIdleGiB"]):
        candidate = next((item for item in idle if not item.get("retentionApproved")), None)
        if candidate is None:
            break
        idle.remove(candidate)
        ok, reason = _remove_entry(repo, registry, candidate)
        removed.append({"workspaceId": candidate.get("workspaceId"), "removed": ok, "reason": reason})
        if not ok:
            continue
    return removed


def release_workspace(
    repo: Path,
    *,
    workspace_id: str,
    expected_revision: int,
    task_state: str,
    integration_evidence: bool,
    action: str = "pool",
    retention_approved: bool = False,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if action not in {"pool", "remove", "retain"}:
        raise ValueError("release action must be pool, remove, or retain")
    with _registry_lock(repo):
        registry = load_registry(repo)
        if registry["revision"] != expected_revision:
            raise ValueError(f"stale workspace registry revision: expected {expected_revision}, actual {registry['revision']}")
        entry = _entry(registry, workspace_id)
        entry["lastTaskState"] = task_state
        entry["retentionApproved"] = bool(retention_approved)
        never_active = not entry.get("everActive")
        if never_active:
            action = "remove"
        elif task_state != "Integrated" or not integration_evidence:
            action = "retain"
        if entry.get("everActive") and task_state in {"Blocked", "Cancelled", "Replan Required"}:
            action = "retain"
        entry["observedGiB"] = round(_size(Path(str(entry.get("path")))) / GIB, 3)
        if max(float(entry.get("estimatedGiB") or 0), float(entry.get("observedGiB") or 0)) > 10 and not retention_approved and action == "pool":
            action = "remove"
        protected = entry.get("managedBy") != "lemmings" or entry.get("lifetime") == "project" or entry.get("kind") == "validation"
        if protected:
            action = "retain"
        removed = False
        reason: str | None = None
        evicted: list[dict[str, Any]] = []
        if action == "retain":
            if protected:
                entry.update({"state": "idle", "taskId": None, "phaseId": None, "lastUsedAt": utc_timestamp(), "quarantineReason": None})
                reason = "protected-workspace"
            elif task_state in {"Active", "Repair", "Candidate", "Accepted"}:
                entry.update({"state": "active", "lastUsedAt": utc_timestamp(), "quarantineReason": None})
                reason = "same-task-workspace-retained"
            else:
                entry["state"] = "quarantined"
                entry["quarantineReason"] = "retained-by-lifecycle-policy"
        elif action == "remove":
            entry["state"] = "idle"
            removed, reason = _remove_entry(repo, registry, entry)
        else:
            if not _pool_policy(profile).get("enabled"):
                entry["state"] = "idle"
                removed, reason = _remove_entry(repo, registry, entry)
                action = "remove"
            else:
                reasons, info = _reuse_reasons(repo, {**entry, "state": "idle", "lastTaskState": "Integrated"})
                if reasons:
                    entry["state"] = "quarantined"
                    entry["quarantineReason"] = ",".join(reasons)
                    action, reason = "retain", entry["quarantineReason"]
                else:
                    entry.update({"state": "idle", "taskId": None, "phaseId": None, "headSha": info["head"], "lastUsedAt": utc_timestamp(), "quarantineReason": None})
                    evicted = _evict_pool(repo, registry, profile)
        _save_registry(repo, registry, expected_revision)
        if removed:
            disposition = "removed"
        elif entry in registry["entries"] and entry.get("state") == "quarantined":
            disposition = "retained"
        elif action == "pool":
            disposition = "released-to-pool"
        elif action == "retain":
            disposition = "retained"
        else:
            disposition = action
        return {"ok": True, "revision": expected_revision + 1, "action": disposition, "reason": reason, "evicted": evicted}


def remove_workspace(repo: Path, *, workspace_id: str, expected_revision: int) -> dict[str, Any]:
    with _registry_lock(repo):
        registry = load_registry(repo)
        if registry["revision"] != expected_revision:
            raise ValueError(f"stale workspace registry revision: expected {expected_revision}, actual {registry['revision']}")
        entry = _entry(registry, workspace_id)
        removed, reason = _remove_entry(repo, registry, entry)
        _save_registry(repo, registry, expected_revision)
        return {"ok": removed, "revision": expected_revision + 1, "action": "removed" if removed else "quarantined", "reason": reason}
