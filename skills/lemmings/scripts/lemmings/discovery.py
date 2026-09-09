
"""Secret-safe, metadata-only provider discovery for Lemmings v4."""
from __future__ import annotations
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 4
PROTOCOLS = {"responses", "chat-completions", "messages", "unknown"}
EXECUTORS = {"native", "codex", "opencode"}
ROUTE_KEYS = ("hostId", "providerId", "modelId", "variantId", "executor", "profileName", "protocol", "configured", "catalogued", "compatible", "authConfigured", "probed", "quotaGroup", "source")
SAFE_SOURCES = {"codex-config", "codex-profile", "codex-auth", "codex-cache", "opencode-config", "opencode-auth", "host-catalog", "state-inventory", "manual", "project-manual", "personal-manual", "generated", "unknown"}
SECRET_KEYS = {"experimental_bearer_token", "bearer_token", "access", "access_token", "api_key", "apikey", "auth", "authorization", "client_secret", "credential", "credentials", "key", "password", "private_key", "refresh", "refresh_token", "secret", "token"}

# https://opencode.ai/docs/go/ documents these endpoint protocols by exact
# model ID. The public /zen/go/v1/models response intentionally carries only
# inventory metadata, so a model-name family or provider-level setting is not
# evidence of a protocol. Verified 2026-09-10.
GO_ENDPOINT = "https://opencode.ai/zen/go/v1"
GO_CATALOG_URL = f"{GO_ENDPOINT}/models"
_GO_PROVIDER_ALIASES = {"opencode", "opencode-go", "go"}
_GO_HOST_ALIASES = {"opencode", "opencode-go", "go", "open-code"}
_GO_CATALOG_HOSTS = {"opencode-go", "go"}
_GO_PROTOCOLS = {
    "grok-4.6": "responses", "gpt-5.6-luna": "responses",
    "muse-spark-1.3-contributor": "responses", "muse-spark-1.2-contributor": "responses",
    "glm-5.3-flash": "chat-completions", "glm-5.3": "chat-completions",
    "glm-5.2": "chat-completions", "glm-5.1": "chat-completions",
    "kimi-k3": "chat-completions", "kimi-k2.7-code": "chat-completions",
    "kimi-k2.6": "chat-completions", "longcat-2.0": "chat-completions",
    "deepseek-v4-pro": "chat-completions", "deepseek-v4-flash": "chat-completions",
    "deepseek-v4-flash-vision-exp": "chat-completions", "mimo-v2.5": "chat-completions",
    "mimo-v2.5-pro": "chat-completions", "hy4-preview": "chat-completions",
    "hy3": "chat-completions", "omen-alpha": "chat-completions",
    "minimax-m3": "messages", "minimax-m2.7": "messages", "minimax-m2.5": "messages",
    "qwen3.8-max": "messages", "qwen3.8-flash": "messages", "qwen3.7-max": "messages",
    "qwen3.7-plus": "messages", "qwen3.6-plus": "messages",
}

_STATE_LOCKS: dict[str, tuple[int, int]] = {}


@contextmanager
def state_lock(home: Path | str | None = None):
    """Acquire the generated-state lock before reading or mutating state."""
    target = _home(home) / ".lemmings" / "state.json.lock"
    key = str(target)
    owner = threading.get_ident()
    current = _STATE_LOCKS.get(key)
    if current and current[0] == owner:
        _STATE_LOCKS[key] = (owner, current[1] + 1)
        try:
            yield
        finally:
            depth = _STATE_LOCKS.get(key, (owner, 1))[1] - 1
            if depth:
                _STATE_LOCKS[key] = (owner, depth)
            else:
                _STATE_LOCKS.pop(key, None)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise ValueError("generated profile state is locked") from error
    else:
        os.close(descriptor)
    _STATE_LOCKS[key] = (owner, 1)
    try:
        yield
    finally:
        _STATE_LOCKS.pop(key, None)
        target.unlink(missing_ok=True)


class _TomlUnsupported(RuntimeError):
    pass

def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()

def _home(value: Path | str | None) -> Path:
    return (Path(value).expanduser() if value is not None else Path.home()).resolve()

def _repo(value: Path | str) -> Path:
    return Path(value).expanduser().resolve()

def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _diag(code: str, message: str, source: str | None = None) -> dict[str, str]:
    result = {"code": code, "message": message}
    if source:
        result["source"] = source
    return result

def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None

def _identifier(value: Any) -> str | None:
    value = _text(value)
    if not value or len(value) > 200 or "://" in value or any(char in value for char in "?&#=\r\n"):
        return None
    return value

def _jsonc(text: str) -> Any:
    output: list[str] = []
    quoted = escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
            continue
        if char == '"':
            quoted = True
            output.append(char)
            index += 1
        elif text.startswith("//", index):
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
        elif text.startswith("/*", index):
            index += 2
            while index + 1 < len(text) and text[index:index + 2] != "*/":
                index += 1
            index = min(index + 2, len(text))
        else:
            output.append(char)
            index += 1
    source = "".join(output)
    output = []
    quoted = escaped = False
    for index, char in enumerate(source):
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
            output.append(char)
        elif char == "," and re.compile(r"\s*[\]}]").match(source, index + 1):
            pass
        else:
            output.append(char)
    return json.loads("".join(output))

def _read(path: Path) -> Any | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None
    try:
        if path.suffix.lower() in {".json", ".jsonc"}:
            try:
                return json.loads(text)
            except ValueError:
                return _jsonc(text)
        try:
            import tomllib
        except (ImportError, AttributeError) as error:
            raise _TomlUnsupported("TOML discovery requires Python 3.11 or tomllib") from error
        return tomllib.loads(text)
    except (TypeError, ValueError):
        return None

