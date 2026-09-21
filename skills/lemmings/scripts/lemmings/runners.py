"""Small, manager-directed adapters; no model selection, retry, or acceptance policy."""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import subprocess
import time
import uuid
from pathlib import Path
from typing import Mapping
from .contracts import validate_agent_result
from .discovery import _home, _trusted_bundle, _is_documented_go_endpoint, _paths, _read_safe
from .workspace import _registry_lock, _save_registry, git_common_dir, load_registry

MAX_OUTPUT = 16 * 1024 * 1024
MAX_RESULT = 1024 * 1024
ARRAYS = ("changedPaths", "acceptanceEvidence", "validationEvidence", "findings", "blockers", "remainingRisks")
CODEX_DISABLED = ("multi_agent", "multi_agent_v2", "plugins", "hooks", "apps", "browser_use", "browser_use_external", "computer_use", "image_generation", "in_app_browser", "in_app_local_automation", "remote_plugin", "skill_mcp_dependency_install", "skill_search")


def _result(invocation, status, reason=None):
    result = {"schemaVersion": 5, "invocationId": invocation["invocationId"], "attempt": invocation["attempt"], "status": status, **{key: [] for key in ARRAYS}}
    if reason:
        result["blockers"] = [reason]
    return result


def _prompt(invocation):
    value = ("Execute only this saved Lemmings invocation. Read its referenced files within the stated budget. "
             "Do not delegate, resume history, change providers, publish, or use unrelated tools. "
             "Return only an AgentResult v5 JSON object with matching invocationId/attempt, status succeeded|failed|blocked|cancelled, "
             "candidateHead when applicable, and arrays changedPaths, acceptanceEvidence, validationEvidence, findings, blockers, remainingRisks. "
             "For reviewer/explorer return changedPaths=[]. Keep evidence compact. Never claim trusted tool usage in AgentResult; host-v1 accounting accepts only a separate host receipt. "
             "Follow reviewSpec full or delta bindings when the role is reviewer.\n" + json.dumps(invocation, separators=(",", ":")))
    if len(value.encode()) > 16384:
        raise ValueError("runner dispatch exceeds16KiB")
    return value


def _toml(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(k) + " = " + _toml(v) for k, v in value.items()) + " }"
    return json.dumps(value)


def _codex_mcp_disable_args(repo: Path, home: Path, profile: str | None) -> list[str]:
    paths = [home / ".codex" / "config.toml", repo / ".codex" / "config.toml"]
    if profile:
        paths.insert(1, home / ".codex" / f"{profile}.config.toml")
    names: set[str] = set()
    for path in paths:
        value = _read_safe(path)
        servers = value.get("mcp_servers") if isinstance(value, dict) else None
        if isinstance(servers, dict):
            names.update(str(name) for name in servers)
    result: list[str] = []
    for name in sorted(names):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("unsupported-capability: configured MCP server name cannot be safely disabled")
        result += ["-c", f"mcp_servers.{name}.enabled=false"]
    return result


def _agent_result_schema(invocation):
    arrays = {name: {"type": "array"} for name in ARRAYS}
    return {
        "type": "object",
        "properties": {
            "schemaVersion": {"const": 5},
            "invocationId": {"const": invocation["invocationId"]},
            "attempt": {"const": invocation["attempt"]},
            "status": {"enum": ["succeeded", "failed", "blocked", "cancelled"]},
            "candidateHead": {"type": ["string", "null"]},
            **arrays,
        },
        "required": ["schemaVersion", "invocationId", "attempt", "status", *ARRAYS],
        "additionalProperties": True,
    }


