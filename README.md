# Lemmings

Lemmings plugin for repository orchestration

## Install and run

Install the Python package in editable mode to expose the single CLI executable:

```text
python -m pip install -e .
lemmings --help
```

Use `$lemmings` from `skills/lemmings/SKILL.md`. A consumer stores its profile at `.codex/lemmings.json`; runtime activation is repository-scoped under the Git common directory at `lemmings/active.json`.

The normal completion check is `lemmings check`. Use `lemmings check --all` for the full Strict lifecycle. See [the guide](Documentation~/guides/lemmings.md) for contracts and examples.
