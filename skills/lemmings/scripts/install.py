#!/usr/bin/env python3
"""Install a complete, self-contained Lemmings skill bundle."""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "4.0.0"
OWNED_AGENTS = (
    "lemmings-worker.toml", "lemmings-reviewer.toml", "lemmings-explorer.toml",
    "lemmings-orchestrator.toml", "lemmings-validator.toml", "lemmings-summarizer.toml",
)


def git(at: Path, *args: str) -> str:
    process = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(at), *args], capture_output=True, text=True, check=False)
    return process.stdout.strip() if process.returncode == 0 else ""


def repository_root(path: Path) -> Path | None:
    value = git(path, "rev-parse", "--show-toplevel")
    return Path(value).resolve() if value else None


def outermost_superproject(path: Path) -> Path | None:
    current, result = path.resolve(), repository_root(path)
    while True:
        parent = git(current, "rev-parse", "--show-superproject-working-tree")
        if not parent:
            return result
        current = Path(parent).resolve()
        result = repository_root(current) or current


def is_unity_project(path: Path) -> bool:
    return (path / "Assets").is_dir() and (path / "Packages/manifest.json").is_file() and (path / "ProjectSettings/ProjectVersion.txt").is_file()


def find_unity_projects(repo: Path) -> list[Path]:
    projects = []
    for manifest in repo.rglob("Packages/manifest.json"):
        if any(part in {".git", "Library", "PackageCache"} for part in manifest.parts):
            continue
        candidate = manifest.parent.parent.resolve()
        if is_unity_project(candidate):
            projects.append(candidate)
    return sorted(set(projects))


def relative(child: Path, parent: Path) -> str:
    return child.resolve().relative_to(parent.resolve()).as_posix()


def git_common_dir(repo: Path) -> Path:
    value = git(repo, "rev-parse", "--git-common-dir")
    if not value:
        raise ValueError(f"cannot resolve Git common directory for {repo}")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def installation_busy(common: Path) -> str | None:
    active = common / "lemmings/active.json"
    if active.is_file():
        return f"active Lemmings runtime: {active}"
    for registry in sorted((common / "lemmings").glob("workspaces-v*.json")):
        try:
            entries = load_json(registry).get("entries", [])
        except (OSError, ValueError, json.JSONDecodeError):
            return f"unreadable workspace registry: {registry}"
        for entry in entries:
            if isinstance(entry, dict) and (entry.get("state") == "active" or entry.get("leases") or entry.get("processes") or entry.get("invocationId")):
                return f"busy Lemmings workspace {entry.get('workspaceId') or '<unknown>'}"
    return None


def same_tree(left: Path, right: Path) -> bool:
    comparison = filecmp.dircmp(left, right, ignore=["__pycache__"])
    return not comparison.left_only and not comparison.right_only and not comparison.diff_files and all(
        same_tree(left / name, right / name) for name in comparison.common_dirs
    )


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def move_if_present(source: Path, destination: Path, moved: list[tuple[Path, Path]]) -> None:
    if source.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved.append((source, destination))


