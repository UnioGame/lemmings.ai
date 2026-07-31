# AGENTS.md

## Scope

- This repository is the Lemmings Unity package and Codex plugin for reusable AI-agent workflows.
- Keep the Python CLI, hooks, orchestration guidance, task templates, and repo-scoped skill here.
- Do not add Unity Runtime or Editor assemblies unless a separate task explicitly requires them.

## Sources Of Truth

- `Documentation~/guides/lemmings.md` owns the detailed orchestration policy.
- `skills/lemmings/SKILL.md` owns the concise agent workflow and command contract.
- `skills/lemmings/references/` owns compact advanced workflow references.
- `Documentation~/tasks/ROADMAP.md` owns orchestration-tooling priorities.
- `Documentation~/tasks/templates/` owns reusable phase, task, review, and integration schemas.

Avoid duplicating detailed policy across these files. Keep the skill compact and link it to the guide for advanced cases.

## Working Rules

- Preserve user model assignments. Never silently substitute a requested model or reasoning effort.
- Default the orchestrator to `gpt-5.6-sol` with `high` reasoning. Use a higher effort only when the user explicitly requests it.
- Use the lowest-cost model that safely fits a role when the user has not pinned one.
- Apply Simple, Standard, or Strict contracts proportionally. Parallel and Strict writers require isolated worktrees; one writer owns one worktree.
- Map acceptance criteria and material risks to tests.
- Keep roadmap and progress notes concise; never paste raw logs.
- Treat security, sandbox, approval, and destructive-action rules as non-disableable.

## Validation

- Run the skill creator `quick_validate.py` against every changed skill.
- Check `package.json`, Markdown links, and `git diff --check`.
- Forward-test material skill changes with fresh agents and minimal task-local context.
