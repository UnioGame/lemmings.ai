#!/usr/bin/env python3
"""Generate the default role agents in agents/ from skills/lemmings/roles/*.md (the single source)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "lemmings" / "scripts"))

from lemmings.agents import ROLES, _template, render_claude, render_codex  # noqa: E402


def planned() -> dict[Path, str]:
    files = {}
    for role in ROLES:
        description = _template(role)[0]["description"]
        name = f"lemmings-{role}"
        files[ROOT / "agents" / f"{name}.md"] = render_claude(role, name, description)
        files[ROOT / "agents" / f"{name}.toml"] = render_codex(role, name, description)
    return files


def main(check: bool = False) -> int:
    stale = []
    for path, text in planned().items():
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(path.name)
            if not check:
                path.write_text(text, encoding="utf-8", newline="\n")
    if check and stale:
        print("stale agent files: " + ", ".join(stale) + "; run scripts/build_agents.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