def _claude_permissions(repo: Path, invocation: Mapping, reader: bool) -> tuple[str, dict]:
    git_read = ["Bash(git status*)", "Bash(git diff*)", "Bash(git log*)",
                "Bash(git show*)", "Bash(git rev-parse*)"]
    allow = ["Read", "Glob", "Grep", *git_read]
    tools = ["Read", "Glob", "Grep", "Bash"]
    deny = ["Task", "Agent", "WebFetch", "WebSearch", "NotebookEdit", "mcp__*"]
    if reader:
        deny += ["Edit", "Write"]
    else:
        tools += ["Edit", "Write"]
        allow += ["Edit", "Write", "Bash(git add*)", "Bash(git commit*)"]
        allow += [f"Bash({command})" for command in invocation.get("validationCommands", [])]
    settings = {
        "fallbackModel": [],
        "switchModelsOnFlag": False,
        "permissions": {"allow": allow, "deny": deny},
    }
    return ",".join(tools), settings


def build_launch(repo, invocation, route) -> dict:
    repo = Path(repo).resolve()
    role = invocation.get("role")
    if role not in {"worker", "reviewer", "explorer", "manager"}:
        raise ValueError("unsupported invocation role")
    if invocation.get("schemaVersion") != 5 or not invocation.get("invocationId") or not isinstance(invocation.get("attempt"), int):
        raise ValueError("runner requires a saved schema-v5 invocation")
    executor = route.get("executor") or ("native" if route.get("hostId") == "native" else route.get("hostId"))
    reader = role in {"reviewer", "explorer"}
    launch = {"executor": executor, "argv": [], "env": {}, "stdin": _prompt(invocation), "readOnly": reader,
              "route": {key: route.get(key) for key in ("hostId", "providerId", "modelId", "variantId", "profileName", "configMode") if route.get(key) is not None},
              "capabilities": {"freshSession": True, "noDelegation": True, "roleRestrictions": True, "processTree": True, "toolCallLimit": "host-enforced", "deadline": "enforced"}}
    if executor == "native":
        launch["capabilities"] = {"requiresHostBridge": True}
        return launch
    protocol = route.get("protocol")
    if executor not in {"codex", "claude", "opencode"} or protocol not in {"responses", "chat-completions", "messages"}:
        raise ValueError("unsupported-capability: executor or protocol")
    if executor == "codex" and protocol != "responses":
        raise ValueError("unsupported-capability: Codex requires Responses protocol")
    if executor == "claude" and protocol != "messages":
        raise ValueError("unsupported-capability: Claude requires Messages protocol")
    provider, model = route.get("providerId"), route.get("modelId")
    if not provider or not model or any(str(value).startswith("-") for value in (provider, model)):
        raise ValueError("route requires explicit provider/model identity")
    mode = route.get("configMode") or "direct"

    if executor == "codex":
        argv = ["codex", "exec", "--ephemeral", "--ignore-rules",
                "--sandbox", "read-only" if reader else "workspace-write",
                "--cd", str(repo), "--model", str(model), "--json", "--color", "never"]
        config = {"approval_policy": "never", "web_search": "disabled",
                  "project_doc_max_bytes": 0, "sandbox_workspace_write.network_access": False,
                  "shell_environment_policy.inherit": "core"}
        if mode == "host":
            profile = route.get("profileName")
            if profile:
                argv += ["--profile", str(profile)]
        else:
            argv.append("--ignore-user-config")
            endpoint, secret = _trusted_bundle(repo, _home(None), route)
            if str(provider) != "openai" or endpoint and endpoint.rstrip("/") != "https://api.openai.com/v1":
                if not endpoint:
                    raise ValueError("unsupported-capability: selected Codex provider has no trusted endpoint")
                config.update({"model_provider": "lemmings-selected",
                               "model_providers.lemmings-selected": {
                                   "name": str(provider), "base_url": endpoint,
                                   "wire_api": "responses", "env_key": "LEMMINGS_PROVIDER_KEY"}})
                if secret:
                    launch["env"]["LEMMINGS_PROVIDER_KEY"] = secret
        if route.get("variantId"):
            config["model_reasoning_effort"] = route["variantId"]
        for name in CODEX_DISABLED:
            argv += ["--disable", name]
        argv += _codex_mcp_disable_args(repo, _home(None), route.get("profileName"))
        for key, value in config.items():
            argv += ["-c", key + "=" + _toml(value)]
        launch["argv"] = argv
        return launch

    if executor == "claude":
        if mode != "host":
            raise ValueError("unsupported-capability: Claude direct transport is not supported")
        tools, settings = _claude_permissions(repo, invocation, reader)
        argv = ["claude", "-p", "--safe-mode", "--no-session-persistence",
                "--setting-sources", "user,project,local",
                "--output-format", "json", "--json-schema",
                json.dumps(_agent_result_schema(invocation), separators=(",", ":")),
                "--model", str(model), "--max-turns",
                str(int((invocation.get("limits") or {}).get("maxTurns", 12))),
                "--permission-mode", "dontAsk", "--permission-prompts", "none",
                "--tools", tools, "--disallowedTools",
                "Task,Agent,WebFetch,WebSearch,NotebookEdit,mcp__*",
                "--settings", json.dumps(settings, separators=(",", ":"))]
        if route.get("variantId"):
            argv += ["--effort", str(route["variantId"])]
        launch["argv"] = argv
        launch["capabilities"]["structuredOutput"] = True
        launch["capabilities"]["usageEvidence"] = True
        return launch

    endpoint, secret = _trusted_bundle(repo, _home(None), route)
    headers = {"User-Agent": "lemmings-runner/4", "x-opencode-session": str(invocation["invocationId"])} if _is_documented_go_endpoint(endpoint) else {}
    permission = {"*": "deny", "read": {"*": "allow", "*.env": "deny", "*.env.*": "deny"}, "glob": "allow", "grep": "allow"}
    if not reader:
        edits = {"*": "deny", **{str((repo/path).resolve()).replace("\\", "/"): "allow" for path in invocation.get("ownedPaths", [])}}
        permission["edit"] = edits
        commands = ["git status*", "git diff*", "git log*", "git show*", "git rev-parse*", "git add*", "git commit*", *invocation.get("validationCommands", [])]
        permission["bash"] = {"*": "deny", **{str(command): "allow" for command in commands}}
    agent_name = "lemmings-" + hashlib.sha256(str(invocation["invocationId"]).encode()).hexdigest()[:16]
    config = {"share": "disabled", "permission": permission,
              "agent": {agent_name: {"mode": "primary", "description": "One saved Lemmings invocation",
                                     "steps": int((invocation.get("limits") or {}).get("maxTurns", 12)),
                                     "permission": permission}}}
    managed = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "opencode" if os.name == "nt" else Path("/Library/Application Support/opencode" if sys.platform == "darwin" else "/etc/opencode")
    if any((managed/name).exists() for name in ("opencode.json", "opencode.jsonc")):
        raise ValueError("unsupported-capability: managed OpenCode settings require host enforcement")
    mcp = {}
    paths = _paths(repo, _home(None))["opencode"]
    if os.environ.get("OPENCODE_CONFIG"):
        paths.append(Path(os.environ["OPENCODE_CONFIG"]))
    for path in paths:
        source = _read_safe(path)
        if isinstance(source, dict) and isinstance(source.get("mcp"), dict):
            mcp.update({name: {"enabled": False} for name in source["mcp"]})
    config["mcp"] = mcp
    launch["capabilities"]["filesystemSandbox"] = False
    if endpoint:
        driver = {"responses": "@ai-sdk/openai", "chat-completions": "@ai-sdk/openai-compatible", "messages": "@ai-sdk/anthropic"}[protocol]
        config["provider"] = {str(provider): {"npm": driver, "options": {
            "baseURL": endpoint, **({"apiKey": secret} if secret else {}),
            **({"headers": headers} if headers else {})},
            "models": {str(model): {"name": str(model)}}}}
    launch["env"] = {"OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
                     "OPENCODE_DISABLE_AUTOUPDATE": "true"}
    launch["argv"] = ["opencode", "run", "--pure", "--dir", str(repo), "--agent",
                      agent_name, "--format", "json", "--model", str(provider) + "/" + str(model)]
    if route.get("variantId"):
        launch["argv"] += ["--variant", str(route["variantId"])]
    return launch