def _read_safe(path: Path, diagnostics: list[dict[str, str]] | None = None) -> Any | None:
    try:
        return _read(path)
    except _TomlUnsupported:
        if diagnostics is not None:
            diagnostics.append(_diag("toml-unsupported", "TOML discovery is unavailable on this Python runtime", "codex-config"))
        return None


def _unique(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()).casefold() if os.name == "nt" else str(path.resolve())
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result

def _paths(repo: Path | None, home: Path) -> dict[str, list[Path]]:
    repo_codex = [] if repo is None else [repo / ".codex" / name for name in ("config.toml", "config.json", "config.jsonc")]
    repo_opencode = [] if repo is None else [repo / name for name in ("opencode.json", "opencode.jsonc")] + [repo / ".opencode" / name for name in ("opencode.json", "opencode.jsonc")]
    codex_profiles = sorted((home / ".codex").glob("*.config.toml"))[:128]
    if repo is not None:
        codex_profiles = sorted((repo / ".codex").glob("*.config.toml"))[:128] + codex_profiles
    repo_codex_auth = [] if repo is None else [repo / ".codex" / "auth.json"]
    repo_opencode_auth = [] if repo is None else [repo / ".opencode" / "auth.json"]
    return {
        "codex": _unique(repo_codex + [home / ".codex" / name for name in ("config.toml", "config.json", "config.jsonc", "profiles.toml", "profiles.json", "profiles.jsonc")] + codex_profiles),
        "opencode": _unique(repo_opencode + [home / ".config" / "opencode" / name for name in ("opencode.json", "opencode.jsonc")] + [home / ".opencode" / name for name in ("opencode.json", "opencode.jsonc", "config.json", "config.jsonc")]),
        "codex-auth": _unique(repo_codex_auth + [home / ".codex" / "auth.json"]),
        "codex-cache": _unique([home / ".codex" / "models_cache.json"]),
        "opencode-auth": _unique(repo_opencode_auth + [home / ".config" / "opencode" / "auth.json", home / ".local" / "share" / "opencode" / "auth.json", home / ".opencode" / "auth.json"]),
        "opencode-cache": _unique([home / ".cache" / "opencode" / "models.json"]),
    }

def _protocol(value: Any) -> str:
    value = (_text(value) or "").lower().replace("_", "-")
    if value in {"responses", "response", "openai-responses"}:
        return "responses"
    if value in {"chat", "chat-completion", "chat-completions", "completions", "openai-chat"}:
        return "chat-completions"
    if value in {"message", "messages", "anthropic-messages"}:
        return "messages"
    return "unknown"


def _route_name(value: Any) -> str:
    return (_text(value) or "").lower().replace("_", "-")


def _is_go_identity(host: Any, provider: Any) -> bool:
    host_name, provider_name = _route_name(host), _route_name(provider)
    return host_name in _GO_HOST_ALIASES and provider_name in _GO_PROVIDER_ALIASES


def _is_documented_go_endpoint(endpoint: Any) -> bool:
    value = _text(endpoint)
    if not value:
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        return parsed.scheme == "https" and parsed.hostname == "opencode.ai" and parsed.port in (None, 443) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path.rstrip("/") == "/zen/go/v1"
    except ValueError:
        return False


def _route_protocol(host: Any, provider: Any, model: Any, value: Any, *, documented_go: bool = False, catalog_go: bool = False) -> str:
    if catalog_go or documented_go:
        return _GO_PROTOCOLS.get(_text(model) or "", "unknown")
    return _protocol(value)


def _config_protocol(host: Any, provider: Any, model: Any, metadata: Mapping[str, Any]) -> str:
    raw = metadata.get("protocol", metadata.get("api", metadata.get("wireApi", "unknown")))
    return _route_protocol(host, provider, model, raw, documented_go=_is_documented_go_endpoint(_section_endpoint(metadata)))

def _split_model(value: Any, provider: Any = None) -> tuple[str | None, str | None]:
    model, provider = _identifier(value), _identifier(provider)
    if model and not provider and "/" in model:
        provider, model = model.split("/", 1)
    return provider, model

def _executor(host: str, protocol: str, explicit: Any = None) -> str:
    explicit = _text(explicit)
    if explicit in EXECUTORS:
        return explicit
    host = host.lower().replace("_", "-")
    if host in {"codex", "openai-codex"}:
        return "codex"
    if host.startswith("opencode") or host in {"go", "open-code"}:
        return "opencode"
    return "native"

def _compatible(executor: str, protocol: str) -> bool:
    if protocol == "unknown":
        return False
    if executor == "codex":
        return protocol == "responses"
    if executor == "opencode":
        return protocol in {"responses", "chat-completions", "messages"}
    return protocol in PROTOCOLS

def normalize_route(value: Mapping[str, Any], *, defaults: Mapping[str, Any] | None = None) -> dict[str, Any]:
    defaults = defaults or {}
    host = _identifier(value.get("hostId")) or _identifier(defaults.get("hostId"))
    provider = _identifier(value.get("providerId")) or _identifier(defaults.get("providerId"))
    model = _identifier(value.get("modelId")) or _identifier(defaults.get("modelId"))
    if not host or not provider or not model:
        raise ValueError("route requires hostId, providerId, and modelId")
    protocol = _protocol(value.get("protocol", defaults.get("protocol", "unknown")))
    executor = _executor(host, protocol, value.get("executor", defaults.get("executor")))
    source = _text(value.get("source")) or _text(defaults.get("source")) or "unknown"
    result: dict[str, Any] = {"hostId": host, "providerId": provider, "modelId": model, "executor": executor, "protocol": protocol, "configured": bool(value.get("configured", defaults.get("configured", False))), "catalogued": bool(value.get("catalogued", defaults.get("catalogued", False))), "compatible": bool(value.get("compatible", defaults.get("compatible", _compatible(executor, protocol)))), "authConfigured": bool(value.get("authConfigured", defaults.get("authConfigured", False))), "probed": bool(value.get("probed", defaults.get("probed", False))), "source": source if source in SAFE_SOURCES else "unknown"}
    for key in ("variantId", "profileName", "quotaGroup"):
        optional = _identifier(value.get(key)) or _identifier(defaults.get(key))
        if optional:
            result[key] = optional
    return result

