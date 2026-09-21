#!/usr/bin/env python3
"""Generate agents/ from the shipped defaults (skills/lemmings/defaults.json) and role prompts.

These files let the Claude Code plugin and a Python-less Codex install work with no setup. They are
byte-identical to what `lemmings agents sync` writes for the shipped agents.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "lemmings" / "scripts"))

from lemmings.agents import load_agents, planned_files  # noqa: E402


def planned() -> dict[Path, str]:
    return {ROOT / "agents" / path.name: text for path, text in planned_files(ROOT, load_agents({})).items()}


def main(check: bool = False) -> int:
    wanted = planned()
    stale = [path.name for path, text in wanted.items()
             if not path.is_file() or path.read_text(encoding="utf-8") != text]
    extra = [path for path in (ROOT / "agents").glob("lemmings-*") if path not in wanted]
    stale += [path.name for path in extra]
    if check:
        if stale:
            print("stale agent files: " + ", ".join(sorted(stale)) + "; run scripts/build_agents.py", file=sys.stderr)
            return 1
        return 0
    for path, text in wanted.items():
        path.write_text(text, encoding="utf-8", newline="\n")
    for path in extra:
        path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
