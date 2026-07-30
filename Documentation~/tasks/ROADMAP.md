# AI Tools Task Roadmap

Roadmap хранит только приоритет, зависимости и краткий delivery status. Детали исполнения находятся в phase/task artifacts.

## Status flow

```text
Planned -> Ready -> Dispatched -> In Progress -> Candidate -> Sol Review
Changes Requested -> Candidate -> Accepted -> Integrated
```

`Blocked`, `Replan Required`, `Cancelled`, `Superseded` — исключения. WIP: максимум 3 agents, 2 writers; только Orchestrator меняет roadmap.

| ID | Priority | Status | Outcome | Dependencies |
| --- | --- | --- | --- | --- |
| ORCH-005 | P1 | Done | Unified lifecycle: phase/wave, gates, states and candidate semantics | ORCH-001 |
| ORCH-006 | P1 | Done | Artifact contracts and templates | ORCH-005 |
| ORCH-007 | P1 | Done | Generic profile and AutoQA adapter contract | ORCH-006 |
| ORCH-008 | P2 | Ready | Validator/status: dependencies, paths, models, drift and validation debt | ORCH-007 |
| ORCH-009 | P2 | Ready | Worktree reconciliation and safe cleanup inspection | ORCH-008 |
| ORCH-010 | P2 | Ready | Hybrid hooks and custom profiles | ORCH-009 |
| ORCH-011 | P1 | In Progress | AutoQA workspace pilot: repo-scoped plugin, adapter and docs gate | ORCH-010 |
| ORCH-012 | P3 | Planned | Evals, hook fixtures and routing benchmark | ORCH-008 |

`Done` requires accepted review, integrated commit, phase validation, and recorded remaining validation debt.