class _WindowsTree:
    """Assign a suspended child to a Job before any child code can run."""
    def __init__(self, process):
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        api = self.api
        api.CreateJobObjectW.restype = w.HANDLE
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        api.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
        api.CloseHandle.argtypes = [w.HANDLE]
        api.CreateToolhelp32Snapshot.restype = w.HANDLE
        api.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        api.OpenThread.restype = w.HANDLE
        api.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.ResumeThread.argtypes = [w.HANDLE]
        api.ResumeThread.restype = w.DWORD
        class Thread(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("id", w.DWORD), ("owner", w.DWORD), ("base", w.LONG), ("delta", w.LONG), ("flags", w.DWORD)]
        api.Thread32First.argtypes = [w.HANDLE, ctypes.POINTER(Thread)]
        api.Thread32Next.argtypes = [w.HANDLE, ctypes.POINTER(Thread)]
        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle or not api.AssignProcessToJobObject(self.handle, int(process._handle)):
            self.close(); raise OSError("cannot establish Windows process-tree ownership")
        snapshot = api.CreateToolhelp32Snapshot(4, 0)
        thread = Thread(); thread.size = ctypes.sizeof(thread)
        found = False
        try:
            present = api.Thread32First(snapshot, ctypes.byref(thread))
            while present:
                if thread.owner == process.pid:
                    handle = api.OpenThread(2, False, thread.id)
                    if handle:
                        try:
                            found = api.ResumeThread(handle) != 0xFFFFFFFF
                        finally:
                            api.CloseHandle(handle)
                        break
                present = api.Thread32Next(snapshot, ctypes.byref(thread))
        finally:
            api.CloseHandle(snapshot)
        if not found:
            self.terminate(); self.close(); raise OSError("cannot resume owned child")
    def empty(self):
        # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: four LARGE_INTEGER + four DWORD.
        data = (ctypes.c_byte * 48)()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(data), 48, None):
            return False
        return ctypes.c_uint32.from_buffer(data, 40).value == 0
    def terminate(self):
        self.api.TerminateJobObject(self.handle, 1)
    def close(self):
        if getattr(self, "handle", None):
            self.api.CloseHandle(self.handle); self.handle = None


