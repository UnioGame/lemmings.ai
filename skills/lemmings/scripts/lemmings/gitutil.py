"""Git and configuration helpers shared by the Lemmings helper commands."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

CONFIG_PATH = ".agents/lemmings.json"


class HelperError(Exception):
    """A user-facing failure reported as JSON by the CLI."""


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if check and process.returncode:
        detail = (process.stderr or process.stdout).strip().splitlines()
        raise HelperError(f"git {' '.join(args[:2])} failed: {detail[-1] if detail else process.returncode}")
    return process


def toplevel(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve()


def common_dir(path: Path) -> Path:
    value = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return Path(value).resolve()


def state_dir(repo: Path) -> Path:
    path = common_dir(repo) / "lemmings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config(repo: Path) -> dict[str, Any]:
    path = repo / CONFIG_PATH
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as error:
        raise HelperError(f"{CONFIG_PATH} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise HelperError(f"{CONFIG_PATH} must contain a JSON object")
    return value