def _route_key(route: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return tuple(str(route.get(key, "")) for key in ("hostId", "providerId", "modelId", "variantId", "profileName"))  # type: ignore[return-value]

def _auth_present(value: Any) -> set[str]:
    found: set[str] = set()
    secret_names = {item.replace("_", "") for item in SECRET_KEYS}
    def visit(node: Any, hint: str | None = None) -> None:
        if isinstance(node, Mapping):
            has_secret = False
            for key, child in node.items():
                lowered = str(key).lower().replace("-", "_")
                if (lowered in SECRET_KEYS or lowered.replace("_", "") in secret_names) and child not in (None, "", [], {}):
                    has_secret = True
                if isinstance(child, (Mapping, list)):
                    visit(child, str(key))
            if has_secret and hint and hint.lower().replace("-", "_") not in SECRET_KEYS and _identifier(hint):
                found.add(_identifier(hint) or "")
        elif isinstance(node, list):
            for child in node:
                visit(child, hint)
    visit(value)
    return found

def _provider_auth(value: Mapping[str, Any]) -> bool:
    return bool(_auth_present(value)) or any(key in value and value[key] not in (None, "", [], {}) for key in ("apiKey", "api_key", "token", "key", "auth", "credentials", "oauth"))

def _models(value: Any) -> list[tuple[str, Mapping[str, Any] | None]]:
    if isinstance(value, str):
        model = _identifier(value)
        return [(model, None)] if model else []
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str) and _identifier(item):
                result.append((_identifier(item) or "", None))
            elif isinstance(item, Mapping):
                model = _identifier(item.get("modelId")) or _identifier(item.get("id")) or _identifier(item.get("name"))
                if model:
                    result.append((model, item))
        return result
    if isinstance(value, Mapping):
        result = []
        for key, item in value.items():
            model = _identifier(item.get("modelId")) if isinstance(item, Mapping) else None
            model = model or _identifier(key)
            if model:
                result.append((model, item if isinstance(item, Mapping) else None))
        return result
    return []

def _add(routes: dict[tuple[str, str, str, str, str], dict[str, Any]], value: Mapping[str, Any]) -> None:
    try:
        route = normalize_route(value)
    except ValueError:
        return
    key = _route_key(route)
    previous = routes.get(key)
    if previous is None:
        routes[key] = route
        return
    for field in ROUTE_KEYS:
        if field not in route:
            continue
        if field in {"configured", "catalogued", "compatible", "authConfigured", "probed"}:
            previous[field] = bool(previous.get(field)) or bool(route.get(field))
        elif previous.get(field) in (None, "", "unknown") and route.get(field) not in (None, ""):
            previous[field] = route[field]
    if previous.get("source") == "state-inventory" and previous.get("configured"):
        previous["source"] = route.get("source", previous["source"])
    previous["compatible"] = _compatible(str(previous["executor"]), str(previous["protocol"]))

def _codex(data: Mapping[str, Any], source: str, routes: dict[tuple[str, str, str, str, str], dict[str, Any]], diagnostics: list[dict[str, str]]) -> None:
    providers = data.get("model_providers") or data.get("modelProviders") or {}
    providers = providers if isinstance(providers, Mapping) else {}
    default_provider = data.get("model_provider") or data.get("modelProvider") or data.get("providerId")
    protocol = data.get("wire_api") or data.get("wireApi") or data.get("protocol")
    def add(model: Any, provider: Any, metadata: Mapping[str, Any] | None = None, profile: str | None = None) -> None:
        provider_id, model_id = _split_model(model, provider)
        if not provider_id or not model_id:
            if model is not None:
                diagnostics.append(_diag("route-missing-provider", "configured model has no explicit provider", source))
            return
        metadata = {**(dict(providers.get(provider_id)) if isinstance(providers.get(provider_id), Mapping) else {}), **dict(metadata or {})}
        wire = metadata.get("wire_api", metadata.get("wireApi", metadata.get("protocol", protocol)))
        wire = _route_protocol("codex", provider_id, model_id, wire, documented_go=_is_documented_go_endpoint(_section_endpoint(metadata)))
        _add(routes, {"hostId": "codex", "providerId": provider_id, "modelId": model_id, "profileName": profile, "protocol": wire, "executor": "codex", "configured": True, "authConfigured": _provider_auth(metadata), "source": source, "quotaGroup": metadata.get("quotaGroup")})
    add(data.get("model") or data.get("modelId"), default_provider)
    for provider, config in providers.items():
        if not isinstance(config, Mapping):
            continue
        for model, metadata in _models(config.get("models")):
            add(model, provider, {**dict(config), **(dict(metadata) if metadata else {})})
    profiles = data.get("profiles")
    if isinstance(profiles, Mapping):
        for name, profile in profiles.items():
            if isinstance(profile, Mapping):
                add(profile.get("model") or profile.get("modelId"), profile.get("model_provider") or profile.get("modelProvider") or profile.get("providerId") or default_provider, profile, str(name))


