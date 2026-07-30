---
name: orchestrate-agent-tasks
description: Coordinate multi-step repository work with phase baselines, frozen contracts, parallel waves, isolated worktrees, model routing, candidate commits, independent Sol review, integration gates, and validation evidence. Use for multi-agent implementation, parallel repository work, worktree allocation, subagent model assignment, task/phase orchestration, or `$orchestrate-agent-tasks` controls.
---

# Orchestrate Agent Tasks

Use this workflow for material or multi-agent work; keep simple, local edits single-agent.

## Controls and model routing

Accept `on`, `off`, `auto`, and `status` after `$orchestrate-agent-tasks`. Mode is thread-local; `off` disables implicit orchestration, never direct user instructions or safety rules.

Accept `models set`, `models task <ID>`, `models status`, and `models reset` assignments in `<model>:<effort>` form. User pins win over task, thread, default, and cost routing, subject to availability and safety. Never silently replace an unavailable pin.

Record distinct `preferred`, `approved fallback`, `selected`, and `actual` model/effort. Keep Reviewer fixed at `gpt-5.6-sol/high`; it is read-only and cannot repair worker code. Defaults and the exact fallback policy are in [models and states](references/models-and-states.md).

## Lifecycle

For a phase, create a reviewed baseline before parallel work. The phase owns an integration branch, reviewed base SHA, frozen public/shared contracts, shared-path owner, resource gates, validation, and rollback point.

Use this task state machine:

```text
Planned -> Ready -> Dispatched -> In Progress -> Candidate -> Sol Review
Changes Requested -> Candidate -> Accepted -> Integrated
```

`Blocked`, `Cancelled`, and `Superseded` are exceptional states. Move to `Replan Required` after two failed review/fix cycles, a changed frozen contract, scope drift, or a false baseline. `Accepted` is not `Integrated`.

Pass these gates in order: Planning, Baseline, Contract Freeze, Dispatch, Candidate, Review, Integration, Cleanup, Phase Close. Do not dispatch a parallel wave until the first three are accepted.

## Plan and dispatch

1. Read applicable `AGENTS.md`, the consumer profile, roadmap, and direct owning files.
2. Inspect dirty state, branch, submodules, validation surfaces, and external/paid constraints.
3. Build a dependency DAG and create one task packet per independently reviewable outcome.
4. Freeze phase contracts and allocate branches/worktrees. Parallel writers need unique worktrees and non-overlapping owned paths.
5. Generate a dispatch manifest from packets and profile; reject manual drift, missing gates, resource conflicts, or unavailable selected models.
6. Send only a bounded context packet. Permit one focused expansion request per worker; reject broad transcript/doc dumps.

Use the artifacts in [templates](references/templates.md) and enforcement rules in [workflow](references/workflow.md).

## Candidate, review, and integration

An implementation worker must create a candidate commit and handoff. Repairs remain with the original worker as separate fix commits. The handoff records the actual model, commit range, validations, validation debt, loaded context, cost, latency, assumptions, and risks.

Give Sol High the packet, baseline/contracts, actual candidate/fix commit range, diff, concise evidence, and direct interfaces/tests. Findings P0–P2 block acceptance unless a P2 follow-up has an owner. The reviewer must not patch worker code.

Integrate accepted commits only in dependency order using the profile integration strategy (normally `--no-ff`), then run phase validation. Record integration evidence, cleanup inventory, deferred validation, and routing scorecard. Inspect cleanup safely; do not delete worktrees automatically.

## Safety and validation

- Serialize shared contracts, generated outputs, Unity hotspots, submodules, ports, devices, accounts, and mutable external state.
- Keep hooks inactive until `orchestration_cli runtime activate --state <json>`
  creates the worktree-specific Git marker. Deactivate it after the task; hook
  installation alone is not an active task contract.
- Stop on scope/ownership drift, unapproved paid actions, missing mandatory external gate, or public-contract changes outside the shared-contract owner.
- Map every acceptance criterion and material risk to a narrow falsifying check, then widen at integration.
- Record unavailable runtime validation as validation debt with a reason, owner, blocking policy, and future gate; never hide it as a pass.
- Do not load generated output, logs, videos, screenshots, dumps, or full transcripts without a concrete need.

Read [workflow](references/workflow.md) for edge cases, hooks, worktree policy, and phase scorecards. Read [templates](references/templates.md) before creating or updating artifacts.
