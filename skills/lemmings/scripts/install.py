#!/usr/bin/env python3
"""Install the Lemmings skill and Codex agent definitions into a Git repository."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "6.5.0"
ROLES = ("worker", "reviewer", "explorer")
RETIRED_AGENTS = ("lemmings-orchestrator.toml", "lemmings-validator.toml", "lemmings-summarizer.toml")


def repository_root(path: Path) -> Path | None:
    process = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=False)
    return Path(process.stdout.strip()).resolve() if process.returncode == 0 else None


def plan(package_root: Path, repo: Path) -> list[tuple[Path | None, Path]]:
    """(source, target) pairs; a None source removes a retired file."""
    skill = package_root / "skills" / "lemmings"
    items: list[tuple[Path | None, Path]] = [(skill, repo / ".agents/skills/lemmings")]
    for role in ROLES:
        items.append((package_root / "agents" / f"lemmings-{role}.toml", repo / ".codex/agents" / f"lemmings-{role}.toml"))
        items.append((package_root / "agents" / f"lemmings-{role}.md", repo / ".claude/agents" / f"lemmings-{role}.md"))
    items += [(None, repo / ".codex/agents" / name) for name in RETIRED_AGENTS if (repo / ".codex/agents" / name).exists()]
    return items


def install(repo: Path, dry_run: bool = False) -> None:
    package_root = Path(__file__).resolve().parents[3]
    items = plan(package_root, repo)
    for source, target in items:
        print(("remove: " if source is None else "install: ") + str(target))
    if dry_run:
        return
    transaction = Path(tempfile.mkdtemp(prefix=".lemmings-install-", dir=repo))
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        staged = []
        for index, (source, target) in enumerate(items):
            copy = None
            if source is not None:
                copy = transaction / "stage" / str(index)
                if source.is_dir():
                    shutil.copytree(source, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                else:
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, copy)
            staged.append((copy, target))
        for index, (copy, target) in enumerate(staged):
            if target.exists():
                backup = transaction / "backup" / str(index)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(backup))
                backups.append((target, backup))
            if copy is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(copy), str(target))
                installed.append(target)
    except Exception:
        # Undo only what this run changed: drop new files, then restore the originals.
        for target in reversed(installed):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for target, backup in reversed(backups):
            shutil.move(str(backup), str(target))
        raise
    finally:
        shutil.rmtree(transaction, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Install the Lemmings skill into a Git repository")
    parser.add_argument("--repo", default=".", help="target repository (default: current directory)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    repo = repository_root(Path(args.repo))
    if repo is None:
        print(f"Lemmings install failed: not a Git repository: {args.repo}", file=sys.stderr)
        return 1
    try:
        install(repo, args.dry_run)
    except (OSError, shutil.Error) as error:
        print(f"Lemmings install failed and was rolled back: {error}", file=sys.stderr)
        return 1
    if not args.dry_run:
        # Named agents from .agents/lemmings.json become native Codex/Claude subagents.
        helper = repo / ".agents/skills/lemmings/scripts/run.py"
        synced = subprocess.run([sys.executable, str(helper), "agents", "sync", "--repo", str(repo)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if synced.returncode:
            print(f"Lemmings installed, but `agents sync` failed: {synced.stdout.strip() or synced.stderr.strip()}",
                  file=sys.stderr)
            return 1
    print(f"Lemmings {VERSION} {'install dry run complete' if args.dry_run else 'installed'} in {repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