def _codex_cache(data: Mapping[str, Any], routes: dict[tuple[str, str, str, str, str], dict[str, Any]], diagnostics: list[dict[str, str]]) -> None:
    models = data.get("models")
    if not isinstance(models, list):
        return
    for metadata in models:
        if not isinstance(metadata, Mapping):
            continue
        slug = _identifier(metadata.get("slug"))
        visibility = metadata.get("visibility")
        visible = visibility == "list" or (isinstance(visibility, list) and "list" in visibility)
        if not slug or not visible:
            diagnostics.append(_diag("codex-cache-unavailable", "Codex cache model is not available for selection", "codex-cache"))
            continue
        levels = metadata.get("supported_reasoning_levels")
        variants = [_identifier(item.get("effort") or item.get("reasoning_effort") or item.get("level")) if isinstance(item, Mapping) else _identifier(item) for item in levels] if isinstance(levels, list) else []
        for variant in [None, *(item for item in variants if item)]:
            route: dict[str, Any] = {
                "hostId": "codex", "providerId": "openai", "modelId": slug,
                "executor": "codex", "protocol": "responses", "configured": False,
                "catalogued": True, "compatible": True, "authConfigured": False,
                "probed": False, "source": "codex-cache",
            }
            if variant:
                route["variantId"] = variant
            _add(routes, route)

def _opencode(data: Mapping[str, Any], routes: dict[tuple[str, str, str, str, str], dict[str, Any]]) -> None:
    providers = data.get("provider") or data.get("providers") or {}
    providers = providers if isinstance(providers, Mapping) else {}
    def add(provider: Any, model: Any, metadata: Mapping[str, Any] | None = None, profile: str | None = None) -> None:
        provider_id, model_id = _split_model(model, provider)
        if not provider_id or not model_id:
            return
        metadata = metadata or {}
        _add(routes, {"hostId": "opencode", "providerId": provider_id, "modelId": model_id, "profileName": profile, "protocol": _config_protocol("opencode", provider_id, model_id, metadata), "executor": "opencode", "configured": True, "authConfigured": _provider_auth(metadata), "source": "opencode-config", "quotaGroup": metadata.get("quotaGroup")})
    top_provider, top_model = _split_model(data.get("model") or data.get("modelId"))
    if top_provider and top_model:
        config = providers.get(top_provider)
        add(top_provider, top_model, config if isinstance(config, Mapping) else None)
    for provider, config in providers.items():
        if not isinstance(config, Mapping):
            continue
        protocol = config.get("protocol", config.get("api", config.get("wireApi")))
        for model, metadata in _models(config.get("models")):
            merged = {**dict(config), **(dict(metadata) if metadata else {})}
            if protocol is not None:
                merged.setdefault("protocol", protocol)
            add(provider, model, merged)
        for key in ("model", "defaultModel", "default_model"):
            if config.get(key):
                add(provider, config[key], {**dict(config), "protocol": protocol})
    agents = data.get("agent") or data.get("agents")
    if isinstance(agents, Mapping):
        for name, agent in agents.items():
            if isinstance(agent, Mapping) and agent.get("model"):
                add(None, agent["model"], agent, str(name))

