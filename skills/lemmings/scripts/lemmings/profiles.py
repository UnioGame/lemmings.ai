"""Named, confirmation-gated route profiles.

Profiles are preference snapshots.  They never rank models, edit project
configuration, or turn a discovered catalog into an implicit assignment.
Project pins are resolved per role before personal presets and generated
selections, and generated state is written atomically under ``~/.lemmings``.
"""

from __future__ import annotations

import json
import os
import tempfile
from functools import wraps
from pathlib import Path
from typing import Any, Mapping

from .discovery import SCHEMA_VERSION, digest, normalize_route, scan_providers, state_lock


ROLES = ("worker", "reviewer", "explorer")
_PROFILE_KEYS = {"roleRoutes", *ROLES}


def _repo(repo: Path | str) -> Path:
    return Path(repo).expanduser().resolve()


def _home(home: Path | str | None) -> Path:
    return Path(home).expanduser().resolve() if home is not None else Path.home().resolve()


def _repo_key(repo: Path) -> str:
    return str(repo).replace("\\", "/").casefold() if os.name == "nt" else str(repo).replace("\\", "/")


def _project_path(repo: Path) -> Path:
    return repo / ".agents" / "lemmings.json"


def _personal_path(home: Path) -> Path:
    return home / ".lemmings" / "profiles.json"


def _state_path(home: Path) -> Path:
    return home / ".lemmings" / "state.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"invalid JSON profile source: {path.name}") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"profile source must be a JSON object: {path.name}")
    return dict(value)


def _raw_digest(path: Path) -> str:
    try:
        return digest({"exists": True, "bytes": path.read_bytes().hex()})
    except OSError:
        return digest({"exists": False})