class _PosixTree:
    def __init__(self, process): self.pid = process.pid
    def empty(self):
        try: os.killpg(self.pid, 0); return False
        except ProcessLookupError: return True
        except PermissionError: return False
    def terminate(self):
        try: os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    def close(self): pass


def _registry_owner(repo, invocation, record=None, clear=False):
    with _registry_lock(repo):
        registry = load_registry(repo)
        entries = [entry for entry in registry["entries"] if Path(entry["path"]).resolve() == repo]
        if not entries:
            return
        if len(entries) != 1:
            raise ValueError("ambiguous managed workspace")
        entry = entries[0]; identity = invocation["invocationId"]
        if entry.get("taskId") != invocation["taskId"]:
            raise ValueError("workspace belongs to another task")
        if clear:
            if entry.get("activeInvocationId") != identity or any(p.get("invocationId") != identity for p in entry.get("processes", [])):
                raise ValueError("workspace process ownership changed")
            entry["activeInvocationId"] = None; entry["processes"] = []
        else:
            if record is None and (entry.get("activeInvocationId") or entry.get("processes")):
                raise ValueError("workspace already has recorded process ownership")
            if entry.get("leases") or entry.get("activeInvocationId") not in (None, identity) or any(p.get("invocationId") != identity for p in entry.get("processes", [])):
                raise ValueError("workspace has an active invocation, process or lease")
            entry["activeInvocationId"] = identity
            if record is not None: entry["processes"] = [record]
        _save_registry(repo, registry, registry["revision"])


def _checked_result(value, invocation, reader):
    if not isinstance(value, dict) or value.get("invocationId") != invocation["invocationId"] or value.get("attempt") != invocation["attempt"]:
        raise ValueError("runner result identity mismatch")
    checks = validate_agent_result(value, invocation, {"revision": invocation.get("taskRevision"), "baseSha": invocation.get("baseSha")})
    if not checks.ok or reader and value.get("changedPaths"):
        raise ValueError("runner result violates role/output contract")
    return value


