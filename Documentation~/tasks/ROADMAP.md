# AI Tools Task Roadmap

Единый реестр приоритетов для развития `unigame.ai.tools`.

## Priority

- `P0` — production/security/blocker.
- `P1` — текущая обязательная цель.
- `P2` — следующая очередь.
- `P3` — improvement или experiment.
- `Inbox` — задача ещё не прошла triage.

## Status Flow

```text
Draft -> Plan Review -> Ready -> In Progress -> Code Review -> Integration -> Done
```

`Blocked` допустим из любого активного статуса.

## WIP

- Максимум три активных субагента.
- Максимум два параллельных writers.
- Один writer на worktree.
- Только Orchestrator меняет этот roadmap.

## Tasks

| ID | Priority | Status | Task | Assignment Summary | Isolation | Dependencies | Parallel Group | Shared Resources | Merge Order | Updated |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| ORCH-001 | P1 | In Progress | [Bootstrap orchestration toolkit](ORCH-001-bootstrap-orchestration-toolkit.md) | `O/R=sol:high; forward-tests=terra:low\|medium+sol:medium` | single-writer | — | — | Git remotes | 1 | 2026-07-30 |
| ORCH-002 | P2 | Inbox | Add task/roadmap schema and linter | `O/R=sol:high; W=sol:medium` | isolated-worktree | ORCH-001 | quality-automation | CI | 2 | 2026-07-30 |
| ORCH-003 | P2 | Inbox | Add orchestration contract and forward-test CI | `O/R=sol:high; W=sol:medium` | isolated-worktree | ORCH-002 | quality-automation | CI/model catalog | 3 | 2026-07-30 |
| ORCH-004 | P3 | Inbox | Benchmark token, cost, latency, and repair rate | `O/R=sol:high; V=terra:medium` | isolated-worktree | ORCH-001 | measurement | model catalog | 4 | 2026-07-30 |

## Queue Rules

- Не угадывать product priority.
- Task-specific user model assignment имеет приоритет над role default.
- Недоступный user-pinned model блокирует spawn, но не меняется молча.
- Dependency и shared resource должны быть разрешены до `Ready`.
- `Done` требует закрытого review и записанного post-merge test evidence.
