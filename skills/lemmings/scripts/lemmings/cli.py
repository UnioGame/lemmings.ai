"""Command line for the Lemmings helper. Every command prints one JSON object."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__, agents, dispatch, scope, workspace
from .gitutil import CONFIG_PATH, HelperError, git, load_config, toplevel


def _version(command: list[str]) -> str | None:
    executable = shutil.which(command[0])
    if not executable:
        return None
    try:
        process = subprocess.run([executable, *command[1:]], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    lines = (process.stdout or process.stderr).strip().splitlines()
    return lines[0] if lines else "unknown"


def doctor(repo: Path) -> dict[str, Any]:
    repo = toplevel(repo)
    config = load_config(repo)
    hosts = {name: _version([name, "--version"]) for name in ("codex", "claude", "opencode")}
    result: dict[str, Any] = {"version": __version__, "python": sys.version.split()[0],
                              "git": git(repo, "--version").stdout.strip(), "repo": str(repo),
                              "config": CONFIG_PATH if (repo / CONFIG_PATH).is_file() else None, "hosts": hosts}
    try:
        configured = agents.load_agents(config)
    except HelperError as error:
        return {"ok": False, **result, "error": str(error)}
    rows = agents.describe(configured)
    problems = []
    for row in rows:
        needed = [row["host"], *[item["host"] for item in configured.get(row["name"], {}).get("fallback") or []]]
        missing = [host for host in needed if host != "native" and not hosts.get(host)]
        if missing:
            problems.append(f"agent {row['name']} needs {', '.join(missing)} on PATH")
    stale = agents.sync(repo, configured, check=True)["stale"]
    if stale:
        problems.append("native agent definitions are out of date; run `agents sync`")
    return {"ok": not problems, **result, "agents": rows, "problems": problems}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="repository (default: current directory)")
    parser = argparse.ArgumentParser(prog="lemmings", description="Deterministic helpers for the Lemmings skill")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", parents=[common], help="check Python, Git, host CLIs, and configured role routes")

    ws = commands.add_parser("workspace", help="isolated task worktrees and clones").add_subparsers(dest="action", required=True)
    create = ws.add_parser("create", parents=[common], help="create task/<slug> in a new worktree (or clone)")
    create.add_argument("slug")
    create.add_argument("--clone", action="store_true", help="standalone clone instead of a linked worktree")
    create.add_argument("--base", default="HEAD")
    create.add_argument("--branch", help="reuse this existing branch instead of creating task/<slug>")
    create.add_argument("--approve-large", action="store_true", help="the user approved a workspace above the size limit")
    ws.add_parser("estimate", parents=[common], help="estimate the size of a new workspace")
    ws.add_parser("list", parents=[common], help="list Lemmings workspaces with their status")
    remove = ws.add_parser("remove", parents=[common], help="remove a clean workspace and its merged task branch")
    remove.add_argument("slug")
    remove.add_argument("--keep-branch", action="store_true")

    team = commands.add_parser("agents", help="named workers, reviewers, and explorers").add_subparsers(dest="action", required=True)
    team.add_parser("list", parents=[common], help="agents, defaults, escalation chains, and native subagent names")
    sync = team.add_parser("sync", parents=[common], help="write native Codex/Claude subagent files for configured agents")
    sync.add_argument("--check", action="store_true", help="only report files that are out of date")

    check = commands.add_parser("scope", parents=[common], help="check changed paths against owned/forbidden rules")
    check.add_argument("--base", required=True)
    check.add_argument("--head", default="HEAD")
    check.add_argument("--worktree", action="store_true", help="compare base with the working tree, including untracked files")
    check.add_argument("--owned", nargs="*", default=[])
    check.add_argument("--forbidden", nargs="*", default=[])

    run = commands.add_parser("dispatch", parents=[common], help="run a worker, reviewer, or explorer on another host CLI")
    run.add_argument("role", nargs="?", choices=agents.ROLES, help="use the role's default agent")
    run.add_argument("--agent", help="a named agent from .agents/lemmings.json")
    run.add_argument("--brief", required=True, help="Markdown brief file, or - for stdin")
    run.add_argument("--cwd", help="checkout the role works in (default: --repo)")
    run.add_argument("--host", choices=dispatch.HOSTS[1:])
    run.add_argument("--model")
    run.add_argument("--effort")
    run.add_argument("--timeout", type=int, default=dispatch.DEFAULT_TIMEOUT)
    run.add_argument("--dry-run", action="store_true", help="print the command lines without running them")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo)
    if args.command == "doctor":
        return doctor(repo)
    if args.command == "workspace":
        if args.action == "create":
            return {"ok": True, **workspace.create(repo, args.slug, clone=args.clone, base=args.base,
                                                   branch=args.branch, approve_large=args.approve_large)}
        if args.action == "estimate":
            root = toplevel(repo)
            return {"ok": True, **workspace.estimate(root, load_config(root))}
        if args.action == "list":
            return {"ok": True, "workspaces": workspace.list_workspaces(repo)}
        return {"ok": True, **workspace.remove(repo, args.slug, keep_branch=args.keep_branch)}
    if args.command == "agents":
        root = toplevel(repo)
        configured = agents.load_agents(load_config(root))
        if args.action == "list":
            return {"ok": True, "agents": agents.describe(configured)}
        outcome = agents.sync(root, configured, check=args.check)
        return {"ok": not outcome["stale"], **outcome}
    if args.command == "scope":
        root = toplevel(repo)
        paths = scope.changed_paths(root, args.base, args.head, worktree=args.worktree)
        violations = scope.check_scope(paths, args.owned, args.forbidden)
        return {"ok": not violations, "changedPaths": paths, "violations": violations}
    brief = sys.stdin.read() if args.brief == "-" else Path(args.brief).read_text(encoding="utf-8-sig")
    if not args.role and not args.agent:
        raise HelperError("dispatch needs a role or --agent")
    return dispatch.dispatch(repo, args.role, brief, agent=args.agent, cwd=Path(args.cwd) if args.cwd else None, host=args.host,
                             model=args.model, effort=args.effort, timeout=args.timeout, dry_run=args.dry_run)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (HelperError, OSError) as error:
        result = {"ok": False, "error": str(error)}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1