def _manual_source(repo: Path, home: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    project_path = _project_path(repo)
    personal_path = _personal_path(home)
    project = _load(project_path)
    personal = _load(personal_path)
    manual_digest = digest({
        "project": _raw_digest(project_path),
        "personal": _raw_digest(personal_path),
    })
    return project, personal, manual_digest


def _profile_map(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    profiles = value.get("profiles")
    return profiles if isinstance(profiles, Mapping) else {}


def _route_input(value: Any, host_hint: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("profile routes must be objects")
    route_value = dict(value)
    if not route_value.get("hostId") and host_hint:
        route_value["hostId"] = host_hint
    return normalize_route(route_value)


def _role_routes(value: Any, *, host_hint: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Normalize role chains from roleRoutes, direct roles, or legacy hosts."""

    if not isinstance(value, Mapping):
        raise ValueError("profile requires roleRoutes or role arrays")
    if isinstance(value.get("roleRoutes"), Mapping):
        value = value["roleRoutes"]
    direct = any(role in value for role in ROLES)
    output: dict[str, list[dict[str, Any]]] = {role: [] for role in ROLES}
    if direct:
        for role in ROLES:
            choices = value.get(role, [])
            if choices is None:
                choices = []
            if not isinstance(choices, list):
                raise ValueError(f"profile role {role} must be an array")
            output[role] = [_route_input(item, host_hint) for item in choices]
        return output
    # Legacy modelRoutes is a host map.  Preserve host and role order; no
    # model is selected or ranked while flattening it into a profile chain.
    for host, roles in value.items():
        if not isinstance(roles, Mapping):
            continue
        for role in ROLES:
            choices = roles.get(role, [])
            if choices is None:
                choices = []
            if not isinstance(choices, list):
                raise ValueError(f"profile host {host} role {role} must be an array")
            output[role].extend(_route_input(item, str(host)) for item in choices)
    return output


def _raw_role_items(value: Any, *, host_hint: str | None = None) -> dict[str, list[tuple[Mapping[str, Any], str | None]]]:
    if not isinstance(value, Mapping):
        raise ValueError("profile requires roleRoutes or role arrays")
    if isinstance(value.get("roleRoutes"), Mapping):
        value = value["roleRoutes"]
    output: dict[str, list[tuple[Mapping[str, Any], str | None]]] = {role: [] for role in ROLES}
    if any(role in value for role in ROLES):
        for role in ROLES:
            choices = value.get(role, [])
            if choices is None:
                choices = []
            if not isinstance(choices, list) or not all(isinstance(item, Mapping) for item in choices):
                raise ValueError(f"profile role {role} must be an array")
            output[role] = [(item, host_hint) for item in choices]
        return output
    for host, roles in value.items():
        if not isinstance(roles, Mapping):
            continue
        for role in ROLES:
            choices = roles.get(role, [])
            if choices is None:
                choices = []
            if not isinstance(choices, list) or not all(isinstance(item, Mapping) for item in choices):
                raise ValueError(f"profile host {host} role {role} must be an array")
            output[role].extend((item, str(host)) for item in choices)
    return output


def _has_routes(routes: Mapping[str, Any] | None) -> bool:
    return bool(routes and any(isinstance(routes.get(role), list) and routes.get(role) for role in ROLES))


def _transactional(function):
    @wraps(function)
    def wrapped(repo, *args, home=None, **kwargs):
        with state_lock(_home(home)):
            return function(repo, *args, home=home, **kwargs)
    return wrapped


def _active_name(source: Mapping[str, Any], label: str) -> str | None:
    value = source.get("activeProfile")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value.strip() not in _profile_map(source):
        raise ValueError(f"{label} activeProfile does not name an available profile")
    return value.strip()


def _route_identity(route: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(route.get(key, "")) for key in ("hostId", "providerId", "modelId", "variantId"))


def _validate_bound_routes(role_routes: Mapping[str, list[Mapping[str, Any]]], inventory: Mapping[str, Any]) -> None:
    bound = [item for item in inventory.get("routes", []) if isinstance(item, Mapping)]
    for role in ROLES:
        for route in role_routes.get(role, []):
            identity = _route_identity(route)
            matches = [item for item in bound if _route_identity(item) == identity]
            if not matches:
                raise ValueError(f"profile route is absent from bound inventory: {role}")
            if not any(item.get("compatible") is True for item in matches):
                raise ValueError(f"profile route is incompatible with bound inventory: {role}")


def _canonical_bound_routes(value: Any, inventory: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    bound = [item for item in inventory.get("routes", []) if isinstance(item, Mapping)]
    output: dict[str, list[dict[str, Any]]] = {role: [] for role in ROLES}
    protected = ("executor", "protocol", "profileName", "compatible")
    for role, items in _raw_role_items(value).items():
        for raw, host_hint in items:
            requested = _route_input(raw, host_hint)
            matches = [item for item in bound if _route_identity(requested) == _route_identity(item)]
            if not matches:
                raise ValueError(f"profile route is absent from bound inventory: {role}")
            explicit = [field for field in protected if field in raw]
            if explicit:
                exact = [item for item in matches if all(requested.get(field) == item.get(field) for field in explicit)]
                if not exact:
                    raise ValueError(f"profile route execution fields differ from bound inventory: {role}")
                matches = exact
            compatible = [item for item in matches if item.get("compatible") is True]
            if not compatible:
                raise ValueError(f"profile route is incompatible with bound inventory: {role}")
            if "profileName" not in raw:
                unprofiled = [item for item in compatible if not item.get("profileName")]
                if unprofiled:
                    compatible = unprofiled
                elif len({item.get("profileName") for item in compatible}) > 1:
                    raise ValueError(f"profile route needs an explicit profileName: {role}")
            output[role].append(normalize_route(compatible[0]))
    return output


def _project_manual_routes(project: Mapping[str, Any], selected_name: str | None = None) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    model_routes = project.get("modelRoutes")
    legacy = _role_routes(model_routes) if isinstance(model_routes, Mapping) and model_routes else {role: [] for role in ROLES}
    profiles = _profile_map(project)
    selected = profiles.get(selected_name) if selected_name else None
    if isinstance(selected, Mapping):
        profile_routes = _role_routes(selected)
        legacy = {
            role: legacy[role] if legacy[role] else profile_routes[role]
            for role in ROLES
        }
    return legacy, "project-manual" if _has_routes(legacy) else None


def _personal_routes(personal: Mapping[str, Any], name: str | None) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    profiles = _profile_map(personal)
    selected = profiles.get(name) if name else None
    if not isinstance(selected, Mapping):
        return {role: [] for role in ROLES}, None
    routes = _role_routes(selected)
    return routes, "personal-manual" if _has_routes(routes) else None


def _state_profiles(state: Mapping[str, Any]) -> Mapping[str, Any]:
    profiles = state.get("profiles")
    return profiles if isinstance(profiles, Mapping) else {}


def _inventory_for(repo: Path, home: Path, host_catalog: Mapping[str, Any] | list[Any] | None = None) -> dict[str, Any]:
    snapshot = scan_providers(repo, offline=False, home=home, host_catalog=host_catalog)
    return snapshot


def _inventory_digest(snapshot: Mapping[str, Any]) -> str:
    stable = {key: value for key, value in snapshot.items() if key != "scannedAt"}
    return digest(stable)


def _proposal_body(proposal: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": proposal.get("schemaVersion"),
        "name": proposal.get("name"),
        "roleRoutes": proposal.get("roleRoutes"),
        "inventoryDigest": proposal.get("inventoryDigest"),
        "manualDigest": proposal.get("manualDigest"),
    }


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _locked_write(path: Path, value: Mapping[str, Any]) -> None:
    with state_lock(path.parent.parent):
        _atomic_write(path, value)


@_transactional
def build_profile_proposal(
    repo: Path | str,
    name: str,
    routes: Mapping[str, Any],
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    repo_path, home_path = _repo(repo), _home(home)
    profile_name = name.strip() if isinstance(name, str) else ""
    if not profile_name:
        raise ValueError("profile name must be non-empty")
    project, personal, manual_digest = _manual_source(repo_path, home_path)
    inventory = _inventory_for(repo_path, home_path)
    role_routes = _canonical_bound_routes(routes, inventory)
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "name": profile_name,
        "roleRoutes": role_routes,
        "inventoryDigest": _inventory_digest(inventory),
        "manualDigest": manual_digest,
    }
    # Reading the sources above is intentional: a proposal binds to the exact
    # manual input revision even though it never embeds those source values.
    del project, personal
    return {**body, "proposalDigest": digest(body)}


@_transactional
def apply_profile_proposal(
    repo: Path | str,
    proposal: Mapping[str, Any],
    confirmation: str,
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    repo_path, home_path = _repo(repo), _home(home)
    if not isinstance(proposal, Mapping) or proposal.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("profile proposal must use schemaVersion 5")
    expected = digest(_proposal_body(proposal))
    if proposal.get("proposalDigest") != expected or confirmation != expected:
        raise ValueError("confirmation digest does not match the current profile proposal")
    name = proposal.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("profile proposal name must be non-empty")
    current_inventory = _inventory_for(repo_path, home_path)
    _, _, current_manual_digest = _manual_source(repo_path, home_path)
    if proposal.get("inventoryDigest") != _inventory_digest(current_inventory):
        raise ValueError("profile inventory changed since proposal")
    if proposal.get("manualDigest") != current_manual_digest:
        raise ValueError("manual profile inputs changed since proposal")
    role_routes = _canonical_bound_routes(proposal.get("roleRoutes") or {}, current_inventory)
    state_path = _state_path(home_path)
    state = _load(state_path)
    profiles = dict(_state_profiles(state))
    profiles[name.strip()] = {"roleRoutes": role_routes}
    updated = dict(state)
    updated["schemaVersion"] = SCHEMA_VERSION
    updated["inventory"] = current_inventory
    updated["profiles"] = profiles
    selections = updated.get("selections")
    updated["selections"] = dict(selections) if isinstance(selections, Mapping) else {}
    _locked_write(state_path, updated)
    return {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "name": name.strip(),
        "proposalDigest": confirmation,
        "statePath": str(state_path),
    }


@_transactional
def use_profile(
    repo: Path | str,
    name: str,
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    repo_path, home_path = _repo(repo), _home(home)
    profile_name = name.strip() if isinstance(name, str) else ""
    if not profile_name:
        raise ValueError("profile name must be non-empty")
    project, personal, _ = _manual_source(repo_path, home_path)
    state_path = _state_path(home_path)
    state = _load(state_path)
    available = set(_profile_map(project)) | set(_profile_map(personal)) | set(_state_profiles(state))
    if profile_name not in available:
        raise ValueError(f"unknown profile: {profile_name}")
    selections = dict(state.get("selections")) if isinstance(state.get("selections"), Mapping) else {}
    selections[_repo_key(repo_path)] = profile_name
    updated = dict(state)
    updated["schemaVersion"] = SCHEMA_VERSION
    updated["selections"] = selections
    if not isinstance(updated.get("profiles"), Mapping):
        updated["profiles"] = {}
    _locked_write(state_path, updated)
    return {"ok": True, "schemaVersion": SCHEMA_VERSION, "name": profile_name, "selection": profile_name}


def inspect_profiles(repo: Path | str, *, home: Path | str | None = None) -> dict[str, Any]:
    repo_path, home_path = _repo(repo), _home(home)
    project, personal, _ = _manual_source(repo_path, home_path)
    state = _load(_state_path(home_path))
    project_active = _active_name(project, "project")
    personal_active = _active_name(personal, "personal")
    origins: dict[str, list[str]] = {}
    for name in _profile_map(project):
        origins.setdefault(str(name), []).append("project-manual")
    for name in _profile_map(personal):
        origins.setdefault(str(name), []).append("personal-manual")
    for name in _state_profiles(state):
        origins.setdefault(str(name), []).append("generated")
    selections = state.get("selections") if isinstance(state.get("selections"), Mapping) else {}
    selected = selections.get(_repo_key(repo_path))
    if not isinstance(selected, str) or not selected:
        selected = project_active or personal_active
    names = sorted(origins)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "names": names,
        "available": names,
        "origins": {name: sorted(values) for name, values in sorted(origins.items())},
        "selection": selected,
    }


def resolve_profile(
    repo: Path | str,
    name: str | None = None,
    *,
    home: Path | str | None = None,
    host_catalog: Mapping[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    repo_path, home_path = _repo(repo), _home(home)
    project, personal, _ = _manual_source(repo_path, home_path)
    state = _load(_state_path(home_path))
    state_profiles = _state_profiles(state)
    selections = state.get("selections") if isinstance(state.get("selections"), Mapping) else {}
    project_active = _active_name(project, "project")
    personal_active = _active_name(personal, "personal")
    selected_name = name.strip() if isinstance(name, str) and name.strip() else None
    if selected_name is None:
        current = selections.get(_repo_key(repo_path))
        if isinstance(current, str) and current.strip():
            selected_name = current.strip()
        elif project_active:
            selected_name = project_active
        elif personal_active:
            selected_name = personal_active
    project_routes, project_source = _project_manual_routes(project, project_active or selected_name)
    personal_name = selected_name
    personal_profiles = _profile_map(personal)
    if personal_name not in personal_profiles:
        personal_name = personal_active
    personal_routes, personal_source = _personal_routes(personal, personal_name)
    generated_value = state_profiles.get(selected_name) if selected_name else None
    generated_routes = _role_routes(generated_value) if isinstance(generated_value, Mapping) else {role: [] for role in ROLES}
    role_routes: dict[str, list[dict[str, Any]]] = {}
    sources: dict[str, str] = {}
    for role in ROLES:
        if project_routes[role]:
            role_routes[role] = project_routes[role]
            sources[role] = project_source or "project-manual"
        elif personal_routes[role]:
            role_routes[role] = personal_routes[role]
            sources[role] = personal_source or "personal-manual"
        elif generated_routes[role]:
            role_routes[role] = generated_routes[role]
            sources[role] = f"generated:{selected_name}"
        else:
            role_routes[role] = []
            sources[role] = "current-host-default"
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "name": selected_name,
        "roleRoutes": role_routes,
        "sources": sources,
    }
    return {**body, "digest": digest(body)}


__all__ = [
    "SCHEMA_VERSION",
    "apply_profile_proposal",
    "build_profile_proposal",
    "inspect_profiles",
    "resolve_profile",
    "use_profile",
]