def install(args: argparse.Namespace) -> int:
    if sys.version_info < (3, 10):
        raise ValueError("Lemmings 4.0 requires Python 3.10 or newer")
    skill_source = Path(__file__).resolve().parents[1]
    package_root = Path(__file__).resolve().parents[3]
    if "Library" in package_root.parts and "PackageCache" in package_root.parts:
        raise ValueError(f"refusing to install from Unity PackageCache: {package_root}")
    if args.repo:
        repo = Path(args.repo).resolve()
        if repository_root(repo) is None:
            raise ValueError(f"not a Git repository: {repo}")
    else:
        repo = outermost_superproject(package_root)
    if not repo:
        raise ValueError("cannot infer the consumer Git repository; pass --repo PATH")
    project = (repo / args.project).resolve() if args.project else None
    if project is None:
        projects = find_unity_projects(repo)
        if len(projects) != 1:
            raise ValueError("expected one Unity project; pass --project PATH")
        project = projects[0]
    if not is_unity_project(project):
        raise ValueError(f"not a Unity project: {project}")
    try:
        project_relative = relative(project, repo)
    except ValueError as error:
        raise ValueError("Unity project must be inside the consumer repository") from error
    common = git_common_dir(repo)
    busy = installation_busy(common)
    if busy:
        raise ValueError(f"refusing to update while Lemmings is active: {busy}")

    profile = load_json(skill_source / "defaults.json")
    profile["game"]["projectPath"] = project_relative
    profile["game"]["workspace"]["validationPath"] = f"../{repo.name}.lemmings.validation"
    try:
        profile["tooling"] = {"root": relative(package_root, repo)}
        package_inside_repo = True
    except ValueError:
        package_inside_repo = False

    skill_target = repo / ".agents/skills/lemmings"
    profile_target = repo / ".agents/lemmings.json"
    agents_source, agents_target = package_root / "agents", repo / ".codex/agents"
    environment = common / "lemmings/environment.json"
    if args.dry_run:
        print(f"replace: {skill_target}")
        print(f"replace: {profile_target}")
        for name in OWNED_AGENTS:
            if (agents_source / name).is_file() or (agents_target / name).exists():
                print(f"replace/delete: {agents_target / name}")
        print("Lemmings 4.0 install dry run complete.")
        return 0

    transaction = Path(tempfile.mkdtemp(prefix=".lemmings-install-", dir=repo))
    stage, backup = transaction / "stage", transaction / "backup"
    moved: list[tuple[Path, Path]] = []
    try:
        shutil.copytree(skill_source, stage / "skill", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        write_json(stage / "lemmings.json", profile)
        (stage / "agents").mkdir(parents=True)
        for name in OWNED_AGENTS[:3]:
            shutil.copy2(agents_source / name, stage / "agents" / name)
        move_if_present(skill_target, backup / "skill", moved)
        move_if_present(profile_target, backup / "lemmings.json", moved)
        for name in OWNED_AGENTS:
            move_if_present(agents_target / name, backup / "agents" / name, moved)
        move_if_present(environment, backup / "environment.json", moved)

        skill_target.parent.mkdir(parents=True, exist_ok=True)
        agents_target.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage / "skill"), str(skill_target))
        if os.environ.get("LEMMINGS_INSTALL_FAIL_AFTER") == "skill":
            raise RuntimeError("injected failure after skill replacement")
        for agent in (stage / "agents").iterdir():
            shutil.move(str(agent), str(agents_target / agent.name))
        if os.environ.get("LEMMINGS_INSTALL_FAIL_AFTER") == "agents":
            raise RuntimeError("injected failure after agent replacement")
        profile_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage / "lemmings.json"), str(profile_target))
        if not package_inside_repo:
            write_json(environment, {"schemaVersion": 4, "toolRoot": str(package_root)})
        if os.environ.get("LEMMINGS_INSTALL_FAIL_AFTER") == "config":
            raise RuntimeError("injected failure after config replacement")
        if not same_tree(skill_source, skill_target):
            raise RuntimeError("installed skill differs from source bundle")
        process = subprocess.run(
            [sys.executable, str(skill_target / "scripts/run.py"), "doctor", "--repo", str(repo)],
            capture_output=True, text=True, check=False,
        )
        if process.returncode:
            raise RuntimeError(process.stdout.strip() or process.stderr.strip() or "installed doctor failed")
    except Exception:
        shutil.rmtree(skill_target, ignore_errors=True)
        profile_target.unlink(missing_ok=True)
        environment.unlink(missing_ok=True)
        for name in OWNED_AGENTS:
            (agents_target / name).unlink(missing_ok=True)
        for original, saved in reversed(moved):
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(saved), str(original))
        raise
    finally:
        shutil.rmtree(transaction, ignore_errors=True)
    print(f"Lemmings {VERSION} installed and verified; runtime is inactive.")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Install the self-contained Lemmings skill")
    parser.add_argument("--repo")
    parser.add_argument("--project")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        return install(args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Lemmings install failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
