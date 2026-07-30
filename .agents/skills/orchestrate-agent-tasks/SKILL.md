---
name: orchestrate-agent-tasks
description: Plan, delegate, review, test, and integrate multi-step repository tasks through cost-aware Codex subagents, user-selectable model routing, task roadmaps, decision-complete task files, isolated worktrees, and risk-based test gates. Use for multi-agent implementation, parallel repository work, subagent model assignment, token optimization, or orchestration on/off/auto/status control.
---

# Orchestrate Agent Tasks

Coordinate repository work without turning delegation into extra noise or cost.

## Handle Control Commands

Interpret these exact commands before normal orchestration:

```text
$orchestrate-agent-tasks on
$orchestrate-agent-tasks off
$orchestrate-agent-tasks auto
$orchestrate-agent-tasks status
```

- Treat a new thread as `auto`.
- Keep `on`, `off`, and `auto` state in the current thread only.
- In `on`, apply this workflow to multi-step work but keep simple work single-agent.
- In `off`, do not implicitly create task files or subagents. Obey later direct user instructions.
- In `auto`, activate when the request matches the skill description.
- In `status`, report mode, orchestrator model and effort, subagent limits, roadmap, and active task. Do not mutate state.
- Never let a mode command bypass security, sandbox, approval, or destructive-action rules.

Do not invent a `/orchestration` slash command. Use explicit `$orchestrate-agent-tasks` invocation.

## Handle Model Commands

Accept:

```text
$orchestrate-agent-tasks models set worker=gpt-5.6-sol:medium
$orchestrate-agent-tasks models set explorer=gpt-5.6-terra:low validator=gpt-5.6-terra:medium
$orchestrate-agent-tasks models task ORCH-017 worker=gpt-5.6-sol:medium
$orchestrate-agent-tasks models status
$orchestrate-agent-tasks models reset
$orchestrate-agent-tasks models reset worker
$orchestrate-agent-tasks models reset task ORCH-017
```

Use `<model>:<reasoning-effort>` only as compact input. Store model and effort separately.

Support user assignments for `complex-worker`, `worker`, `mechanical-worker`, `explorer`, `validator`, and `summarizer`. Let `workers=` address all worker roles. Do not support `all=`.

Keep the mandatory independent Reviewer fixed at `gpt-5.6-sol/high`. Reject attempts to change `reviewer=` and explain that the user may request an additional advisory agent, but it cannot replace the mandatory quality gate.

Apply precedence:

1. System, admin, surface availability, and safety constraints.
2. Latest direct user instruction for the spawn.
3. Task-specific user override.
4. Thread role override.
5. Skill default.
6. Automatic cost-based routing.

Preserve partial overrides. Keep unspecified roles at their existing values. Record every effective model, effort, source, and scope in the task file and a compact assignment summary in the roadmap.

Validate the model and effort immediately before spawn. Never silently replace an unavailable user pin. Block that spawn, state the exact incompatibility, and suggest the closest available option. Continue independent tasks with valid assignments.

Build the suggestion deterministically, but apply it only after user confirmation:

1. If the model exists but the effort does not, keep the model and suggest the nearest supported effort at or above the requested level; otherwise use its highest supported effort.
2. If the model does not exist, use the role-default model with the requested effort when compatible.
3. Otherwise suggest the complete role-default assignment.

Keep the orchestrator on `gpt-5.6-sol` with `high` reasoning by default. Raise it only when the user explicitly requests `xhigh`, `max`, or `ultra`. Treat an unspecified request for "above high" as `xhigh`. Do not lower the orchestrator below `high`.

Default roles:

| Role | Model | Effort |
| --- | --- | --- |
| Reviewer | `gpt-5.6-sol` | `high` |
| Complex Worker | `gpt-5.6-sol` | `medium` |
| Worker | `gpt-5.6-terra` | `medium` |
| Mechanical Worker | `gpt-5.6-terra` | `low` |
| Explorer | `gpt-5.6-terra` | `low` |
| Validator | `gpt-5.6-terra` | `medium` |
| Summarizer | `gpt-5.6-luna` when available, otherwise Terra | `low` |

## Run The Workflow

1. Read applicable `AGENTS.md`, the current roadmap, and the closest owning code or docs.
2. Inspect dirty state, submodules, current branch, and available validation surfaces.
3. Keep simple local work single-agent.
4. For material unknowns, delegate bounded read-only questions to one or two cheap explorers.
5. Build a dependency DAG. Parallelize only independent nodes.
6. For material or multi-agent work, create one decision-complete task file per independently reviewable result. Skip task files for simple single-agent edits.
7. Record base revision, ownership, read/write/generated sets, resources, dependencies, acceptance criteria, risks, tests, rollback, and merge order.
8. Have an independent Sol High reviewer check the plan before implementation.
9. Use one writer per worktree. Never run concurrent writers in the same checkout.
10. Dispatch the cheapest safe worker unless the user pinned a model.
11. Require the worker to add or update tests with the behavior and return concise evidence.
12. Have the reviewer inspect task, diff, and validation evidence without editing code.
13. Return findings to the original worker. Stop after two failed repair loops and re-plan.
14. Integrate task commits serially in DAG order.
15. Re-run integration tests after merge.
16. Update roadmap, final task status, and stable knowledge.

## Control Parallelism

- Default to at most three subagents and two parallel writers.
- Permit multiple read-only agents in one checkout.
- Require isolated worktrees for concurrent writers.
- Serialize changes that share source files, generated outputs, contracts, scenes, prefabs, `.meta` files, asmdefs, configs, Addressables groups, submodules, ports, devices, accounts, or mutable external state.
- Stop a worker that expands beyond its declared write set.
- Prohibit recursive spawn unless the orchestrator explicitly authorizes it.

Read the [full orchestration guide](../../../Documentation~/guides/subagent-task-orchestration.md) before planning concurrent writers, Unity asset work, submodule work, migrations, or platform validation.

## Require Risk-Based Tests

Map each acceptance criterion and material risk to an automated test or an explicitly justified manual validation. Prefer the narrowest test that can falsify the change, then widen by integration risk.

- Require regression tests for reproducible bug fixes.
- Cover success, boundary, failure, cancellation, retry, cleanup, and idempotency where relevant.
- Validate Unity serialization, assets, platform guards, and builds at their owning layer.
- Treat flaky tests as unresolved after one diagnostic rerun.
- Re-run cross-task tests after integration.
- Do not replace requirement coverage with a global line-coverage percentage.

Use the [task template](../../../Documentation~/tasks/task-template.md) and update the [roadmap](../../../Documentation~/tasks/ROADMAP.md).

## Keep Context Lean

- Use targeted search and exact file reads.
- Pass task files and narrow context packets, not full chat history.
- Return summaries instead of raw logs.
- Keep explorer results to about twelve evidence points, worker handoffs to about eight, and reviewer output to findings only.
- Reuse the original worker for repairs.
- Avoid delegation when coordination costs exceed expected benefit.
