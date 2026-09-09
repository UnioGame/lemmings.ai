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
_COMPLETED_PROCESS_STATUSES = {"completed", "complete", "exited", "terminated", "cancelled", "failed"}


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_symlink_component(path: Path) -> bool:
    current = Path(os.path.abspath(str(path)))
    while True:
        if current.exists() and current.is_symlink():
            return True
        if current.parent == current:
            return False
        current = current.parent


def _canonical_path(repo: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    target = raw if raw.is_absolute() else repo / raw
    if _has_symlink_component(target):
        raise ValueError(f"{label} cannot use a symlink alias: {value}")
    return Path(os.path.abspath(str(target))).resolve()


def _workspace_root(repo: Path, workspace: Mapping[str, Any] | None = None) -> Path:
    values = workspace or {}
    configured = next(
        (values.get(key) for key in ("workspaceRoot", "destinationRoot", "root") if values.get(key)),
        None,
    )
    return _canonical_path(repo, configured, label="workspace root") if configured else repo.resolve().parent


def _validate_destination(
    repo: Path,
    destination: str | Path,
    workspace: Mapping[str, Any] | None = None,
    *,
    allow_repo: bool = False,
) -> tuple[Path, Path]:
    target = _canonical_path(repo, destination, label="workspace destination")
    root = _workspace_root(repo, workspace)
    if not _is_relative_to(target, root):
        raise ValueError("workspace destination is outside the intended workspace root")
    if not allow_repo and _same_path(target, repo):
        raise ValueError("managed workspace cannot use the primary checkout")
    return target, root


def _git_root(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    process = git(path, "rev-parse", "--show-toplevel")
    if process.returncode or not process.stdout.strip():
        return None
    return Path(process.stdout.strip()).resolve()


def _git_path(target: Path, name: str) -> Path | None:
    process = git(target, "rev-parse", "--git-path", name)
    if process.returncode or not process.stdout.strip():
        return None
    value = Path(process.stdout.strip())
    return (target / value).resolve() if not value.is_absolute() else value.resolve()


def _process_owner_reasons(entry: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if entry.get("activeInvocationId"):
        reasons.append("active-invocation")
    if entry.get("leases"):
        reasons.append("active-lease")
    records = entry.get("processes")
    if records:
        if not isinstance(records, list):
            return [*reasons, "unverifiable-process-record"]
        for record in records:
            if not isinstance(record, Mapping):
                reasons.append("unverifiable-process-record")
                continue
            status = str(record.get("status") or "").lower()
            if status not in _COMPLETED_PROCESS_STATUSES:
                reasons.append("live-or-unknown-process")
                continue
            if not isinstance(record.get("pid"), int) or isinstance(record.get("pid"), bool) or not record.get("invocationId"):
                reasons.append("unverifiable-process-record")
    return sorted(set(reasons))


def _task_path(repo: Path, value: str | Path) -> Path:
    target = _canonical_path(repo, value, label="canonical Task")
    if not _is_relative_to(target, repo.resolve()) or not target.is_file():
        raise ValueError("canonical Task must be an existing file inside the repository")
    return target


def _read_task(repo: Path, value: str | Path) -> tuple[Path, dict[str, Any]]:
    target = _task_path(repo, value)
    try:
        task = read_object(target)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"canonical Task cannot be read: {target}") from error
    if task.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(schema_error("canonical Task", task))
    revision = task.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("canonical Task requires a non-negative integer revision")
    if not task.get("taskId"):
        raise ValueError("canonical Task requires taskId")
    workspace = task.get("workspace")
    if not isinstance(workspace, Mapping):
        raise ValueError("canonical Task requires a workspace object")
    return target, task


def _task_workspace(task: Mapping[str, Any]) -> Mapping[str, Any]:
    workspace = task.get("workspace")
    if not isinstance(workspace, Mapping):
        raise ValueError("canonical Task requires a workspace object")
    provision = workspace.get("provision")
    if isinstance(provision, Mapping):
        return {**dict(provision), **dict(workspace)}
    return workspace


def _task_candidate_head(task: Mapping[str, Any]) -> str | None:
    commits = task.get("commits")
    if not isinstance(commits, Mapping):
        return None
    fixes = commits.get("fix")
    if isinstance(fixes, list) and fixes:
        return str(fixes[-1])
    return str(commits.get("candidate")) if commits.get("candidate") else None


def _commit_exists(repo: Path, value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return git(repo, "rev-parse", "--verify", f"{value}^{{commit}}").returncode == 0


def _verified_mapping(close: Mapping[str, Any], candidate: str, merge: str) -> bool:
    for key in ("candidateToMerge", "candidateToMergeMapping", "mergeMapping"):
        value = close.get(key)
        values = value if isinstance(value, list) else [value]
        for mapping in values:
            if not isinstance(mapping, Mapping):
                continue
            candidate_value = mapping.get("candidateHead") or mapping.get("candidate") or mapping.get("headSha")
            merge_value = mapping.get("mergeCommit") or mapping.get("merge") or mapping.get("target")
            if (
                str(candidate_value or "") == candidate
                and str(merge_value or "") == merge
                and mapping.get("verified") is True
            ):
                return True
    return False


def _integration_evidence_digest(evidence: list[Mapping[str, Any]]) -> str:
    snapshot: list[dict[str, Any]] = []
    for item in evidence:
        exit_code = item.get("exitCode")
        if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
            raise ValueError("canonical Task integration evidence exitCode must be an integer when supplied")
        snapshot.append(
            {
                "command": str(item["command"]).strip(),
                "headSha": str(item["headSha"]),
                "passed": item["passed"],
                "exitCode": exit_code,
            }
        )
    payload = json.dumps(
        sorted(snapshot, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _active_task_entry(entry: Mapping[str, Any]) -> bool:
    task_id = entry.get("taskId")
    return entry.get("state") == "active" and isinstance(task_id, str) and bool(task_id)


def _stored_pool_release_evidence(entry: Mapping[str, Any]) -> dict[str, Any]:
    task_id = entry.get("lastTaskId")
    task_path = entry.get("lastTaskPath")
    task_revision = entry.get("lastTaskRevision")
    evidence = entry.get("releaseEvidence")
    if (
        not isinstance(task_id, str)
        or not task_id
        or not isinstance(task_path, str)
        or not task_path
        or not isinstance(task_revision, int)
        or isinstance(task_revision, bool)
        or task_revision < 0
        or not isinstance(evidence, Mapping)
    ):
        raise ValueError("pooled workspace lacks complete prior canonical Task release evidence")
    for key, expected in {
        "taskId": task_id,
        "taskPath": task_path,
        "taskRevision": task_revision,
        "state": "Integrated",
    }.items():
        if evidence.get(key) != expected:
            raise ValueError("pooled workspace release evidence does not match its prior canonical Task")
    for key in ("candidateHead", "mergeCommit"):
        value = evidence.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError("pooled workspace release evidence is incomplete")
    commands = evidence.get("validationCommands")
    if (
        not isinstance(commands, list)
        or not commands
        or any(not isinstance(command, str) or not command.strip() for command in commands)
        or len({command.strip() for command in commands}) != len(commands)
    ):
        raise ValueError("pooled workspace release evidence is missing declared validation commands")
    digest = evidence.get("integrationEvidenceDigest")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("pooled workspace release evidence is missing canonical integration evidence")
    return dict(evidence)


def _verify_cleanup_task(
    repo: Path,
    entry: Mapping[str, Any],
    task_path: str | Path,
    task_revision: int | None,
) -> dict[str, Any]:
    path, task = _read_task(repo, task_path)
    workspace = _task_workspace(task)
    workspace_id = workspace.get("workspaceId")
    if workspace_id != entry.get("workspaceId"):
        raise ValueError("canonical Task workspace id does not match the registry entry")
    active_task_id = entry.get("taskId") if _active_task_entry(entry) else None
    stored_evidence = None if active_task_id else _stored_pool_release_evidence(entry)
    prior_task_id = stored_evidence.get("taskId") if stored_evidence else None
    expected_task_id = active_task_id or prior_task_id
    if expected_task_id and task.get("taskId") != expected_task_id:
        raise ValueError("canonical Task id does not match the registry entry")
    if not active_task_id and str(path) != stored_evidence["taskPath"]:
        raise ValueError("canonical Task path does not match the pooled workspace release")
    stored_revision = stored_evidence.get("taskRevision") if stored_evidence else None
    if stored_revision is not None and task.get("revision") != stored_revision:
        raise ValueError("canonical Task revision does not match the pooled workspace release")
    if task_revision is not None and task.get("revision") != task_revision:
        raise ValueError("canonical Task revision does not match task_revision")
    if task.get("state") != "Integrated":
        raise ValueError("workspace cleanup requires an Integrated canonical Task")
    if workspace.get("backend") and workspace.get("backend") != entry.get("backend"):
        raise ValueError("canonical Task backend does not match the registry entry")
    declared_root = workspace.get("repoRoot") or workspace.get("repositoryRoot") or task.get("repoRoot")
    if entry.get("backend") != "current" and not declared_root:
        raise ValueError("canonical Task repository root is required for isolated cleanup")
    if declared_root and not _same_path(_canonical_path(repo, declared_root, label="Task repository root"), repo):
        raise ValueError("canonical Task repository root does not match the repository")
    declared_destination = workspace.get("destination") or workspace.get("destinationPath")
    if entry.get("backend") != "current" and not declared_destination:
        raise ValueError("canonical Task destination is required for isolated cleanup")
    if declared_destination and not _same_path(
        _canonical_path(repo, declared_destination, label="Task destination"),
        Path(str(entry.get("destination") or entry.get("path"))),
    ):
        raise ValueError("canonical Task destination does not match the registry entry")
    if entry.get("backend") != "current" and not workspace.get("branch"):
        raise ValueError("canonical Task branch is required for isolated cleanup")
    if workspace.get("branch") and workspace.get("branch") != entry.get("branch"):
        raise ValueError("canonical Task branch does not match the registry entry")
    if entry.get("backend") != "current" and not workspace.get("baseSha"):
        raise ValueError("canonical Task base SHA is required for isolated cleanup")
    if workspace.get("baseSha") and workspace.get("baseSha") != entry.get("baseSha"):
        raise ValueError("canonical Task base SHA does not match the registry entry")
    close = task.get("close")
    if not isinstance(close, Mapping):
        raise ValueError("canonical Task close evidence is required")
    merge_commit = close.get("mergeCommit")
    if not isinstance(merge_commit, str) or not _commit_exists(repo, merge_commit):
        raise ValueError("canonical Task mergeCommit does not resolve to a commit")
    evidence = close.get("integrationEvidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("canonical Task requires passing integration evidence")
    for item in evidence:
        if not isinstance(item, Mapping) or item.get("headSha") != merge_commit or not item.get("command") or item.get("passed") is not True:
            raise ValueError("canonical Task integration evidence must pass at close.mergeCommit")
    validation = task.get("validation")
    declared_commands = validation.get("commands") if isinstance(validation, Mapping) else None
    if (
        not isinstance(declared_commands, list)
        or not declared_commands
        or any(not isinstance(command, str) or not command.strip() for command in declared_commands)
        or len({command.strip() for command in declared_commands}) != len(declared_commands)
    ):
        raise ValueError("canonical Task validation.commands must declare every integration check")
    declared = {command.strip() for command in declared_commands}
    observed = {str(item["command"]).strip() for item in evidence if isinstance(item, Mapping)}
    if observed != declared:
        raise ValueError("canonical Task integration evidence must cover exactly validation.commands")
    candidate = _task_candidate_head(task)
    if not candidate or not _commit_exists(repo, candidate):
        raise ValueError("canonical Task candidate commit does not resolve")
    if git(repo, "merge-base", "--is-ancestor", candidate, merge_commit).returncode and not _verified_mapping(close, candidate, merge_commit):
        raise ValueError("candidate is not an ancestor of mergeCommit and has no verified mapping")
    result = {
        "taskPath": str(path),
        "taskId": str(task["taskId"]),
        "taskRevision": int(task["revision"]),
        "state": "Integrated",
        "candidateHead": candidate,
        "mergeCommit": merge_commit,
        "validationCommands": [command.strip() for command in declared_commands],
        "integrationEvidenceDigest": _integration_evidence_digest(evidence),
    }
    if stored_evidence:
        for key in (
            "taskId",
            "taskPath",
            "taskRevision",
            "candidateHead",
            "mergeCommit",
            "validationCommands",
            "integrationEvidenceDigest",
        ):
            if stored_evidence.get(key) != result[key]:
                raise ValueError("canonical Task release evidence does not match the pooled workspace")
    return result


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
    tracked = _size(repo) if selected in {"code-worktree", "unity-clone"} else 0
    submodules = 0
    cache = _size(game / "Library") if game and selected == "unity-clone" else 0
    if selected == "current":
        estimate = 0
    elif selected == "package-worktree":
        package = resolve_path(repo, package_path) if package_path else None
        if not package or not package.is_dir():
            raise ValueError("package-worktree requires an existing --package path")
        if _git_root(package) != package.resolve():
            raise ValueError("package-worktree requires the package's own Git root")
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


def _registered_worktrees(repo: Path, source_repo: Path | None = None) -> list[Path]:
    process = git(source_repo or repo, "worktree", "list", "--porcelain")
    if process.returncode:
        return []
    return [Path(line[9:]).resolve() for line in process.stdout.splitlines() if line.startswith("worktree ")]


def inspect_registered_workspace(
    repo: Path,
    path: Path,
    allowed_caches: list[str] | None = None,
    *,
    source_repo: Path | None = None,
) -> dict[str, Any]:
    target = path.resolve()
    exists = target.is_dir()
    worktrees = _registered_worktrees(repo, source_repo)
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
        probe = _git_path(target, name) if exists else None
        if probe and probe.exists():
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
        "gitRoot": str(Path(top_level.stdout.strip()).resolve()) if top_level and not top_level.returncode else None,
    }


def inspect_registry(repo: Path) -> dict[str, Any]:
    registry = load_registry(repo)
    entries: list[dict[str, Any]] = []
    for item in registry["entries"]:
        if not isinstance(item, Mapping):
            continue
        current = dict(item)
        raw_path = item.get("path")
        if raw_path:
            source_repo = Path(str(item.get("sourceRepoRoot") or repo)).resolve()
            current["inspection"] = inspect_registered_workspace(
                repo,
                Path(str(raw_path)),
                list(item.get("allowedCaches") or []),
                source_repo=source_repo,
            )
        entries.append(current)
    return {"schemaVersion": SCHEMA_VERSION, "revision": registry["revision"], "entries": entries, "lockPresent": registry_path(repo).with_suffix(".lock").exists()}


def _entry_source_repo(repo: Path, entry: Mapping[str, Any]) -> Path:
    return Path(str(entry.get("sourceRepoRoot") or repo)).resolve()


def _entry_identity_reasons(repo: Path, entry: Mapping[str, Any]) -> list[str]:
    actual_repo = repo.resolve()
    reasons: list[str] = []
    backend = entry.get("backend")
    if backend not in {"code-worktree", "package-worktree", "unity-clone"}:
        reasons.append("invalid-managed-backend")
    declared_root = entry.get("repoRoot")
    try:
        root_matches = bool(
            declared_root
            and _same_path(
                _canonical_path(repo, str(declared_root), label="registered repository root"),
                actual_repo,
            )
        )
    except ValueError:
        root_matches = False
    if not root_matches:
        reasons.append("repository-root-mismatch")
    declared_source = entry.get("sourceRepoRoot")
    try:
        source_matches = bool(
            declared_source
            and _same_path(
                _canonical_path(repo, str(declared_source), label="registered source repository"),
                actual_repo,
            )
        )
    except ValueError:
        source_matches = False
    if not source_matches:
        reasons.append("source-repository-root-mismatch")
    try:
        actual_common_identity = common_dir_identity(actual_repo)
    except ValueError:
        actual_common_identity = None
    if entry.get("commonDirIdentity") != actual_common_identity:
        reasons.append("git-common-dir-mismatch")
    if backend == "package-worktree" and _git_root(actual_repo) != actual_repo:
        reasons.append("package-source-not-git-root")
    return reasons


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
    source_repo_root: str | Path | None = None,
    base_sha: str | None = None,
    destination: str | Path | None = None,
    branch: str | None = None,
    task_path: str | Path | None = None,
    task_revision: int | None = None,
) -> dict[str, Any]:
    if backend not in WORKSPACE_BACKENDS - {"auto", "current"} or managed_by not in MANAGERS or lifetime not in LIFETIMES:
        raise ValueError("invalid workspace backend, manager, or lifetime")
    source_repo = _canonical_path(repo, source_repo_root, label="workspace source repository") if source_repo_root else repo.resolve()
    if backend == "package-worktree" and (not _same_path(source_repo, repo) or _git_root(source_repo) != source_repo):
        raise ValueError("package-worktree requires --repo to be the package's own Git root")
    if backend != "package-worktree":
        source_repo = repo.resolve()
    target, intended_root = _validate_destination(repo, destination or path, None, allow_repo=managed_by != "lemmings")
    if destination and not _same_path(target, Path(path)):
        raise ValueError("workspace path and destination must identify the same path")
    if managed_by == "lemmings" and _same_path(target, repo):
        raise ValueError("primary checkout cannot be registered as a managed workspace")
    info = inspect_registered_workspace(repo, target, allowed_caches, source_repo=source_repo)
    if not info["exists"] or (backend == "unity-clone" and not info["standaloneGitRoot"]) or (backend != "unity-clone" and not info["registered"]):
        raise ValueError("workspace must be an existing exact Git worktree or standalone Unity clone")
    if not info["clean"] or info["unfinishedOperations"] or info["unexpectedIgnored"]:
        raise ValueError("workspace must be clean and free of unfinished Git operations")
    actual_estimate = _actual_estimate_gib(repo, backend, source_repo)
    if actual_estimate > 10 and approval != "approved":
        raise ValueError("workspace estimates above 10 GiB require recorded approval")
    estimated_gib = actual_estimate
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
            "destination": str(target),
            "repoRoot": str(repo.resolve()),
            "sourceRepoRoot": str(source_repo),
            "intendedRoot": str(intended_root),
            "commonDirIdentity": common_dir_identity(source_repo),
            "backend": backend,
            "managedBy": managed_by,
            "lifetime": lifetime,
            "kind": kind,
            "state": "active" if task_id else "idle",
            "taskId": task_id,
            "phaseId": phase_id,
            "branch": branch or info["branch"],
            "headSha": info["head"],
            "baseSha": base_sha,
            "estimatedGiB": float(estimated_gib),
            "approval": approval,
            "approvalRecorded": approval,
            "everActive": False,
            "lastTaskState": None,
            "taskRevision": task_revision,
            "taskPath": str(_task_path(repo, task_path)) if task_path else None,
            "activeInvocationId": None,
            "leases": [],
            "processes": [],
            "allowedCaches": list(allowed_caches or []),
            "createdAt": now,
            "lastUsedAt": now,
            "quarantineReason": None,
            "releaseEvidence": None,
            "lastTaskId": None,
            "lastTaskPath": None,
            "lastTaskRevision": None,
        }
        registry["entries"].append(entry)
        _save_registry(repo, registry, expected_revision)
        return {"ok": True, "revision": expected_revision + 1, "entry": entry}


def _reuse_reasons(repo: Path, entry: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    source_repo = _entry_source_repo(repo, entry)
    target = Path(str(entry.get("path") or ""))
    info = inspect_registered_workspace(
        repo,
        target,
        list(entry.get("allowedCaches") or []),
        source_repo=source_repo,
    )
    reasons: list[str] = []
    if entry.get("commonDirIdentity") != common_dir_identity(source_repo): reasons.append("git-common-dir-mismatch")
    reasons.extend(_entry_identity_reasons(repo, entry))
    if entry.get("managedBy") != "lemmings": reasons.append("not-lemmings-managed")
    if entry.get("state") != "idle": reasons.append("not-idle")
    if entry.get("everActive") and entry.get("lastTaskState") != "Integrated": reasons.append("previous-task-not-integrated")
    exact_workspace = info["registered"] or (entry.get("backend") == "unity-clone" and info["standaloneGitRoot"])
    if not info["exists"] or not exact_workspace or info["primary"]: reasons.append("not-an-exact-reusable-workspace")
    if _has_symlink_component(target): reasons.append("symlink-alias")
    intended_root = Path(str(entry.get("intendedRoot") or repo.resolve().parent))
    if not _is_relative_to(target.resolve(), intended_root.resolve()): reasons.append("outside-intended-root")
    if not info["clean"]: reasons.append("dirty-or-untracked")
    if info["unfinishedOperations"]: reasons.append("unfinished-git-operation")
    if info["unexpectedIgnored"]: reasons.append("unexpected-ignored-files")
    reasons.extend(_process_owner_reasons(entry))
    return sorted(set(reasons)), info


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
        source_repo = _entry_source_repo(repo, entry)
        target = Path(str(entry.get("path") or ""))
        if entry.get("state") == "active" and entry.get("taskId") == task_id:
            info = inspect_registered_workspace(repo, target, list(entry.get("allowedCaches") or []), source_repo=source_repo)
            active_reasons = _process_owner_reasons(entry)
            active_reasons.extend(_entry_identity_reasons(repo, entry))
            backend = entry.get("backend")
            if entry.get("managedBy") != "lemmings":
                active_reasons.append("not-lemmings-managed")
            if backend not in {"code-worktree", "package-worktree", "unity-clone"}:
                active_reasons.append("invalid-managed-backend")
            try:
                common_identity = common_dir_identity(source_repo)
            except ValueError:
                common_identity = None
            if entry.get("commonDirIdentity") != common_identity:
                active_reasons.append("git-common-dir-mismatch")
            declared_root = entry.get("repoRoot")
            try:
                root_matches = bool(
                    declared_root
                    and _same_path(
                        _canonical_path(repo, str(declared_root), label="registered repository root"),
                        repo,
                    )
                )
            except ValueError:
                root_matches = False
            if not root_matches:
                active_reasons.append("repository-root-mismatch")
            exact_workspace = info["registered"] if backend != "unity-clone" else info["standaloneGitRoot"]
            if not info["exists"] or not exact_workspace:
                active_reasons.append("not-an-exact-registered-workspace")
            if backend == "package-worktree" and _git_root(source_repo) != source_repo:
                active_reasons.append("package-source-not-git-root")
            if _has_symlink_component(target):
                active_reasons.append("symlink-alias")
            if info["primary"] and entry.get("managedBy") == "lemmings":
                active_reasons.append("primary-managed-workspace")
            intended_root_value = entry.get("intendedRoot")
            intended_root = Path(str(intended_root_value or repo.resolve().parent))
            if (
                not intended_root_value
                or _has_symlink_component(intended_root)
                or not _is_relative_to(target.resolve(), intended_root.resolve())
            ):
                active_reasons.append("outside-intended-root")
            if entry.get("branch") != branch:
                active_reasons.append("registry-branch-mismatch")
            if entry.get("baseSha") != base_sha:
                active_reasons.append("registry-base-mismatch")
            if active_reasons or not info["clean"] or info["unfinishedOperations"] or info["unexpectedIgnored"] or info["head"] != base_sha or info["branch"] != branch:
                if not info["clean"]:
                    active_reasons.append("dirty-or-untracked")
                if info["unfinishedOperations"]:
                    active_reasons.append("unfinished-git-operation")
                if info["unexpectedIgnored"]:
                    active_reasons.append("unexpected-ignored-files")
                if info["head"] != base_sha:
                    active_reasons.append("head-mismatch")
                if info["branch"] != branch:
                    active_reasons.append("branch-mismatch")
                entry["state"] = "quarantined"
                entry["quarantineReason"] = ",".join(sorted(set(active_reasons or ["reserved-workspace-state-mismatch"])))
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
        if git(source_repo, "rev-parse", "--verify", f"{base_sha}^{{commit}}").returncode:
            raise ValueError(f"base commit does not resolve: {base_sha}")
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
            "lastTaskState": None, "lastTaskId": None, "lastTaskPath": None,
            "lastTaskRevision": None, "releaseEvidence": None,
        })
        _save_registry(repo, registry, expected_revision)
        return {"ok": True, "revision": expected_revision + 1, "entry": entry, "inspection": info}


def _remove_entry(
    repo: Path,
    registry: dict[str, Any],
    entry: dict[str, Any],
    *,
    cleanup_evidence: Mapping[str, Any] | None = None,
    active_release: bool = False,
) -> tuple[bool, str | None]:
    reasons, info = _reuse_reasons(repo, entry)
    if active_release:
        if not _active_task_entry(entry):
            reasons.append("active-release-state-mismatch")
        reasons = [reason for reason in reasons if reason != "not-idle"]
    else:
        try:
            _stored_pool_release_evidence(entry)
        except ValueError as error:
            reasons.append(str(error) or "canonical-task-required")
    if cleanup_evidence is None:
        reasons.append("canonical-task-required")
    expected_task_id = entry.get("taskId") or entry.get("lastTaskId")
    if cleanup_evidence is not None and expected_task_id and cleanup_evidence.get("taskId") != expected_task_id:
        reasons.append("canonical-task-mismatch")
    if cleanup_evidence is not None and entry.get("lastTaskPath") and not _same_path(
        Path(str(cleanup_evidence.get("taskPath") or "")),
        Path(str(entry["lastTaskPath"])),
    ):
        reasons.append("canonical-task-path-mismatch")
    if cleanup_evidence is not None and entry.get("lastTaskRevision") is not None and (
        cleanup_evidence.get("taskRevision") != entry.get("lastTaskRevision")
    ):
        reasons.append("canonical-task-revision-mismatch")
    if entry.get("managedBy") != "lemmings" or entry.get("lifetime") == "project" or entry.get("kind") == "validation":
        reasons.append("protected-workspace")
    if reasons:
        entry["state"] = "quarantined"
        entry["quarantineReason"] = ",".join(sorted(set(reasons)))
        return False, entry["quarantineReason"]
    target = Path(str(entry["path"])).resolve()
    source_repo = _entry_source_repo(repo, entry)
    if entry.get("backend") == "unity-clone" and target not in _registered_worktrees(source_repo):
        if not info["standaloneGitRoot"] or _same_path(target, source_repo):
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
        removed = git(source_repo, "worktree", "remove", str(target))
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
        evidence = None
        try:
            stored_evidence = _stored_pool_release_evidence(candidate)
            evidence = _verify_cleanup_task(
                repo,
                candidate,
                stored_evidence["taskPath"],
                stored_evidence["taskRevision"],
            )
        except ValueError as error:
            candidate["state"] = "quarantined"
            candidate["quarantineReason"] = str(error) or "canonical-task-required"
            ok, reason = False, candidate["quarantineReason"]
        else:
            ok, reason = _remove_entry(repo, registry, candidate, cleanup_evidence=evidence)
        removed.append({"workspaceId": candidate.get("workspaceId"), "removed": ok, "reason": reason})
        if not ok:
            continue
    return removed


def _actual_estimate_gib(repo: Path, backend: str, source_repo: Path) -> float:
    size = _size(source_repo)
    if backend == "unity-clone":
        game = find_game_project(source_repo)
        if game:
            size += _size(game / "Library")
    return size / GIB


def _prepare_record(
    repo: Path,
    task_path: str | Path,
    destination: str | Path,
    branch: str,
    approval: str,
) -> dict[str, Any]:
    task_file, task = _read_task(repo, task_path)
    workspace = _task_workspace(task)
    backend = str(workspace.get("backend") or "current")
    if backend not in WORKSPACE_BACKENDS - {"auto"}:
        raise ValueError(f"unsupported workspace backend: {backend}")
    managed_by = str(workspace.get("managedBy") or "lemmings")
    lifetime = str(workspace.get("lifetime") or "task")
    if managed_by != "lemmings":
        raise ValueError("prepare_workspace requires a lemmings-managed Task workspace")
    if lifetime not in LIFETIMES:
        raise ValueError("invalid Task workspace lifetime")
    workspace_id = workspace.get("workspaceId")
    if backend != "current" and not workspace_id:
        raise ValueError("isolated Task workspace requires workspaceId")
    if backend != "current":
        for field in ("repoRoot", "destination", "branch", "baseSha", "estimatedGiB", "approval"):
            if workspace.get(field) in (None, ""):
                raise ValueError(f"isolated Task workspace requires recorded {field}")
    target, intended_root = _validate_destination(repo, destination, workspace, allow_repo=backend == "current")
    if backend == "current" and not _same_path(target, repo):
        raise ValueError("current workspace destination must be the repository root")
    if backend != "current" and _same_path(target, repo):
        raise ValueError("managed workspace cannot use the primary checkout")
    declared_destination = workspace.get("destination") or workspace.get("destinationPath")
    if declared_destination and not _same_path(_canonical_path(repo, declared_destination, label="Task destination"), target):
        raise ValueError("prepare destination does not match the canonical Task record")
    declared_root = workspace.get("repoRoot") or workspace.get("repositoryRoot") or task.get("repoRoot")
    if declared_root and not _same_path(_canonical_path(repo, declared_root, label="Task repository root"), repo):
        raise ValueError("Task repository root does not match the prepare repository")
    declared_branch = workspace.get("branch")
    if backend != "current" and not branch:
        raise ValueError("isolated workspace branch is required")
    if declared_branch and declared_branch != branch:
        raise ValueError("prepare branch does not match the canonical Task record")
    declared_approval = workspace.get("approval")
    if declared_approval and declared_approval != approval:
        raise ValueError("prepare approval does not match the canonical Task record")
    estimated = workspace.get("estimatedGiB", 0)
    if not isinstance(estimated, (int, float)) or isinstance(estimated, bool) or estimated < 0:
        raise ValueError("Task workspace estimatedGiB must be a non-negative number")
    source_repo = repo.resolve()
    package_path = workspace.get("packagePath") or workspace.get("sourcePath") or workspace.get("packageRoot")
    if backend == "package-worktree":
        if not package_path:
            raise ValueError("package-worktree requires packagePath")
        package = _canonical_path(repo, package_path, label="package root")
        if not _same_path(package, repo) or _git_root(package) != package:
            raise ValueError("package-worktree requires --repo to be the package's own Git root")
        source_repo = repo.resolve()
    base_sha = workspace.get("baseSha")
    if task.get("baseSha") and task.get("baseSha") != base_sha:
        raise ValueError("Task baseSha and workspace baseSha must match")
    if backend != "current" and not isinstance(base_sha, str):
        raise ValueError("Task baseSha must resolve in the source repository")
    if backend != "current":
        if len(base_sha) not in {40, 64} or any(character not in "0123456789abcdefABCDEF" for character in base_sha):
            raise ValueError("Task baseSha must be a full immutable commit SHA")
        resolved_base = git(source_repo, "rev-parse", "--verify", f"{base_sha}^{{commit}}")
        if resolved_base.returncode or resolved_base.stdout.strip().casefold() != base_sha.casefold():
            raise ValueError("Task baseSha must resolve to its full immutable commit SHA")
        actual_estimate = _actual_estimate_gib(repo, backend, source_repo)
        if actual_estimate > 10 and approval != "approved":
            raise ValueError("workspace estimates above 10 GiB require recorded approval")
        estimated = actual_estimate
    if target.exists() and backend != "current":
        raise ValueError("workspace destination already exists")
    return {
        "taskPath": task_file,
        "task": task,
        "workspace": workspace,
        "workspaceId": str(workspace_id or task["taskId"]),
        "backend": backend,
        "managedBy": managed_by,
        "lifetime": lifetime,
        "kind": str(workspace.get("kind") or "writer"),
        "target": target,
        "intendedRoot": intended_root,
        "sourceRepo": source_repo,
        "baseSha": base_sha,
        "branch": branch or None,
        "estimatedGiB": float(estimated),
        "approval": approval,
    }


def prepare_workspace(
    repo: Path,
    *,
    task_path: str | Path,
    destination: str | Path,
    branch: str,
    expected_revision: int,
    approval: str,
) -> dict[str, Any]:
    record = _prepare_record(repo, task_path, destination, branch, approval)
    if record["backend"] == "current":
        raise ValueError("prepare_workspace cannot register the primary checkout as a managed workspace")
    with _registry_lock(repo):
        registry = load_registry(repo)
        if registry["revision"] != expected_revision:
            raise ValueError(f"stale workspace registry revision: expected {expected_revision}, actual {registry['revision']}")
        if any(
            isinstance(item, Mapping)
            and (
                item.get("workspaceId") == record["workspaceId"]
                or _same_path(Path(str(item.get("path") or "")), record["target"])
            )
            for item in registry["entries"]
        ):
            raise ValueError("workspace id and destination must be unique")
        now = utc_timestamp()
        entry = {
            "workspaceId": record["workspaceId"],
            "path": str(record["target"]),
            "destination": str(record["target"]),
            "repoRoot": str(repo.resolve()),
            "sourceRepoRoot": str(record["sourceRepo"]),
            "intendedRoot": str(record["intendedRoot"]),
            "commonDirIdentity": common_dir_identity(record["sourceRepo"]),
            "backend": record["backend"],
            "managedBy": record["managedBy"],
            "lifetime": record["lifetime"],
            "kind": record["kind"],
            "state": "active",
            "taskId": record["task"]["taskId"],
            "phaseId": None,
            "branch": record["branch"],
            "headSha": None,
            "baseSha": record["baseSha"],
            "estimatedGiB": record["estimatedGiB"],
            "approval": record["approval"],
            "approvalRecorded": record["approval"],
            "everActive": False,
            "lastTaskState": None,
            "taskRevision": record["task"]["revision"],
            "taskPath": str(record["taskPath"]),
            "activeInvocationId": None,
            "leases": [],
            "processes": [],
            "allowedCaches": [],
            "createdAt": now,
            "lastUsedAt": now,
            "quarantineReason": None,
            "releaseEvidence": None,
            "lastTaskId": None,
            "lastTaskPath": None,
            "lastTaskRevision": None,
            "prepared": False,
        }
        registry["entries"].append(entry)
        _save_registry(repo, registry, expected_revision)
        reservation_revision = expected_revision + 1
        source_repo = record["sourceRepo"]
        target = record["target"]
        try:
            if record["backend"] in {"code-worktree", "package-worktree"}:
                created = git(source_repo, "worktree", "add", "-b", record["branch"], str(target), str(record["baseSha"]))
            elif record["backend"] == "unity-clone":
                created = git(source_repo, "clone", "--no-hardlinks", str(source_repo), str(target))
                if not created.returncode:
                    created = git(target, "switch", "-c", record["branch"], str(record["baseSha"]))
            else:
                raise ValueError(f"unsupported provisioning backend: {record['backend']}")
            if created.returncode:
                raise ValueError(created.stderr.strip() or "workspace provisioning failed")
            info = inspect_registered_workspace(
                repo,
                target,
                list(entry.get("allowedCaches") or []),
                source_repo=source_repo,
            )
            exact = info["registered"] if record["backend"] != "unity-clone" else info["standaloneGitRoot"]
            if not info["exists"] or not exact or info["primary"] or not info["clean"] or info["unfinishedOperations"]:
                raise ValueError("provisioned workspace failed exact Git safety validation")
            entry.update({
                "state": "active",
                "headSha": info["head"],
                "branch": info["branch"] or record["branch"],
                "everActive": True,
                "prepared": True,
                "lastUsedAt": utc_timestamp(),
                "quarantineReason": None,
            })
        except Exception as error:
            entry["state"] = "quarantined"
            entry["quarantineReason"] = str(error) or "workspace-provision-failed"
            _save_registry(repo, registry, reservation_revision)
            raise
        _save_registry(repo, registry, reservation_revision)
        return {"ok": True, "revision": reservation_revision + 1, "entry": entry}


def release_workspace(
    repo: Path,
    *,
    workspace_id: str | None = None,
    expected_revision: int,
    task_state: str | None = None,
    integration_evidence: bool | None = None,
    action: str = "pool",
    retention_approved: bool = False,
    profile: Mapping[str, Any] | None = None,
    task_path: str | Path | None = None,
    task_revision: int | None = None,
) -> dict[str, Any]:
    if action not in {"pool", "remove", "retain"}:
        raise ValueError("release action must be pool, remove, or retain")
    with _registry_lock(repo):
        registry = load_registry(repo)
        if registry["revision"] != expected_revision:
            raise ValueError(f"stale workspace registry revision: expected {expected_revision}, actual {registry['revision']}")
        if workspace_id is None:
            if not task_path:
                raise ValueError("canonical Task path is required")
            _, task = _read_task(repo, task_path)
            workspace_id = str(_task_workspace(task).get("workspaceId") or "")
            if not workspace_id:
                raise ValueError("canonical Task workspaceId is required")
        entry = _entry(registry, workspace_id)
        active_release = _active_task_entry(entry)
        owner_reasons = _process_owner_reasons(entry)
        if owner_reasons:
            reason = ",".join(owner_reasons)
            if entry.get("managedBy") == "lemmings":
                entry["state"] = "quarantined"
                entry["quarantineReason"] = reason
            _save_registry(repo, registry, expected_revision)
            return {"ok": True, "revision": expected_revision + 1, "action": "retained", "reason": reason, "evicted": []}
        evidence: dict[str, Any] | None = None
        reason: str | None = None
        if task_path:
            try:
                evidence = _verify_cleanup_task(repo, entry, task_path, task_revision)
            except ValueError as error:
                reason = str(error) or "canonical-task-required"
        else:
            reason = "canonical-task-required"
        if reason:
            if entry.get("managedBy") == "lemmings" and (entry.get("everActive") or entry.get("state") == "active"):
                entry["state"] = "quarantined"
                entry["quarantineReason"] = reason
            else:
                entry["state"] = "idle"
                entry["quarantineReason"] = "protected-workspace"
            _save_registry(repo, registry, expected_revision)
            return {"ok": True, "revision": expected_revision + 1, "action": "retained", "reason": reason, "evicted": []}
        if active_release:
            entry["lastTaskState"] = "Integrated"
            entry["lastTaskId"] = evidence["taskId"]
            entry["lastTaskPath"] = evidence["taskPath"]
            entry["lastTaskRevision"] = evidence["taskRevision"]
            entry["releaseEvidence"] = dict(evidence)
        entry["retentionApproved"] = bool(retention_approved)
        entry["observedGiB"] = round(_size(Path(str(entry.get("path")))) / GIB, 3)
        protected = entry.get("managedBy") != "lemmings" or entry.get("lifetime") == "project" or entry.get("kind") == "validation"
        removed = False
        evicted: list[dict[str, Any]] = []
        if protected:
            entry.update({"state": "idle", "taskId": None, "phaseId": None, "lastUsedAt": utc_timestamp(), "quarantineReason": None})
            reason = "protected-workspace"
            action = "retain"
        elif action == "retain":
            entry.update({"state": "idle", "taskId": None, "phaseId": None, "lastUsedAt": utc_timestamp(), "quarantineReason": None})
            reason = "retained-by-lifecycle-policy"
        elif action == "remove" or not _pool_policy(profile).get("enabled"):
            removed, reason = _remove_entry(
                repo,
                registry,
                entry,
                cleanup_evidence=evidence,
                active_release=active_release,
            )
            action = "remove"
        else:
            entry["state"] = "idle"
            reasons, info = _reuse_reasons(repo, entry)
            if reasons:
                entry["state"] = "quarantined"
                entry["quarantineReason"] = ",".join(reasons)
                action, reason = "retain", entry["quarantineReason"]
            else:
                entry.update({"taskId": None, "phaseId": None, "headSha": info["head"], "lastUsedAt": utc_timestamp(), "quarantineReason": None})
                evicted = _evict_pool(repo, registry, profile)
                removed = entry not in registry["entries"]
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


def remove_workspace(
    repo: Path,
    *,
    workspace_id: str | None = None,
    expected_revision: int,
    task_path: str | Path | None = None,
    task_revision: int | None = None,
) -> dict[str, Any]:
    with _registry_lock(repo):
        registry = load_registry(repo)
        if registry["revision"] != expected_revision:
            raise ValueError(f"stale workspace registry revision: expected {expected_revision}, actual {registry['revision']}")
        if workspace_id is None:
            if not task_path:
                raise ValueError("canonical Task path is required")
            _, task = _read_task(repo, task_path)
            workspace_id = str(_task_workspace(task).get("workspaceId") or "")
            if not workspace_id:
                raise ValueError("canonical Task workspaceId is required")
        entry = _entry(registry, workspace_id)
        active_release = _active_task_entry(entry)
        evidence = None
        reason: str | None = None
        if task_path:
            try:
                evidence = _verify_cleanup_task(repo, entry, task_path, task_revision)
            except ValueError as error:
                reason = str(error) or "canonical-task-required"
        else:
            reason = "canonical-task-required"
        if reason:
            if entry.get("managedBy") == "lemmings" and (entry.get("everActive") or entry.get("state") == "active"):
                entry["state"] = "quarantined"
                entry["quarantineReason"] = reason
            else:
                entry["state"] = "idle"
                entry["quarantineReason"] = "protected-workspace"
            _save_registry(repo, registry, expected_revision)
            return {"ok": False, "revision": expected_revision + 1, "action": "quarantined", "reason": reason}
        if active_release:
            entry["lastTaskState"] = "Integrated"
            entry["lastTaskId"] = evidence["taskId"]
            entry["lastTaskPath"] = evidence["taskPath"]
            entry["lastTaskRevision"] = evidence["taskRevision"]
            entry["releaseEvidence"] = dict(evidence)
        removed, reason = _remove_entry(
            repo,
            registry,
            entry,
            cleanup_evidence=evidence,
            active_release=active_release,
        )
        _save_registry(repo, registry, expected_revision)
        return {"ok": removed, "revision": expected_revision + 1, "action": "removed" if removed else "quarantined", "reason": reason}
