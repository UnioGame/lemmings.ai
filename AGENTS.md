# AGENTS.md

## Scope

- This repository is the Lemmings skill (a Codex and Claude Code plugin, also packaged for Unity as `unigame.ai.lemmings`), its optional Python helper, and the optional telemetry package.
- Do not add Unity Runtime or Editor assemblies unless a task explicitly requires them.

## Sources of truth

- `skills/lemmings/SKILL.md` owns the entire workflow. Keep it complete and short (under 150 lines). The skill must work without Python.
- `skills/lemmings/references/` holds details that are needed only for specific operations: the helper, game projects, and skill reuse.
- `agents/*.md` are the role definitions. `agents/*.toml` are generated from them by `scripts/build_agents.py`; never edit the TOML files by hand.
- `packages/lemmings-telemetry/` is optional. The skill and helper must never import it.

## Design rules

- AI-first: the model makes decisions and does the work. Python only does what an agent cannot do reliably (worktree lifecycle, scope checks from the real diff, launching other host CLIs, verifying model identity). Do not add task state machines, JSON protocol artifacts, budgets, or hooks.
- Never silently substitute a model the user or config assigned.
- The reviewer is independent and read-only. P3 findings never block.
- Every new task branch is named `task/<slug>`.
- The helper uses only the standard library and supports Python 3.10+.

## Validation

- `python -m pytest -q` (tests must be hermetic: no dependence on the developer's HOME or provider environment variables).
- `python scripts/build_agents.py --check`
- `git diff --check`
- Forward-test material workflow changes with a fresh agent on a small real task.
