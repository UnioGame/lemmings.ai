# AGENTS.md

## Scope

- This repository is the Lemmings Unity package and Codex plugin for reusable AI-agent workflows.
- Keep the Python CLI, hooks, orchestration guidance, task templates, and repo-scoped skill here.
- Do not add Unity Runtime or Editor assemblies unless a separate task explicitly requires them.

## Sources Of Truth

- `skills/lemmings/SKILL.md` owns the workflow, task controls, and sole-orchestrator contract.
- `skills/lemmings/references/` owns game-project, artifact-contract, and telemetry policy.
- `Documentation~/tasks/ROADMAP.md` owns orchestration-tooling priorities.
- `Documentation~/tasks/templates/` owns reusable phase, task, and immutable review contracts; close evidence remains embedded in the phase or task contract.

Avoid duplicating detailed policy across these files. Keep the skill compact and link to the owning reference for advanced cases.

## Working Rules

- The smart Lemmings skill is the only orchestrator. Do not split orchestration policy into command or workflow reference files.
- Preserve user model assignments. Never silently substitute a requested model or reasoning effort.
- Default the orchestrator to `gpt-5.6-sol` with `high` reasoning. Use a higher effort only when the user explicitly requests it.
- Use the lowest-cost model that safely fits a role when the user has not pinned one.
- Apply Simple, Standard, or Strict contracts proportionally. The default workspace policy is hybrid: safe serial work may use current, while parallel writers and dirty-primary isolation require separate worktrees; one writer owns one isolated worktree.
- For game projects, select a code worktree, package worktree, or Unity clone from the affected scope. The pipeline, subagents, current checkout, and Git worktrees need no special user approval. Ask only before creating a full Unity clone estimated above 10 GiB; after refusal, use a worktree or safe serial current work and block only a step with no safe fallback.
- Keep only Task, Phase, and immutable Review files as canonical artifacts. Embed handoff, validation, and integration evidence in their owning artifact.
- Map acceptance criteria and material risks to tests.
- Keep roadmap and progress notes concise; never paste raw logs.
- Treat security, sandbox, approval, and destructive-action rules as non-disableable.

## Validation

- Run the skill creator `quick_validate.py` against every changed skill.
- Check `package.json`, Markdown links, and `git diff --check`.
- Forward-test material skill changes with fresh agents and minimal task-local context.
