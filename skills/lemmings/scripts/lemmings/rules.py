"""Bounded project discovery and references to optional, version-aware rule packs."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from .bundle import skill_root

ENGINES = {"unity", "unreal", "godot", "defold", "flutter", "phaser", "pixijs"}
ALIASES = {"pixi.js": "pixijs", "pixi": "pixijs", "ue": "unreal"}
TARGETS = {"web", "webgl", "html5", "android", "ios", "mobile", "desktop", "windows", "linux", "macos"}
EXCLUDED = {".git", ".agents", ".codex", ".lemmings-worktrees", "node_modules", "library", "temp", "obj", "logs", "build", "builds", "dist", ".godot", ".import", ".internal", ".dart_tool", "intermediate", "saved", "deriveddatacache", "binaries", ".next", "coverage", "vendor"}
SOURCE_EXTENSIONS = {".js", ".ts", ".tsx", ".jsx", ".html", ".mjs"}
MAX_BYTES = 65536


def _read(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > MAX_BYTES or path.is_symlink():
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def _json(path: Path) -> dict:
    try:
        value = json.loads(_read(path))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _sources(paths: list[Path]) -> dict[str, str]:
    result = {}
    for path in paths[:24]:
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        text = _read(path)
        for dependency, technology in (("phaser", "phaser"), ("pixi.js", "pixijs")):
            escaped = re.escape(dependency)
            # Direct module references or CDN script URLs; ordinary prose and lockfiles do not count.
            pattern = rf'''(?:\bfrom\s*|\bimport\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)["']{escaped}(?:/[^"']*)?["']|<script\b[^>]*\bsrc=["'][^"']*(?:cdn|unpkg|jsdelivr)[^"']*/{escaped}(?:@|/)[^"']*["']'''
            if re.search(pattern, text, re.I):
                result[technology] = path.name
    return result


def _flutter_dependency(text: str) -> bool:
    in_dependencies = False
    flutter_indent = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        value = line.strip()
        if indent == 0:
            in_dependencies = value == "dependencies:"
            flutter_indent = None
            continue
        if not in_dependencies:
            continue
        if flutter_indent is not None and indent > flutter_indent:
            key, _, sdk = value.partition(":")
            if key.strip("\"'") == "sdk" and sdk.strip().strip("\"'") == "flutter":
                return True
        else:
            flutter_indent = indent if value.strip("\"'") == "flutter:" else None
    return False


def _detect(directory: Path, scoped_files: list[Path]) -> tuple[bool, dict]:
    technologies: dict[str, str] = {}
    evidence: list[str] = []
    marker = False
    version = _read(directory / "ProjectSettings/ProjectVersion.txt")
    if version and (directory / "Assets").is_dir():
        match = re.search(r"m_EditorVersion:\s*(\S+)", version)
        technologies["unity"] = match.group(1) if match else "project-pinned"
        evidence.append("ProjectSettings/ProjectVersion.txt"); marker = True
    projects = sorted(directory.glob("*.uproject"))[:4]
    if projects:
        technologies["unreal"] = str(_json(projects[0]).get("EngineAssociation") or "project-pinned")
        evidence.append(projects[0].name); marker = True
    for name, tech in (("project.godot", "godot"), ("game.project", "defold")):
        if (directory / name).is_file():
            marker = True; technologies[tech] = "project-pinned"; evidence.append(name)
    pubspec = _read(directory / "pubspec.yaml")
    if pubspec:
        marker = True
        # Match a direct SDK dependency, not a Flutter mention in descriptions or comments.
        if _flutter_dependency(pubspec):
            technologies["flutter"] = "pubspec SDK constraint"; evidence.append("pubspec.yaml")
    package = _json(directory / "package.json")
    if (directory / "package.json").is_file():
        marker = True
        for section in ("dependencies", "devDependencies"):
            dependencies = package.get(section) or {}
            if isinstance(dependencies, dict):
                for name, tech in (("phaser", "phaser"), ("pixi.js", "pixijs")):
                    if name in dependencies:
                        technologies[tech] = str(dependencies[name]); evidence.append("package.json:" + section + "." + name)
    direct = _sources([*scoped_files, directory / "index.html", directory / "src/main.ts", directory / "src/main.js"])
    for tech, source in direct.items():
        technologies.setdefault(tech, "source-pinned"); evidence.append(source)
    return marker, {"technologies": sorted(technologies), "versions": technologies, "evidence": sorted(set(evidence))}


def _excluded(repo: Path, path: Path) -> bool:
    return any(part.lower() in EXCLUDED for part in path.relative_to(repo).parts)


def _names(values, allowed, aliases=None):
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    result = []
    for value in values:
        name = str(value).lower()
        name = (aliases or {}).get(name, name)
        if name not in allowed:
            raise ValueError("unknown rule selection: " + name)
        if name not in result:
            result.append(name)
    return sorted(result)


def resolve_rules(repo, paths=None, technologies=None, platforms=None) -> dict:
    repo = Path(repo).resolve()
    explicit = _names(technologies, ENGINES, ALIASES)
    targets = _names(platforms, TARGETS) or []
    values = [paths] if isinstance(paths, str) else list(paths or ["."])
    if len(values) > 128:
        raise ValueError("rule scope exceeds128 paths; narrow the task")
    scoped = []
    for value in values:
        raw = repo / str(value)
        path = raw.resolve()
        if not path.is_relative_to(repo):
            raise ValueError("rule scope escapes repository")
        if raw.is_symlink() or _excluded(repo, path):
            continue
        scoped.append(path)
    projects = {}
    for path in scoped:
        current = path if path.is_dir() else path.parent
        fallback = None
        while current.is_relative_to(repo):
            marker, info = _detect(current, [p for p in scoped if p.is_relative_to(current)])
            if fallback is None and info["technologies"]:
                fallback = (current, info)
            if marker:
                if info["technologies"] or explicit is not None:
                    projects[current] = info
                break
            if current == repo:
                if fallback is not None:
                    projects[fallback[0]] = fallback[1]
                break
            current = current.parent
    # Default root discovery is intentionally shallow and bounded; explicit file scope never scans siblings.
    if not projects and values == ["."]:
        queue = [(repo, 0)]
        visited = 0
        while queue and visited < 128:
            directory, depth = queue.pop(0); visited += 1
            marker, info = _detect(directory, [])
            if info["technologies"]:
                projects[directory] = info
            if not marker and depth < 2:
                queue.extend((child, depth + 1) for child in sorted(directory.iterdir()) if child.is_dir() and not child.is_symlink() and child.name.lower() not in EXCLUDED)
    if explicit is not None or targets:
        if not projects:
            projects[repo] = {"technologies": [], "versions": {}, "evidence": []}
        for info in projects.values():
            if explicit is not None:
                info["technologies"] = explicit
                info["versions"] = {key: info["versions"].get(key, "explicit; inspect pinned project version") for key in explicit}
                info["evidence"] = ["explicit task selection"]
    output = []
    selected = set()
    for root, info in sorted(projects.items()):
        selected.update(info["technologies"])
        output.append({"root": root.relative_to(repo).as_posix(), **info, "platforms": targets})
    if targets:
        selected.add("platforms")
    refs = []
    if selected:
        bundle = skill_root(repo)
        manifest_path = bundle / "rules/manifest.json"
        if not manifest_path.is_relative_to(repo) or not manifest_path.is_file():
            raise ValueError("selected rules need the Lemmings bundle installed inside the target repository")
        manifest = _json(manifest_path)
        for name in ["manifest", *sorted(selected)]:
            filename = "manifest.json" if name == "manifest" else manifest["packs"][name]["file"]
            path = (bundle / "rules" / filename).resolve()
            if not path.is_relative_to(bundle / "rules") or not path.is_relative_to(repo):
                raise ValueError("rule reference escapes the installed bundle")
            refs.append({"ref": path.relative_to(repo).as_posix(), "purpose": "Optional task-scoped rules: " + name, "contentHash": hashlib.sha256(path.read_bytes()).hexdigest()})
    result = {"projects": output, "ruleRefs": refs}
    result["digest"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result
