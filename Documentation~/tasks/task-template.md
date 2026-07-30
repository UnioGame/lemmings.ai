# <TASK-ID>: <Название>

## Metadata

| Field | Value |
| --- | --- |
| Roadmap ID | `<TASK-ID>` |
| Priority | `P0/P1/P2/P3/Inbox` |
| Status | `Draft` |
| Risk | `Low/Medium/High/Critical` |
| Base revision | `<commit>` |
| Submodule revisions | `<path: commit or —>` |
| Worktree/branch | `<path / branch>` |
| Updated | `<YYYY-MM-DD>` |

## Agent Assignments

Record every effective role assignment. The mandatory Reviewer remains `gpt-5.6-sol/high`.

| Role / Scope | Model | Effort | Source | Command | State |
| --- | --- | --- | --- | --- | --- |
| Orchestrator / task | `gpt-5.6-sol` | `high` | `skill-default` | `—` | `effective` |
| Reviewer / mandatory gate | `gpt-5.6-sol` | `high` | `required-invariant` | `—` | `effective` |
| `<worker/explorer/validator scope>` | `<model>` | `<effort>` | `skill-default/user-thread/user-task/direct-user` | `<command or —>` | `effective/blocked/pending` |

## Goal

Один проверяемый результат.

## Non-Goals

- Явно исключённое поведение.

## Current Evidence

- Фактические owning files/symbols.
- Текущее поведение.
- Подтверждённые ограничения.

## Decisions

- Зафиксированные product и architecture решения.
- Public API/schema/serialization решения.

## Ownership

### Read Set

- `<path/symbol>`

### Write Set

- `<path>`

### Generated Set

- `<path or —>`

### Shared Resources

- `<port/device/account/backend/editor or —>`

### Resource Lease

- `<owner/window or —>`

## Dependencies And Merge Order

- Depends on: `<task IDs or —>`
- Blocks: `<task IDs or —>`
- Parallel group: `<group or —>`
- Merge order: `<number>`

## Implementation Plan

- [ ] Step with exact behavior and owning boundary.
- [ ] Add or update tests.
- [ ] Run task-level validation.
- [ ] Complete independent review.
- [ ] Integrate and run post-merge validation.

## Acceptance Criteria

- [ ] Observable criterion.

## Risk-To-Test Matrix

| Risk / Criterion | Test Level | Scenario | Command / Environment | Expected Evidence | Post-Merge Rerun |
| --- | --- | --- | --- | --- | --- |
| `<risk>` | `<unit/integration/playmode/manual>` | `<scenario>` | `<command>` | `<result>` | `yes/no` |

## Failure Modes

- Failure, cancellation, retry, cleanup, idempotency, compatibility, or platform cases.

## Rollback

- Recoverable rollback steps.
- Data/content compatibility requirements.

## Progress

- Concise status only. Do not paste raw logs.

## Validation Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| `<check>` | `Pass/Fail/Blocked` | `<summary/artifact>` |

## Handoff

- Changed files.
- Decisions applied.
- Checks run.
- Remaining risk.

## Reviewer Findings

- Blocking findings or `None`.

## Final Notes

- Final state, integrated commit, and remaining manual validation.
