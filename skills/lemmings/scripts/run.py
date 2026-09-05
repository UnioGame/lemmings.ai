#!/usr/bin/env python3
"""Run the Lemmings runtime shipped with this skill."""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True


RUNTIME_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(RUNTIME_ROOT))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        from lemmings.hooks import main as hook_main

        return hook_main(sys.argv[2:])

    from lemmings.cli import main as cli_main

    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