def _agent_result_from_value(value):
    if isinstance(value, dict) and value.get("schemaVersion") == 5:
        return value
    if not isinstance(value, dict):
        return None
    for key in ("structured_output", "structuredOutput", "result"):
        candidate = value.get(key)
        if isinstance(candidate, dict) and candidate.get("schemaVersion") == 5:
            return candidate
        if isinstance(candidate, str):
            clean = candidate.strip()
            if clean.startswith(chr(96) * 3):
                clean = "\n".join(clean.splitlines()[1:-1])
            try:
                parsed = json.loads(clean)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("schemaVersion") == 5:
                return parsed
    return None


def _parse_output(path, executor):
    if path.stat().st_size > MAX_RESULT:
        raise ValueError("result exceeds bounded output contract")
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    clean = "\n".join(text.splitlines()[1:-1]) if text.startswith(chr(96) * 3) else text
    try:
        result = _agent_result_from_value(json.loads(clean))
        if result is not None:
            return result
    except ValueError:
        pass
    messages = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        result = _agent_result_from_value(event)
        if result is not None:
            return result
        if event.get("type") == "text":
            messages.append(str((event.get("part") or {}).get("text") or ""))
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            messages.append(str(item.get("text") or ""))
    for message in [*reversed(messages), "".join(messages)]:
        result = _agent_result_from_value({"result": message})
        if result is not None:
            return result
    raise ValueError("no final AgentResult JSON")


