# <TASK-ID>: Sol review cycle <n>

| Field | Value |
| --- | --- |
| Commit range | `<base..candidate/fix>` |
| Reviewer | `gpt-5.6-sol/high` |
| Verdict | `Accepted/Changes Requested/Replan Required` |
| Integration decision | `<approve/block/follow-up>` |

- Evidence reviewed: `<packet, baseline, diff, validation, direct interfaces/tests>`
- Independent falsifying validation: `<command/result>`
- Compatibility/dependency/license/ownership/unrelated-change checks: `<result>`

| Severity | File/line | Evidence | Required bounded fix / follow-up owner |
| --- | --- | --- | --- |
| `P0-P3` | `<location>` | `<fact>` | `<fix>` |

P0–P2 block unless a P2 follow-up has an explicit owner. Reviewer does not patch task code.
