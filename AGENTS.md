# AGENTS.md

## Scope

- This repository is a documentation-only Unity package for reusable AI-agent workflows.
- Keep orchestration guidance, task templates, and repo-scoped skills here.
- Do not add Unity Runtime or Editor assemblies unless a separate task explicitly requires them.

## Sources Of Truth

- `Documentation~/guides/subagent-task-orchestration.md` owns the detailed orchestration policy.
- `.agents/skills/orchestrate-agent-tasks/SKILL.md` owns the concise agent workflow and command contract.
- `Documentation~/tasks/ROADMAP.md` owns orchestration-tooling priorities.
- `Documentation~/tasks/task-template.md` owns the reusable task-file schema.

Avoid duplicating detailed policy across these files. Keep the skill compact and link it to the guide for advanced cases.

## Working Rules

- Preserve user model assignments. Never silently substitute a requested model or reasoning effort.
- Default the orchestrator to `gpt-5.6-sol` with `high` reasoning. Use a higher effort only when the user explicitly requests it.
- Use the lowest-cost model that safely fits a role when the user has not pinned one.
- Parallel writers require isolated worktrees. One writer owns one worktree.
- Map acceptance criteria and material risks to tests.
- Keep roadmap and progress notes concise; never paste raw logs.
- Treat security, sandbox, approval, and destructive-action rules as non-disableable.

## Validation

- Run the skill creator `quick_validate.py` against every changed skill.
- Check `package.json`, Markdown links, and `git diff --check`.
- Forward-test material skill changes with fresh agents and minimal task-local context.
