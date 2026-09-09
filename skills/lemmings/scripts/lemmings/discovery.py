
"""Secret-safe, metadata-only provider discovery for Lemmings v4."""
from __future__ import annotations
import hashlib
import json
import os
import re
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
SAFE_SOURCES = {"codex-config", "codex-profile", "codex-auth", "opencode-config", "opencode-auth", "host-catalog", "state-inventory", "manual", "project-manual", "personal-manual", "generated", "unknown"}
SECRET_KEYS = {"access", "access_token", "api_key", "apikey", "auth", "authorization", "client_secret", "credential", "credentials", "key", "password", "private_key", "refresh", "refresh_token", "secret", "token"}

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
        elif char == "," and re.match(r"\s*[\]}]", source[index + 1:]):
            pass
        else:
            output.append(char)
    return json.loads("".join(output))

def _toml_fallback(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current = result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = result
            for part in line[1:-1].split("."):
                key = part.strip().strip('"')
                child = current.setdefault(key, {})
                if not isinstance(child, dict):
                    child = {}
                    current[key] = child
                current = child
            continue
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        value = raw_value.split(" #", 1)[0].strip()
        try:
            parsed = _jsonc(value) if value.startswith(('"', "[", "{")) else value
        except (TypeError, ValueError):
            parsed = value.strip('"')
        current[key.strip().strip('"')] = parsed
    return result

def _read(path: Path) -> Any | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None
    try:
        if path.suffix.lower() in {".json", ".jsonc"}:
            return _jsonc(text)
        try:
            import tomllib
            return tomllib.loads(text)
        except (ImportError, AttributeError):
            return _toml_fallback(text)
    except (TypeError, ValueError):
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

def _paths(repo: Path, home: Path) -> dict[str, list[Path]]:
    return {
        "codex": _unique([home / ".codex" / name for name in ("config.toml", "config.json", "config.jsonc", "profiles.toml", "profiles.json", "profiles.jsonc")] + [repo / ".codex" / name for name in ("config.toml", "config.json", "config.jsonc")]),
        "opencode": _unique([repo / name for name in ("opencode.json", "opencode.jsonc")] + [repo / ".opencode" / name for name in ("opencode.json", "opencode.jsonc")] + [home / ".config" / "opencode" / name for name in ("opencode.json", "opencode.jsonc")] + [home / ".opencode" / name for name in ("opencode.json", "opencode.jsonc", "config.json", "config.jsonc")]),
        "codex-auth": _unique([home / ".codex" / "auth.json", repo / ".codex" / "auth.json"]),
        "opencode-auth": _unique([repo / ".opencode" / "auth.json", home / ".config" / "opencode" / "auth.json", home / ".local" / "share" / "opencode" / "auth.json", home / ".opencode" / "auth.json"]),
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

def _route_key(route: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(route.get(key, "")) for key in ("hostId", "providerId", "modelId", "variantId"))  # type: ignore[return-value]

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

def _add(routes: dict[tuple[str, str, str, str], dict[str, Any]], value: Mapping[str, Any]) -> None:
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

def _codex(data: Mapping[str, Any], source: str, routes: dict[tuple[str, str, str, str], dict[str, Any]], diagnostics: list[dict[str, str]]) -> None:
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
        metadata = metadata or {}
        _add(routes, {"hostId": "codex", "providerId": provider_id, "modelId": model_id, "profileName": profile, "protocol": metadata.get("wire_api", metadata.get("wireApi", metadata.get("protocol", protocol))), "executor": "codex", "configured": True, "source": source, "quotaGroup": metadata.get("quotaGroup")})
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

def _opencode(data: Mapping[str, Any], routes: dict[tuple[str, str, str, str], dict[str, Any]]) -> None:
    providers = data.get("provider") or data.get("providers") or {}
    providers = providers if isinstance(providers, Mapping) else {}
    def add(provider: Any, model: Any, metadata: Mapping[str, Any] | None = None, profile: str | None = None) -> None:
        provider_id, model_id = _split_model(model, provider)
        if not provider_id or not model_id:
            return
        metadata = metadata or {}
        _add(routes, {"hostId": "opencode", "providerId": provider_id, "modelId": model_id, "profileName": profile, "protocol": metadata.get("protocol", metadata.get("api", metadata.get("wireApi", "unknown"))), "executor": "opencode", "configured": True, "authConfigured": _provider_auth(metadata), "source": "opencode-config", "quotaGroup": metadata.get("quotaGroup")})
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

def _catalog_items(value: Any) -> list[tuple[str | None, Mapping[str, Any]]]:
    if isinstance(value, list):
        return [(_text(item.get("hostId")), item) for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    if isinstance(value.get("data"), list) or isinstance(value.get("models"), list):
        host = _text(value.get("hostId")) or _text(value.get("host"))
        return [(host or _text(item.get("hostId")), item) for item in (value.get("data") or value.get("models") or []) if isinstance(item, Mapping)]
    result = []
    for host, item in value.items():
        if host in {"schemaVersion", "scannedAt", "providers", "routes", "diagnostics", "status"}:
            continue
        if isinstance(item, Mapping):
            nested = item.get("models") or item.get("data")
            if isinstance(nested, list):
                result.extend((_text(model.get("hostId")) or str(host), model) for model in nested if isinstance(model, Mapping))
            elif item.get("id") or item.get("model") or item.get("modelId"):
                result.append((str(host), item))
        elif isinstance(item, list):
            result.extend((str(host), model) for model in item if isinstance(model, Mapping))
    return result

def _catalog(value: Any, diagnostics: list[dict[str, str]]) -> tuple[list[dict[str, Any]], set[str]]:
    result = []
    hosts: set[str] = set()
    for parent, item in _catalog_items(value):
        host = _identifier(item.get("hostId")) or _identifier(parent) or "unknown"
        hosts.add(host)
        raw = _identifier(item.get("modelId")) or _identifier(item.get("model")) or _identifier(item.get("id")) or _identifier(item.get("name"))
        provider = _identifier(item.get("providerId")) or _identifier(item.get("provider"))
        model = raw
        if raw and not provider and "/" in raw:
            provider, model = raw.split("/", 1)
        if not provider or not model:
            continue
        protocol = _protocol(item.get("protocol", item.get("wireApi", item.get("api"))))
        executor = _executor(host, protocol, item.get("executor"))
        variants = item.get("variants") if isinstance(item.get("variants"), list) else [item.get("variantId")]
        for variant in variants or [None]:
            route = {"hostId": host, "providerId": provider, "modelId": model, "protocol": protocol, "executor": executor, "configured": False, "catalogued": True, "compatible": _compatible(executor, protocol), "authConfigured": bool(item.get("authConfigured", False)), "probed": False, "source": "host-catalog"}
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

def scan_providers(repo: Path | str, *, offline: bool = False, home: Path | str | None = None, host_catalog: Mapping[str, Any] | list[Any] | None = None) -> dict[str, Any]:
    repo_path, home_path = _repo(repo), _home(home)
    paths = _paths(repo_path, home_path)
    diagnostics = []
    routes: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    providers: dict[str, dict[str, Any]] = {}
    auth_ids: set[str] = set()
    for path in paths["codex-auth"] + paths["opencode-auth"]:
        value = _read(path)
        if value is not None:
            auth_ids.update(_auth_present(value))
    for path in paths["codex"]:
        value = _read(path)
        if isinstance(value, Mapping):
            _codex(value, "codex-profile" if "profile" in path.name.lower() else "codex-config", routes, diagnostics)
            if _provider_auth(value):
                provider = _identifier(value.get("model_provider") or value.get("modelProvider") or value.get("providerId"))
                if provider:
                    auth_ids.add(provider)
    for path in paths["opencode"]:
        value = _read(path)
        if isinstance(value, Mapping):
            _opencode(value, routes)
            maps = value.get("provider") or value.get("providers") or {}
            if isinstance(maps, Mapping):
                for provider, config in maps.items():
                    if isinstance(config, Mapping) and _provider_auth(config) and _identifier(provider):
                        auth_ids.add(_identifier(provider) or "")
    catalog_routes, catalog_hosts = ([], set())
    if host_catalog is not None:
        catalog_routes, catalog_hosts = _catalog(host_catalog, diagnostics)
        for route in catalog_routes:
            _add(routes, route)
    inventory = _state_inventory(home_path)
    stale_providers = [item for item in (inventory or {}).get("providers", []) if isinstance(item, Mapping)]
    stale_routes = [item for item in (inventory or {}).get("routes", []) if isinstance(item, Mapping)]
    if offline and not catalog_routes:
        for item in stale_routes:
            try:
                _add(routes, {**dict(item), "source": "state-inventory", "probed": False})
            except (TypeError, ValueError):
                pass
        if stale_routes:
            diagnostics.append(_diag("catalog-stale", "using the previous sanitized inventory in offline mode", "state-inventory"))
    current_catalog = host_catalog is not None
    for route in routes.values():
        provider = str(route["providerId"])
        route["authConfigured"] = bool(route.get("authConfigured")) or provider in auth_ids
        route["compatible"] = _compatible(str(route["executor"]), str(route["protocol"]))
        status = "current" if current_catalog else ("stale" if stale_providers else "unavailable")
        _merge_provider(providers, provider, str(route.get("source", "unknown")), bool(route.get("authConfigured")), status)
    for item in stale_providers:
        provider = _identifier(item.get("providerId"))
        if provider:
            _merge_provider(providers, provider, "state-inventory", bool(item.get("authConfigured")), "stale")
    for provider in sorted(auth_ids):
        _merge_provider(providers, provider, "opencode-auth", True, "current" if current_catalog else "unavailable")
    if not current_catalog and not offline:
        diagnostics.append(_diag("catalog-unavailable", "no explicit provider catalog supplied; configured models remain uncatalogued"))
    if not routes and not providers:
        diagnostics.append(_diag("no-providers", "no supported provider metadata was found"))
    diagnostics.extend(_alias(routes.values(), [*providers, *catalog_hosts]))
    return {"schemaVersion": SCHEMA_VERSION, "scannedAt": _now(), "providers": sorted(providers.values(), key=lambda item: item["providerId"]), "routes": sorted(routes.values(), key=_route_key), "diagnostics": sorted(diagnostics, key=lambda item: (item.get("code", ""), item.get("source", ""), item.get("message", "")))}

def probe_route(route: Mapping[str, Any], *, home: Path | str | None = None) -> dict[str, Any]:
    if not isinstance(route, Mapping):
        raise ValueError("route must be an object")
    selected = normalize_route(route)
    auth_ids: set[str] = set()
    paths = _paths(_home(home), _home(home))
    for path in paths["codex-auth"] + paths["opencode-auth"]:
        value = _read(path)
        if value is not None:
            auth_ids.update(_auth_present(value))
    selected["authConfigured"] = bool(selected.get("authConfigured")) or selected["providerId"] in auth_ids
    selected["probed"] = True
    status, reachable, diagnostics = "metadata-only", None, []
    endpoint = _text(route.get("probeUrl")) or _text(route.get("endpoint"))
    if endpoint:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            status = "invalid-endpoint"
            diagnostics.append(_diag("probe-endpoint-invalid", "explicit probe endpoint is not a safe HTTP URL"))
        else:
            safe_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
            try:
                request = urllib.request.Request(safe_url, method="HEAD", headers={"User-Agent": "lemmings-discovery/4"})
                with urllib.request.urlopen(request, timeout=2) as response:
                    reachable = 200 <= int(getattr(response, "status", 200)) < 400
                    status = "reachable" if reachable else "unavailable"
            except (OSError, urllib.error.URLError, ValueError, TimeoutError):
                status = "unavailable"
                diagnostics.append(_diag("probe-unavailable", "explicit route probe did not complete"))
    return {"schemaVersion": SCHEMA_VERSION, "route": selected, "probed": True, "status": status, "reachable": reachable, "diagnostics": diagnostics}

__all__ = ["SCHEMA_VERSION", "canonical_json", "digest", "normalize_route", "probe_route", "scan_providers"]