def _host_observation(path: Path, executor: str, requested: str) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    roots = []
    try:
        roots.append(json.loads(text))
    except ValueError:
        for line in text.splitlines():
            try:
                roots.append(json.loads(line))
            except ValueError:
                pass
    models: set[str] = set()
    usage = None
    def visit(value):
        nonlocal usage
        if isinstance(value, Mapping):
            model_usage = value.get("modelUsage")
            if isinstance(model_usage, Mapping):
                models.update(str(key) for key in model_usage if key)
                if usage is None:
                    usage = {str(key): child for key, child in model_usage.items()
                             if isinstance(child, Mapping)}
            for key in ("model", "model_id", "modelId"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate:
                    models.add(candidate)
            for key, child in value.items():
                if key not in {"result", "structured_output", "structuredOutput", "item", "part"}:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for root in roots:
        visit(root)
    exact = requested in models
    alias = requested in {"default", "sonnet", "opus", "haiku", "fable"} and any(requested in model.casefold() for model in models)
    return {"kind": "host-route", "executor": executor, "requestedModel": requested,
            "observedModels": sorted(models), "modelConfirmed": bool(exact or alias),
            "usageComplete": usage is not None}


def run_invocation(repo, invocation, route, *, native_bridge=None, cancelled=None) -> dict:
    repo = Path(repo).resolve()
    try: launch = build_launch(repo, invocation, route)
    except ValueError as error: return _result(invocation, "blocked", str(error))
    is_cancelled = cancelled or (lambda: False)
    if launch["executor"] == "native" and native_bridge is None:
        result = _result(invocation, "blocked", "dispatch-required")
        result["dispatch"] = {"invocationId": invocation["invocationId"], "role": invocation["role"]}
        return result
    if launch["executor"] == "native":
        capabilities = getattr(native_bridge, "capabilities", {})
        required = {"freshSession", "noDelegation", "roleRestrictions", "cancellation"}
        if not all(capabilities.get(key) is True for key in required) or launch["readOnly"] and capabilities.get("readOnly") is not True:
            return _result(invocation, "blocked", "unsupported-capability: native bridge")
    elif shutil.which(launch["argv"][0]) is None:
        return _result(invocation, "blocked", "executor-not-installed")
    common = git_common_dir(repo) / "lemmings/runs"
    common.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(repo).encode()).hexdigest()[:24]
    lock = common / (key + ".lock")
    record = {"invocationId": invocation["invocationId"], "pid": os.getpid(), "startToken": uuid.uuid4().hex, "status": "reserved"}
    try: descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError: return _result(invocation, "blocked", "workspace-run-locked; inspect prior owner")
    with os.fdopen(descriptor, "w", encoding="utf-8") as output: json.dump(record, output)
    confirmed = False; reserved = False; process = tree = None; result = {}
    artifact = common / (key + "-" + record["startToken"] + ".log")
    final_path = artifact.with_suffix(".result.json")
    try:
        _registry_owner(repo, invocation); reserved = True
        if is_cancelled():
            confirmed = True; result = _result(invocation, "cancelled"); return result
        if launch["executor"] == "native":
            value = native_bridge(repo, invocation, route, cancelled=is_cancelled)
            confirmed = True
            result = _checked_result(value, invocation, launch["readOnly"])
            return result
        argv = list(launch["argv"])
        if launch["executor"] == "codex": argv += ["--output-last-message", str(final_path), "-"]
        env = os.environ.copy(); env.update(launch["env"])
        deadline = time.monotonic() + float((invocation.get("limits") or {}).get("deadlineSeconds", 1200))
        with artifact.open("wb") as output:
            kwargs = {"creationflags": 0x00000004 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen(argv, cwd=repo, env=env, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, **kwargs)
            tree = _WindowsTree(process) if os.name == "nt" else _PosixTree(process)
            record.update(pid=process.pid, status="running")
            lock.write_text(json.dumps(record), encoding="utf-8")
            _registry_owner(repo, invocation, record)
            payload = launch["stdin"].encode()
            status = None
            while True:
                if is_cancelled(): status = "cancelled"
                elif time.monotonic() >= deadline: status = "failed"
                elif artifact.stat().st_size > MAX_OUTPUT: status = "failed"
                if status: tree.terminate(); break
                try:
                    process.communicate(input=payload, timeout=0.1); break
                except subprocess.TimeoutExpired: payload = None
            if process.poll() is None:
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: pass
            if not tree.empty(): tree.terminate()
            end = time.monotonic() + 5
            while not tree.empty() and time.monotonic() < end: time.sleep(0.05)
            confirmed = process.poll() is not None and tree.empty()
        if not confirmed: result = _result(invocation, "blocked", "process-tree-termination-unconfirmed")
        elif status: result = _result(invocation, status, "cancelled-or-limit-reached")
        elif process.returncode: result = _result(invocation, "failed", "executor-exit:" + str(process.returncode))
        else:
            output_path = final_path if final_path.exists() else artifact
            parsed = _parse_output(output_path, launch["executor"])
            observation = _host_observation(artifact, launch["executor"], str(route.get("modelId") or ""))
            if route.get("configMode") == "host" and observation["observedModels"] and not observation["modelConfirmed"]:
                raise ValueError("runner result model identity mismatch")
            result = _checked_result(parsed, invocation, launch["readOnly"])
            result["validationEvidence"].append(observation)
        result["validationEvidence"].append({"artifact": "git-common-dir:lemmings/runs/" + artifact.name, "totalBytes": artifact.stat().st_size, "exitCode": process.returncode})
        return result
    except (OSError, ValueError, TypeError) as error:
        if process is None:
            confirmed = confirmed or launch["executor"] != "native" or not reserved
        else:
            if tree is not None: tree.terminate()
            else: process.kill()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: pass
            confirmed = process.poll() is not None and (tree is None or tree.empty())
        # Do not echo provider errors, environment contents or raw model logs.
        result = _result(invocation, "failed" if confirmed else "blocked", "runner-error:" + type(error).__name__)
        if artifact.exists(): result["validationEvidence"] = [{"artifact": "git-common-dir:lemmings/runs/" + artifact.name, "totalBytes": artifact.stat().st_size}]
        return result
    finally:
        if tree is not None: tree.close()
        if confirmed:
            try:
                if reserved: _registry_owner(repo, invocation, clear=True)
            except (ValueError, OSError):
                result.clear()
                result.update(_result(invocation, "blocked", "registry-ownership-unconfirmed; run lock retained"))
            else:
                lock.unlink(missing_ok=True)
