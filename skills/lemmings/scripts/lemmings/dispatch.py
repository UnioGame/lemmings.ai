"""Run one Lemmings role on another host CLI (Codex, Claude Code, OpenCode).

The manager decides what to dispatch and reads the returned report. This module
only builds a safe command line, enforces a deadline, records the run, and
reports which model actually answered.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .gitutil import HelperError, load_config, state_dir, toplevel

HOSTS = ("native", "codex", "claude", "opencode")
ROLES = ("worker", "reviewer", "explorer")
READ_ONLY = {"reviewer", "explorer"}
MAX_OUTPUT = 16 * 1024 * 1024
DEFAULT_TIMEOUT = 1800
CLAUDE_ALIASES = {"default", "sonnet", "opus", "haiku", "fable"}
CODEX_DISABLED = ("multi_agent", "plugins", "hooks", "apps", "browser_use", "computer_use", "image_generation")

PREAMBLE = {
    "worker": ("You are a Lemmings worker. Implement only the brief below inside its owned paths. "
               "Read the repository rules (AGENTS.md, CLAUDE.md) that apply to your change. Do not delegate or edit outside ownership. "
               "Run the listed checks, commit your change on the current branch, and stop when acceptance passes. "
               "Finish with a short report: status (done|blocked), commit, changed paths, check results, remaining risks."),
    "reviewer": ("You are a Lemmings reviewer. Do not modify files. Review the candidate described below against its "
                 "acceptance criteria and checks. Report only P0-P2 findings with a concrete failure scenario; list P3 "
                 "ideas as follow-ups that never block. The first line of your answer must be exactly "
                 "'VERDICT: Accepted' or 'VERDICT: ChangesRequested'."),
    "explorer": ("You are a Lemmings explorer. Do not modify files. Answer the question below with the smallest "
                 "sufficient investigation and cite file:line evidence."),
}


def route_chain(config: Mapping[str, Any], role: str, override: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The configured route for a role followed by its explicit fallbacks."""
    configured = dict(((config.get("roles") or {}).get(role)) or {"host": "native"})
    fallback = [dict(item) for item in configured.pop("fallback", []) or [] if isinstance(item, Mapping)]
    if override.get("host") and override["host"] != configured.get("host"):
        configured = {"host": override["host"]}
    primary = {**configured, **{key: value for key, value in override.items() if value}}
    chain = [primary] + ([] if override.get("host") or override.get("model") else fallback)
    for route in chain:
        if route.get("host") not in HOSTS:
            raise HelperError(f"unknown host for {role}: {route.get('host')!r}; use one of {', '.join(HOSTS)}")
        for key in ("model", "effort", "profile"):
            if str(route.get(key) or "").startswith("-"):
                raise HelperError(f"invalid {key} for {role}")
        if route["host"] == "opencode" and route.get("model") and "/" not in route["model"]:
            raise HelperError("OpenCode models use provider/model")
    return chain


def _executable(name: str) -> str | None:
    return shutil.which(name)


def build_command(route: Mapping[str, Any], role: str, cwd: Path, run_dir: Path) -> dict[str, Any]:
    host, model, effort = route["host"], route.get("model"), route.get("effort")
    reader = role in READ_ONLY
    env: dict[str, str] = {}
    if host == "codex":
        argv = ["codex", "exec", "--ephemeral", "--sandbox", "read-only" if reader else "workspace-write",
                "--cd", str(cwd), "--json", "--color", "never",
                "--output-last-message", str(run_dir / "report.md")]
        if route.get("profile"):
            argv += ["--profile", str(route["profile"])]
        if model:
            argv += ["--model", str(model)]
        for feature in CODEX_DISABLED:
            argv += ["--disable", feature]
        argv += ["-c", 'approval_policy="never"', "-c", 'web_search="disabled"']
        if effort:
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        argv.append("-")
    elif host == "claude":
        tools = ["Read", "Glob", "Grep", "Bash"] + ([] if reader else ["Edit", "Write"])
        allow = ["Read", "Glob", "Grep", "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git show*)"]
        if not reader:
            allow += ["Edit", "Write", "Bash"]
        deny = ["Task", "Agent", "WebFetch", "WebSearch", "NotebookEdit", "mcp__*"] + (["Edit", "Write"] if reader else [])
        settings = run_dir / "claude-settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": allow, "deny": deny}}), encoding="utf-8")
        argv = ["claude", "-p", "--safe-mode", "--no-session-persistence", "--output-format", "json",
                "--permission-mode", "dontAsk", "--tools", ",".join(tools),
                "--disallowedTools", ",".join(deny), "--settings", str(settings)]
        if model:
            argv += ["--model", str(model)]
        if effort:
            argv += ["--effort", str(effort)]
    elif host == "opencode":
        permission: dict[str, Any] = {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow", "list": "allow"}
        if not reader:
            permission.update({"edit": "allow", "bash": "allow"})
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"share": "disabled", "permission": permission})
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
        argv = ["opencode", "run", "--pure", "--dir", str(cwd), "--format", "json"]
        if model:
            argv += ["--model", str(model)]
        if effort:
            argv += ["--variant", str(effort)]
    else:
        raise HelperError("native routes are dispatched by the current host's own subagents, not by this helper")
    return {"argv": argv, "env": env}