def _catalog_records(host: str | None, value: Any) -> list[tuple[str | None, Mapping[str, Any]]]:
    if isinstance(value, list):
        return [(host or _text(item.get("hostId")), item) for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    result: list[tuple[str | None, Mapping[str, Any]]] = []
    for model_id, metadata in value.items():
        identifier = _identifier(model_id)
        if identifier:
            item = dict(metadata) if isinstance(metadata, Mapping) else {}
            item["modelId"] = identifier
            result.append((host or _text(item.get("hostId")), item))
    return result


def _catalog_items(value: Any) -> list[tuple[str | None, Mapping[str, Any]]]:
    if isinstance(value, list):
        return [(_text(item.get("hostId")), item) for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    providers = value.get("provider") or value.get("providers")
    if isinstance(providers, Mapping) and not any(key in value for key in ("data", "models", "id", "model", "modelId")):
        value = providers
    if "data" in value or "models" in value:
        host = _text(value.get("hostId")) or _text(value.get("host"))
        return _catalog_records(host, value.get("data") if "data" in value else value.get("models"))
    result: list[tuple[str | None, Mapping[str, Any]]] = []
    for host, item in value.items():
        if host in {"schemaVersion", "scannedAt", "providers", "routes", "diagnostics", "status", "provider"}:
            continue
        if isinstance(item, Mapping):
            if "models" in item or "data" in item:
                result.extend((parent, {**model, "_catalogEndpoint": item.get("api")}) for parent, model in _catalog_records(str(host), item.get("models") if "models" in item else item.get("data")))
            elif item.get("id") or item.get("model") or item.get("modelId"):
                result.append((str(host), item))
        elif isinstance(item, list):
            result.extend((str(host), model) for model in item if isinstance(model, Mapping))
    return result


def _catalog(value: Any, diagnostics: list[dict[str, str]], *, go_catalog: str | None = None) -> tuple[list[dict[str, Any]], set[str]]:
    result = []
    hosts: set[str] = set()
    for parent, item in _catalog_items(value):
        host = _identifier(item.get("hostId")) or _identifier(parent) or "unknown"
        cache_provider = _identifier(parent) if go_catalog == "cache" and "_catalogEndpoint" in item else None
        if cache_provider:
            host = "opencode-go" if _route_name(cache_provider) in _GO_CATALOG_HOSTS else "opencode"
        hosts.add(host)
        raw = _identifier(item.get("modelId")) or _identifier(item.get("model")) or _identifier(item.get("id")) or _identifier(item.get("name"))
        provider = cache_provider or _identifier(item.get("providerId")) or _identifier(item.get("provider"))
        model = raw
        if raw and not provider and "/" in raw:
            provider, model = raw.split("/", 1)
        if not provider and _route_name(host) in _GO_CATALOG_HOSTS:
            provider = "opencode-go"
        if not provider or not model:
            continue
        catalog_go = go_catalog == "public" or (go_catalog == "cache" and _route_name(host) in _GO_CATALOG_HOSTS and _is_documented_go_endpoint(item.get("_catalogEndpoint")))
        protocol = _route_protocol(host, provider, model, item.get("protocol", item.get("wireApi", item.get("api"))), catalog_go=catalog_go)
        executor = "opencode" if _is_go_identity(host, provider) else _executor(host, protocol, item.get("executor"))
        variants = item.get("variants") if isinstance(item.get("variants"), list) else [item.get("variantId")]
        for variant in variants or [None]:
            route = {"hostId": host, "providerId": provider, "modelId": model, "protocol": protocol, "executor": executor, "configured": False, "catalogued": True, "compatible": _compatible(executor, protocol), "authConfigured": False, "probed": False, "source": "host-catalog"}
            if _identifier(variant):
                route["variantId"] = _identifier(variant)
            if _identifier(item.get("quotaGroup")):
                route["quotaGroup"] = _identifier(item.get("quotaGroup"))
            try:
                result.append(normalize_route(route))
            except ValueError:
                diagnostics.append(_diag("catalog-route-invalid", "explicit catalog route was ignored", "host-catalog"))
    return result, hosts

def _state_inventory(home: Path) -> Mapping[str, Any] | None:
    value = _read(home / ".lemmings" / "state.json")
    inventory = value.get("inventory") if isinstance(value, Mapping) else None
    return inventory if isinstance(inventory, Mapping) else None

def _merge_provider(providers: dict[str, dict[str, Any]], provider: str, source: str, auth: bool, status: str) -> None:
    current = providers.get(provider)
    if current is None:
        providers[provider] = {"providerId": provider, "source": source, "authConfigured": bool(auth), "catalogStatus": status}
        return
    current["authConfigured"] = bool(current.get("authConfigured")) or bool(auth)
    if current.get("source") == "state-inventory" and source != "state-inventory":
        current["source"] = source
    if status == "current" or current.get("catalogStatus") != "current":
        current["catalogStatus"] = status

def _alias(routes: Iterable[Mapping[str, Any]], known: Iterable[str]) -> list[dict[str, str]]:
    known = {str(item) for item in known}
    result = []
    for route in routes:
        for field in ("providerId", "hostId"):
            value = str(route.get(field, ""))
            dashed = value.replace("_", "-")
            if dashed != value and dashed in known:
                result.append(_diag("provider-alias-mismatch", f"provider alias {value} differs from {dashed}; edit the source explicitly"))
    return result

def _documented_go_configs(paths: Mapping[str, list[Path]]):
    for host, key, sections in (("codex", "codex", ("model_providers", "modelProviders")), ("opencode", "opencode", ("provider", "providers"))):
        for candidate in paths[key]:
            value = _read_safe(candidate)
            if not isinstance(value, Mapping):
                continue
            for section in sections:
                providers = value.get(section)
                if not isinstance(providers, Mapping):
                    continue
                for provider, config in providers.items():
                    if isinstance(config, Mapping) and _is_documented_go_endpoint(_section_endpoint(config)):
                        yield host, str(provider), config


def _configured_documented_go(paths: Mapping[str, list[Path]]) -> bool:
    return next(_documented_go_configs(paths), None) is not None


def _fetch_go_catalog(repo: Path | None, home: Path, diagnostics: list[dict[str, str]]) -> Any | None:
    headers = {"Accept": "application/json", "User-Agent": "lemmings-discovery/4", "x-opencode-session": digest({"schemaVersion": SCHEMA_VERSION, "purpose": "go-catalog"})}
    secret = _go_catalog_secret(repo, home)
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    request = urllib.request.Request(
        GO_CATALOG_URL,
        method="GET",
        headers=headers,
    )
    try:
        response = urllib.request.urlopen(request, timeout=3)
        try:
            body = response.read(2 * 1024 * 1024 + 1)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if len(body) > 2 * 1024 * 1024:
            diagnostics.append(_diag("go-catalog-too-large", "documented Go catalog exceeded the bounded response limit"))
            return None
        payload = json.loads(body.decode("utf-8"))
        data = payload.get("data") if isinstance(payload, Mapping) else payload
        if not isinstance(data, (list, Mapping)):
            diagnostics.append(_diag("go-catalog-invalid", "documented Go catalog returned an unsupported shape"))
            return None
        return {"hostId": "opencode-go", "data": data}
    except (OSError, urllib.error.URLError, ValueError, UnicodeError, TimeoutError):
        diagnostics.append(_diag("go-catalog-unavailable", "documented Go catalog could not be fetched"))
        return None


def scan_providers(repo: Path | str, *, offline: bool = False, home: Path | str | None = None, host_catalog: Mapping[str, Any] | list[Any] | None = None) -> dict[str, Any]:
    home_path = _home(home)
    with state_lock(home_path):
        return _scan_providers(repo, offline=offline, home=home, host_catalog=host_catalog)


def _scan_providers(repo: Path | str | None, *, offline: bool = False, home: Path | str | None = None, host_catalog: Mapping[str, Any] | list[Any] | None = None) -> dict[str, Any]:
    repo_path, home_path = (_repo(repo) if repo is not None else None), _home(home)
    paths = _paths(repo_path, home_path)
    diagnostics: list[dict[str, str]] = []
    routes: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    providers: dict[str, dict[str, Any]] = {}
    auth_ids: set[str] = set()
    for candidate in paths["codex-auth"] + paths["opencode-auth"]:
        value = _read_safe(candidate, diagnostics)
        if value is not None:
            if candidate in paths["codex-auth"] and isinstance(value, Mapping) and (value.get("OPENAI_API_KEY") or isinstance(value.get("tokens"), Mapping) and value["tokens"].get("access_token")):
                auth_ids.add("openai")
            else:
                auth_ids.update(_auth_present(value))
    codex_definitions = {}
    for candidate in reversed(paths["codex"]):
        value = _read_safe(candidate, diagnostics)
        if isinstance(value, Mapping) and not candidate.name.endswith(".config.toml"):
            definitions = value.get("model_providers") or value.get("modelProviders") or {}
            if isinstance(definitions, Mapping):
                codex_definitions.update(definitions)
    for candidate in paths["codex"]:
        value = _read_safe(candidate, diagnostics)
        if isinstance(value, Mapping):
            if candidate.name.endswith(".config.toml"):
                name = candidate.name[:-len(".config.toml")]
                definitions = {**codex_definitions, **(value.get("model_providers") or value.get("modelProviders") or {})}
                _codex({"model_providers": definitions, "profiles": {name: value}}, "codex-profile", routes, diagnostics)
            else:
                _codex(value, "codex-profile" if "profile" in candidate.name.lower() else "codex-config", routes, diagnostics)
            provider = _identifier(value.get("model_provider") or value.get("modelProvider") or value.get("providerId"))
            if provider and _provider_auth(value):
                auth_ids.add(provider)
    for candidate in paths["codex-cache"]:
        value = _read_safe(candidate, diagnostics)
        if isinstance(value, Mapping):
            _codex_cache(value, routes, diagnostics)
    for candidate in paths["opencode"]:
        value = _read_safe(candidate, diagnostics)
        if isinstance(value, Mapping):
            _opencode(value, routes)
            maps = value.get("provider") or value.get("providers") or {}
            if isinstance(maps, Mapping):
                for provider, config in maps.items():
                    provider_id = _identifier(provider)
                    if provider_id and isinstance(config, Mapping) and _provider_auth(config):
                        auth_ids.add(provider_id)
    catalog_routes: list[dict[str, Any]] = []
    catalog_hosts: set[str] = set()
    catalog_loaded = False
    if host_catalog is not None:
        catalog_routes, catalog_hosts = _catalog(host_catalog, diagnostics)
        catalog_loaded = bool(catalog_routes)
    for candidate in paths["opencode-cache"]:
        cached = _read_safe(candidate, diagnostics)
        if cached is not None:
            local_routes, local_hosts = _catalog(cached, diagnostics, go_catalog="cache")
            connected = {str(route["providerId"]) for route in routes.values() if route.get("configured")} | auth_ids
            if connected:
                # The shared cache contains every vendor, not account access.
                # Keep connected providers and visible spelling mismatches only.
                names = {_route_name(provider) for provider in connected}
                local_routes = [route for route in local_routes if _route_name(route["providerId"]) in names]
                local_hosts = {route["hostId"] for route in local_routes}
            catalog_routes.extend(local_routes)
            catalog_hosts.update(local_hosts)
            catalog_loaded = catalog_loaded or bool(local_routes)
    go_attempted = not offline and host_catalog is None and _configured_documented_go(paths)
    if go_attempted:
        fetched = _fetch_go_catalog(repo_path, home_path, diagnostics)
        if fetched is not None:
            public_routes, public_hosts = _catalog(fetched, diagnostics, go_catalog="public")
            catalog_routes.extend(public_routes)
            catalog_hosts.update(public_hosts)
            catalog_loaded = catalog_loaded or bool(public_routes)
    for route in catalog_routes:
        if _is_go_identity(route.get("hostId"), route.get("providerId")):
            bundle = _configured_bundle(repo_path, home_path, route)
            if bundle is not None and not _is_documented_go_endpoint(bundle[0]):
                route = {**route, "protocol": "unknown", "compatible": False}
                diagnostics.append(_diag("provider-endpoint-override", "configured endpoint overrides the documented Go service; catalog route is not selectable"))
        _add(routes, route)
    with state_lock(home_path):
        inventory = _state_inventory(home_path)
    stale_providers = [item for item in (inventory or {}).get("providers", []) if isinstance(item, Mapping)]
    stale_routes = [item for item in (inventory or {}).get("routes", []) if isinstance(item, Mapping)]
    stale_added = False
    for item in stale_routes:
        try:
            key = _route_key(normalize_route(item))
        except ValueError:
            continue
        if key not in routes:
            _add(routes, {**dict(item), "source": "state-inventory", "probed": False})
            stale_added = True
    if stale_added:
        diagnostics.append(_diag("catalog-stale", "using the previous sanitized inventory after catalog unavailability", "state-inventory"))
    current_catalog = catalog_loaded or any(item.get("source") == "codex-cache" for item in routes.values())
    for route in routes.values():
        provider = str(route["providerId"])
        route["authConfigured"] = bool(route.get("authConfigured")) or provider in auth_ids
        route["compatible"] = _compatible(str(route["executor"]), str(route["protocol"]))
        status = "current" if current_catalog else ("stale" if route.get("source") == "state-inventory" else "unavailable")
        _merge_provider(providers, provider, str(route.get("source", "unknown")), bool(route.get("authConfigured")), status)
    for item in stale_providers:
        provider = _identifier(item.get("providerId"))
        if provider and provider not in providers:
            _merge_provider(providers, provider, "state-inventory", bool(item.get("authConfigured")), "stale")
    for provider in sorted(auth_ids):
        _merge_provider(providers, provider, "opencode-auth", True, "current" if current_catalog else "unavailable")
    if not current_catalog and not offline:
        diagnostics.append(_diag("catalog-unavailable", "no explicit provider catalog supplied; configured models remain uncatalogued"))
    if not routes and not providers:
        diagnostics.append(_diag("no-providers", "no supported provider metadata was found"))
    diagnostics.extend(_alias(routes.values(), [*providers, *catalog_hosts]))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "scannedAt": _now(),
        "providers": sorted(providers.values(), key=lambda item: item["providerId"]),
        "routes": sorted(routes.values(), key=_route_key),
        "diagnostics": sorted(diagnostics, key=lambda item: (item.get("code", ""), item.get("source", ""), item.get("message", ""))),
    }

def _same_provider(value: Any, provider: str) -> bool:
    return isinstance(value, str) and value.casefold() == provider.casefold()


def _sections(value: Any, route: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    provider, model = str(route["providerId"]), str(route["modelId"])
    result: list[Mapping[str, Any]] = []
    profiles = value.get("profiles")
    profile_name = route.get("profileName")
    if isinstance(profiles, Mapping) and isinstance(profile_name, str) and isinstance(profiles.get(profile_name), Mapping):
        selected_profile = profiles[profile_name]
        selected_provider = selected_profile.get("model_provider") or value.get("model_provider")
        if _same_provider(selected_provider, provider):
            result.append(selected_profile)
    for key in ("model_providers", "modelProviders", "provider", "providers"):
        providers = value.get(key)
        if not isinstance(providers, Mapping):
            continue
        for name, config in providers.items():
            if not _same_provider(name, provider) or not isinstance(config, Mapping):
                continue
            models = config.get("models")
            if isinstance(models, Mapping) and isinstance(models.get(model), Mapping):
                result.append(models[model])
            elif isinstance(models, list):
                result.extend(item for item in models if isinstance(item, Mapping) and _text(item.get("modelId") or item.get("id") or item.get("name")) == model)
            result.append(config)
    if _same_provider(value.get("model_provider") or value.get("modelProvider") or value.get("providerId"), provider):
        result.append(value)
    direct = value.get(provider)
    if isinstance(direct, Mapping):
        result.append(direct)
    return result


def _env_secret(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = re.fullmatch(r"(?:\$?\{)?env:([A-Za-z_][A-Za-z0-9_]*)(?:\})?", text) or re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text)
    return os.environ.get(match.group(1)) if match else text


def _section_secret(section: Mapping[str, Any]) -> str | None:
    environment_key = section.get("env_key") or section.get("envKey")
    if isinstance(environment_key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", environment_key):
        return os.environ.get(environment_key)
    compact = {item.replace("_", "") for item in SECRET_KEYS}
    for key, value in section.items():
        name = str(key).lower().replace("-", "_")
        if name in SECRET_KEYS or name.replace("_", "") in compact:
            secret = _env_secret(value)
            if secret:
                return secret
    for key in ("options", "auth", "credentials", "config"):
        nested = section.get(key)
        if isinstance(nested, Mapping):
            secret = _section_secret(nested)
            if secret:
                return secret
    environment = section.get("env")
    if isinstance(environment, Mapping):
        for name, value in environment.items():
            if str(name).upper().endswith(("_API_KEY", "_TOKEN", "_SECRET")):
                secret = _env_secret(value) if value is not None else os.environ.get(str(name))
                if secret:
                    return secret
    return None


def _config_paths(paths: Mapping[str, list[Path]], route: Mapping[str, Any]) -> list[Path]:
    if _route_name(route.get("hostId")) != "codex":
        return paths["opencode"]
    name = route.get("profileName")
    selected = [path for path in paths["codex"] if name and path.name == f"{name}.config.toml"]
    return selected + [path for path in paths["codex"] if not path.name.endswith(".config.toml")]


def _section_endpoint(section: Mapping[str, Any]) -> str | None:
    for current in (section, section.get("options"), section.get("config")):
        if isinstance(current, Mapping):
            for key in ("endpoint", "baseUrl", "baseURL", "base_url", "apiUrl", "apiURL", "api_url", "url"):
                endpoint = _text(current.get(key))
                if endpoint:
                    return endpoint
    return None


def _configured_bundle(repo: Path | None, home: Path, route: Mapping[str, Any]) -> tuple[str, str | None] | None:
    paths = _paths(repo, home)
    for candidate in _config_paths(paths, route):
        value = _read_safe(candidate)
        for section in _sections(value, route):
            endpoint = _section_endpoint(section)
            if endpoint:
                return endpoint, _section_secret(section)
    return None


def _documented_endpoint(route: Mapping[str, Any]) -> str | None:
    if _is_go_identity(route.get("hostId"), route.get("providerId")):
        return GO_ENDPOINT
    if _route_name(route.get("providerId")) == "openai":
        return "https://api.openai.com/v1"
    if _route_name(route.get("providerId")) == "anthropic" and route.get("protocol") == "messages":
        return "https://api.anthropic.com/v1"
    return None


def _documented_auth_secret(repo: Path | None, home: Path, route: Mapping[str, Any]) -> str | None:
    paths = _paths(repo, home)
    for candidate in paths["codex-auth"] + paths["opencode-auth"] + _config_paths(paths, route):
        value = _read_safe(candidate)
        for section in _sections(value, route):
            endpoint = _section_endpoint(section)
            if endpoint and not _is_documented_endpoint(route, endpoint):
                continue
            secret = _section_secret(section)
            if secret:
                return secret
    return None


def _is_documented_endpoint(route: Mapping[str, Any], endpoint: str) -> bool:
    if _is_go_identity(route.get("hostId"), route.get("providerId")):
        return _is_documented_go_endpoint(endpoint)
    documented = _documented_endpoint(route)
    return documented is not None and endpoint.rstrip("/") == documented.rstrip("/")


def _trusted_bundle(repo: Path | None, home: Path, route: Mapping[str, Any]) -> tuple[str | None, str | None]:
    configured = _configured_bundle(repo, home, route)
    if configured is not None:
        endpoint, secret = configured
        # A custom endpoint can only use a credential explicitly co-located in
        # its configured section. Do not pair it with another provider source.
        if secret is None and _is_documented_endpoint(route, endpoint):
            secret = _documented_auth_secret(repo, home, route)
        return endpoint, secret
    endpoint = _documented_endpoint(route)
    return endpoint, _documented_auth_secret(repo, home, route) if endpoint else None


def _go_catalog_secret(repo: Path | None, home: Path) -> str | None:
    for host, provider, config in _documented_go_configs(_paths(repo, home)):
        secret = _section_secret(config)
        if secret:
            return secret
        route = {"hostId": host, "providerId": provider, "modelId": "go-catalog", "protocol": "responses"}
        secret = _documented_auth_secret(repo, home, route)
        if secret:
            return secret
    return None


def _probe_url(endpoint: str, protocol: str) -> str | None:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    suffix = {"responses": "/responses", "chat-completions": "/chat/completions", "messages": "/messages"}.get(protocol)
    if suffix is None:
        return None
    path = parsed.path.rstrip("/")
    if not path.endswith(suffix):
        if path.endswith("/v1") or path.endswith("/api"):
            path += suffix
        else:
            return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _probe_inventory(repo: Path | None, home: Path) -> Mapping[str, Any]:
    if repo is not None:
        return scan_providers(repo, offline=True, home=home)
    with state_lock(home):
        return _scan_providers(None, offline=True, home=home)


def _untrusted_probe_fields(route: Mapping[str, Any]) -> bool:
    blocked = {"endpoint", "baseurl", "base_url", "url", "apiurl", "api_url", "headers", "authtoken", "apikey"}
    compact = {item.replace("_", "") for item in SECRET_KEYS}
    return any((name := str(key).lower().replace("-", "_")) in blocked or name in SECRET_KEYS or name.replace("_", "") in compact for key in route)


def probe_route(route: Mapping[str, Any], *, repo: Path | str | None = None, home: Path | str | None = None) -> dict[str, Any]:
    if not isinstance(route, Mapping):
        raise ValueError("route must be an object")
    selected = normalize_route(route)
    selected["probed"] = False
    diagnostics: list[dict[str, str]] = []
    if _untrusted_probe_fields(route):
        diagnostics.append(_diag("probe-untrusted-fields", "caller endpoint or credentials are not accepted"))
        return {"schemaVersion": SCHEMA_VERSION, "route": selected, "probed": False, "status": "unsupported", "reachable": None, "diagnostics": diagnostics}
    repo_path = _repo(repo) if repo is not None else None
    inventory = _probe_inventory(repo_path, _home(home))
    scanned = next((item for item in inventory.get("routes", []) if isinstance(item, Mapping) and all(selected.get(key) == item.get(key) for key in ROUTE_KEYS)), None)
    if scanned is None:
        diagnostics.append(_diag("probe-route-not-scanned", "targeted route does not match the fresh inventory"))
        return {"schemaVersion": SCHEMA_VERSION, "route": selected, "probed": False, "status": "unsupported", "reachable": None, "diagnostics": diagnostics}
    selected = dict(scanned)
    selected["probed"] = False
    protocol = selected["protocol"]
    endpoint, secret = _trusted_bundle(repo_path, _home(home), selected)
    selected["authConfigured"] = bool(secret)
    target = _probe_url(endpoint, protocol) if endpoint else None
    if not target:
        diagnostics.append(_diag("probe-unsupported", "route lacks a supported exact protocol endpoint"))
        return {"schemaVersion": SCHEMA_VERSION, "route": selected, "probed": False, "status": "unsupported", "reachable": None, "diagnostics": diagnostics}
    if not secret:
        diagnostics.append(_diag("probe-auth-required", "targeted probe requires configured authentication"))
        return {"schemaVersion": SCHEMA_VERSION, "route": selected, "probed": False, "status": "unsupported", "reachable": None, "diagnostics": diagnostics}
    if protocol == "responses":
        payload = {"model": selected["modelId"], "input": "Reply OK.", "max_output_tokens": 16}
    elif protocol == "chat-completions":
        payload = {"model": selected["modelId"], "messages": [{"role": "user", "content": "Reply OK."}], "max_tokens": 16}
    else:
        payload = {"model": selected["modelId"], "max_tokens": 16, "messages": [{"role": "user", "content": "Reply OK."}]}
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if _is_go_identity(selected["hostId"], selected["providerId"]) and _is_documented_go_endpoint(endpoint):
        headers["User-Agent"] = "lemmings-probe/4"
        headers["x-opencode-session"] = digest({key: selected.get(key) for key in ("hostId", "providerId", "modelId", "protocol")})
    if protocol == "messages":
        headers["x-api-key"] = secret
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {secret}"
    request = urllib.request.Request(target, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=3)
        try:
            status_code = int(getattr(response, "status", 200))
            body = response.read(64 * 1024)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    except (OSError, urllib.error.URLError, ValueError, TimeoutError):
        diagnostics.append(_diag("probe-unavailable", "targeted route probe did not complete"))
        return {"schemaVersion": SCHEMA_VERSION, "route": selected, "probed": False, "status": "unavailable", "reachable": False, "diagnostics": diagnostics}
    if not 200 <= status_code < 300:
        diagnostics.append(_diag("probe-rejected", "targeted route probe was rejected"))
        return {"schemaVersion": SCHEMA_VERSION, "route": selected, "probed": False, "status": "rejected", "reachable": False, "diagnostics": diagnostics}
    try:
        response_value = json.loads(body.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        response_value = None
    observed = response_value.get("model") if isinstance(response_value, Mapping) else None
    if observed != selected["modelId"]:
        diagnostics.append(_diag("probe-model-mismatch", "targeted response did not confirm the requested model"))
        return {"schemaVersion": SCHEMA_VERSION, "route": selected, "probed": False, "status": "unsupported", "reachable": True, "diagnostics": diagnostics}
    selected["probed"] = True
    return {"schemaVersion": SCHEMA_VERSION, "route": selected, "probed": True, "status": "probed", "reachable": True, "diagnostics": diagnostics}


__all__ = ["SCHEMA_VERSION", "canonical_json", "digest", "normalize_route", "probe_route", "scan_providers", "state_lock"]
