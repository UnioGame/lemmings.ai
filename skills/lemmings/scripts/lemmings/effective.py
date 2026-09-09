"""Freeze only routing and scoped rules; never persist provider inventories or secrets."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from typing import Mapping, Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def effective_profile(task: Mapping, profile: Mapping) -> dict:
    frozen = task.get("effectiveConfig")
    if not frozen:
        return dict(profile)
    checked_effective(frozen)
    routes = {}
    for role, chain in frozen["profile"].get("roleRoutes", {}).items():
        for route in chain:
            routes.setdefault(route["hostId"], {}).setdefault(role, []).append(route)
    return {**profile, "modelRoutes": routes}


def checked_effective(value: Mapping) -> None:
    if value.get("schemaVersion") != 4 or value.get("digest") != digest({k:v for k,v in value.items() if k != "digest"}):
        raise ValueError("frozen effectiveConfig digest is invalid")
    if not isinstance(value.get("profile"), Mapping) or not isinstance(value.get("rules"), Mapping):
        raise ValueError("effectiveConfig requires profile and rules")


def capture_effective(repo: Path, task: dict, profile: Mapping, *, preset: str | None = None, home: Path | None = None) -> dict:
    if task.get("effectiveConfig"):
        checked_effective(task["effectiveConfig"])
        if preset and preset != task["effectiveConfig"]["profile"].get("name"):
            raise ValueError("an active Task cannot switch its frozen preset; create a new Task")
        return task["effectiveConfig"]
    from .profiles import resolve_profile
    from .rules import resolve_rules
    selected = resolve_profile(repo, preset, home=home)
    # --profile is the existing explicit project settings file; its manual role pins win.
    manual_roles = {}
    fields = {"hostId", "providerId", "modelId", "variantId", "executor", "profileName", "protocol", "quotaGroup", "specializations", "compatible", "configured", "catalogued", "authConfigured", "probed", "source"}
    for host, roles in (profile.get("modelRoutes") or {}).items():
        for role, chain in roles.items():
            manual_roles.setdefault(role, []).extend([{**{k:v for k,v in r.items() if k in fields}, "hostId":host} for r in chain])
    for role, chain in manual_roles.items():
        selected.setdefault("roleRoutes", {})[role] = chain
        selected.setdefault("sources", {})[role] = "project-manual"
    selected["digest"] = digest({k:v for k,v in selected.items() if k != "digest"})
    scope = task.get("ruleSelection") or {}
    paths = scope.get("paths")
    if not paths:
        paths = [r["ref"].split("#", 1)[0] for r in task.get("workingSet", []) if isinstance(r, dict) and r.get("ref")]
        for owned in (task.get("ownership") or {}).get("owned", []):
            prefix = str(owned)
            for marker in ("*", "?", "["):
                prefix = prefix.split(marker, 1)[0]
            if prefix:
                paths.append(prefix.rstrip("/"))
        paths = list(dict.fromkeys(paths))
    rules = resolve_rules(repo, paths=paths or None, technologies=scope.get("technologies"), platforms=scope.get("platforms"))
    value = {"schemaVersion":4,"profile":selected,"rules":rules}
    value["digest"] = digest(value)
    task["effectiveConfig"] = copy.deepcopy(value)
    return value
