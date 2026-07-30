# Models and states

## Defaults

| Role | Preferred | Approved fallback |
| --- | --- | --- |
| Orchestrator | `gpt-5.6-sol/high` | none below high |
| Reviewer | `gpt-5.6-sol/high` | none |
| Complex Worker | `gpt-5.6-sol/medium` | none unless Sol approves |
| Worker | `gpt-5.6-terra/medium` | `gpt-5.6-sol/medium` |
| Mechanical/Explorer | `gpt-5.6-terra/low` | `gpt-5.6-terra/medium` |
| Validator | `gpt-5.6-terra/medium` | `gpt-5.6-sol/medium` |

Use an approved fallback only after availability checking. Preserve user pins; if a pin is unavailable, block only that spawn and request an explicit replacement. `selected` is the pre-dispatch available assignment; `actual` comes from runtime/handoff and must match selected unless Sol records an approved exception.

## State transition rules

`Planned` becomes `Ready` only after Planning Gate. `Ready` becomes `Dispatched` only after the baseline, contract freeze, and dispatch gates. `Candidate` requires a candidate commit, handoff, and task validation. `Accepted` requires an independent Review Gate. `Integrated` requires a merge plus Integration Gate. A second unsuccessful repair cycle forces `Replan Required`.