class _WindowsTree:
    """Kill the whole child process tree through a Job object."""

    def __init__(self, process: subprocess.Popen[bytes]):
        from ctypes import wintypes as w
        api = self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.restype = w.HANDLE
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        api.CloseHandle.argtypes = [w.HANDLE]
        self.handle = api.CreateJobObjectW(None, None)
        if self.handle:
            api.AssignProcessToJobObject(self.handle, int(process._handle))  # type: ignore[attr-defined]

    def terminate(self) -> None:
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class _PosixTree:
    def __init__(self, process: subprocess.Popen[bytes]):
        self.pid = process.pid

    def terminate(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def close(self) -> None:
        pass


def _json_values(text: str) -> list[Any]:
    try:
        return [json.loads(text)]
    except ValueError:
        values = []
        for line in text.splitlines():
            try:
                values.append(json.loads(line))
            except ValueError:
                pass
        return values


def parse_output(host: str, stdout: str, report_file: Path | None = None) -> dict[str, Any]:
    """Extract the final report, observed models, and token usage from host output."""
    values = _json_values(stdout)
    models: set[str] = set()
    usage: dict[str, int] = {}
    texts: list[str] = []
    errors: list[str] = []

    def add_usage(source: Mapping[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[key] = usage.get(key, 0) + int(value)

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            if isinstance(value.get("modelUsage"), Mapping):
                models.update(str(key) for key in value["modelUsage"])
            for key in ("model", "modelID", "model_id", "modelId"):
                if isinstance(value.get(key), str) and value[key]:
                    models.add(value[key])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
        if not isinstance(value, Mapping):
            continue
        if value.get("type") == "error" or value.get("is_error") is True:
            error = value.get("error") if isinstance(value.get("error"), Mapping) else {}
            message = ((error.get("data") or {}).get("message") if isinstance(error.get("data"), Mapping) else None)                 or error.get("message") or value.get("message") or value.get("result")
            if message:
                errors.append(str(message))
        if host == "claude":
            if isinstance(value.get("result"), str):
                texts.append(value["result"])
            if isinstance(value.get("usage"), Mapping):
                add_usage(value["usage"])
        elif host == "codex":
            item = value.get("item") or {}
            if item.get("type") == "agent_message":
                texts.append(str(item.get("text") or ""))
            if value.get("type") == "turn.completed" and isinstance(value.get("usage"), Mapping):
                add_usage(value["usage"])
        elif host == "opencode":
            part = value.get("part") or {}
            if value.get("type") == "text" and part.get("text"):
                texts.append(str(part["text"]))
            if isinstance(part.get("tokens"), Mapping):
                add_usage({key: item for key, item in part["tokens"].items() if not isinstance(item, Mapping)})
    report = ""
    if report_file and report_file.is_file():
        report = report_file.read_text(encoding="utf-8", errors="replace").strip()
    if not report and texts:
        report = texts[-1].strip() if host != "opencode" else "".join(texts).strip()
    if not values and not report:
        report = stdout.strip()
    verdict = None
    match = re.search(r"^\s*\**VERDICT:\s*\**\s*(Accepted|ChangesRequested)", report, re.MULTILINE | re.IGNORECASE)
    if match:
        verdict = "Accepted" if match.group(1).lower() == "accepted" else "ChangesRequested"
    return {"report": report, "verdict": verdict, "observedModels": sorted(models), "usage": usage or None,
            "error": errors[-1] if errors else None}


def model_confirmed(requested: str | None, observed: list[str]) -> bool | None:
    if not requested or not observed:
        return None
    wanted = requested.split("/")[-1].casefold()
    names = [item.split("/")[-1].casefold() for item in observed]
    if wanted in names:
        return True
    return wanted in CLAUDE_ALIASES and any(wanted in name for name in names)


def _run_process(argv: list[str], env: Mapping[str, str], cwd: Path, stdin: str, log: Path, timeout: int) -> tuple[int | None, bool]:
    kwargs: dict[str, Any] = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
    with log.open("wb") as output:
        process = subprocess.Popen(argv, cwd=cwd, env={**os.environ, **env}, stdin=subprocess.PIPE,
                                   stdout=output, stderr=subprocess.STDOUT, **kwargs)
        tree = _WindowsTree(process) if os.name == "nt" else _PosixTree(process)
        payload: bytes | None = stdin.encode("utf-8")
        deadline = time.monotonic() + timeout
        timed_out = False
        try:
            while True:
                if time.monotonic() >= deadline or log.stat().st_size > MAX_OUTPUT:
                    timed_out = True
                    tree.terminate()
                    break
                try:
                    process.communicate(input=payload, timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    payload = None
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tree.terminate()
            tree.terminate()
        finally:
            tree.close()
    return process.returncode, timed_out


def dispatch(repo: Path, role: str, brief: str, *, cwd: Path | None = None, host: str | None = None,
             model: str | None = None, effort: str | None = None, timeout: int = DEFAULT_TIMEOUT,
             dry_run: bool = False) -> dict[str, Any]:
    if role not in ROLES:
        raise HelperError(f"role must be one of {', '.join(ROLES)}")
    repo = toplevel(repo)
    cwd = (cwd or repo).resolve()
    chain = route_chain(load_config(repo), role, {"host": host, "model": model, "effort": effort})
    if chain[0]["host"] == "native":
        return {"ok": False, "status": "native", "role": role,
                "message": "role is configured as native; use this host's lemmings-" + role + " subagent"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if dry_run:
        run_dir = Path(tempfile.mkdtemp(prefix="lemmings-dry-run-"))
    else:
        run_dir = state_dir(repo) / "runs" / f"{stamp}-{role}-{uuid.uuid4().hex[:6]}"
        run_dir.mkdir(parents=True)
    prompt = PREAMBLE[role] + "\n\n" + brief.strip() + "\n"
    (run_dir / "brief.md").write_text(prompt, encoding="utf-8")
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    for index, route in enumerate(chain):
        if route["host"] == "native":
            break
        command = build_command(route, role, cwd, run_dir)
        executable = _executable(command["argv"][0])
        attempt: dict[str, Any] = {"route": route, "argv": command["argv"]}
        attempts.append(attempt)
        if dry_run:
            continue
        if not executable:
            attempt["error"] = f"{command['argv'][0]} is not installed"
            continue
        log = run_dir / f"output-{index}.log"
        started = time.monotonic()
        exit_code, timed_out = _run_process([executable, *command["argv"][1:]], command["env"], cwd, prompt, log, timeout)
        attempt.update(exitCode=exit_code, timedOut=timed_out, elapsedSeconds=round(time.monotonic() - started, 1))
        if timed_out or exit_code:
            attempt["error"] = "timed out" if timed_out else f"exit code {exit_code}"
            parsed = parse_output(route["host"], log.read_text(encoding="utf-8", errors="replace"))
            detail = parsed["error"] or parsed["report"]
            if detail and not timed_out:
                attempt["error"] += ": " + detail.strip().splitlines()[-1][:300]
            continue
        parsed = parse_output(route["host"], log.read_text(encoding="utf-8", errors="replace"),
                              run_dir / "report.md" if route["host"] == "codex" else None)
        confirmed = model_confirmed(route.get("model"), parsed["observedModels"])
        result = {"ok": confirmed is not False and bool(parsed["report"]),
                  "status": "model-mismatch" if confirmed is False else ("completed" if parsed["report"] else "empty-report"),
                  "role": role, "host": route["host"], "requestedModel": route.get("model"),
                  "observedModels": parsed["observedModels"], "modelConfirmed": confirmed,
                  "fallbackUsed": index > 0, "verdict": parsed["verdict"], "usage": parsed["usage"],
                  "elapsedSeconds": attempt["elapsedSeconds"], "report": parsed["report"], "error": parsed["error"]}
        # A wrong model is a routing error, not a transport failure: never silently fall back.
        break
    if dry_run:
        result = {"ok": True, "status": "dry-run", "role": role}
    elif not result:
        result = {"ok": False, "status": "failed", "role": role,
                  "message": "; ".join(item.get("error", "") for item in attempts) or "no runnable route"}
    result["runDir"] = str(run_dir)
    result["attempts"] = attempts
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if result.get("report"):
        (run_dir / "report.md").write_text(result["report"] + "\n", encoding="utf-8")
    return result
