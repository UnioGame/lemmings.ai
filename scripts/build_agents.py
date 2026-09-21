#!/usr/bin/env python3
"""Generate Codex agent TOML files from the Claude Code agent Markdown files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

AGENTS = Path(__file__).resolve().parents[1] / "agents"


def render(markdown: str) -> str:
    _, front, body = markdown.split("---", 2)
    meta = dict(line.split(":", 1) for line in front.strip().splitlines())
    meta = {key.strip(): value.strip() for key, value in meta.items()}
    writes = "Edit" in meta.get("tools", "")
    return (f"name = {json.dumps(meta['name'])}\n"
            f"description = {json.dumps(meta['description'])}\n"
            f"sandbox_mode = {json.dumps('workspace-write' if writes else 'read-only')}\n"
            f'developer_instructions = """\n{body.strip()}\n"""\n')


def main(check: bool = False) -> int:
    stale = []
    for source in sorted(AGENTS.glob("lemmings-*.md")):
        target = source.with_suffix(".toml")
        text = render(source.read_text(encoding="utf-8"))
        if check:
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                stale.append(target.name)
        else:
            target.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        print("stale agent TOML: " + ", ".join(stale) + "; run scripts/build_agents.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
