# <TASK-ID>: <title>

## Delivery metadata

| Field | Value |
| --- | --- |
| Phase / wave / state | `<phase> / <wave> / Planned>` |
| Base SHA / branch / absolute worktree | `<sha> / <branch> / <path>` |
| Preferred / approved fallback / selected / actual | `<model> / <model> / <model> / pending>` |
| Role and rationale | `<role/reason>` |
| Integration order | `<n>` |

## Goal, non-goals, inputs, frozen decisions

- Goal: `<one outcome>`
- Non-goals: `<excluded>`
- Inputs/frozen decisions: `<links>`

## Ownership and dependencies

- Read set: `<paths/symbols>`
- Write set: `<paths>`
- Generated set: `<paths or none>`
- Shared set / owner: `<paths / Sol>`
- Forbidden paths: `<paths>`
- Dependencies / resource lease: `<IDs / lease>`

## Requirements and acceptance

- Public contracts: `<contract/compatibility>`
- Implementation requirements: `<bounded steps>`
- Acceptance criteria: `<observable checks>`

| Risk / criterion | Narrow validation | Evidence | Integration rerun |
| --- | --- | --- | --- |
| `<risk>` | `<command/environment>` | `<expected>` | `yes/no` |

## Failure and control

- Failure/retry/cancellation/cleanup/idempotency: `<cases>`
- Stop conditions: `<scope, contract, resource, paid action>`
- Paid/external budget: `<ceiling or prohibited>`
- Rollback: `<strategy>`
- Handoff/review required: `candidate commit; Sol High commit-range review`
