# Contracts

All JSON artifacts use `schemaVersion: 1`.

- Roadmap stores only priority and dependencies.
- Strict Phase stores baseline, frozen contracts, workspace choice, leases, and close evidence.
- Task stores plan, ownership, execution/handoff, implementation attempts, commits, model assignment, validation/debt, all review references, aggregate quality, and close evidence.
- Review is separate immutable evidence for the exact candidate/fix head.
- `telemetryCohort` is optional. Set it only for tasks intended to be compared under the same delivery/risk class.

Only Task, Phase, and immutable Review files are canonical. Do not create tracked dispatch, separate handoff, separate integration evidence, adapters, or compatibility fields; embed that evidence in its owning Task or Phase. Request benchmark evidence through `lemmings metrics report --benchmark` only when observations are genuinely comparable.

## Lifecycle states

```text
Planned → Ready → Active → Candidate → Accepted → Integrated
```

`Blocked`, `Replan Required`, `Cancelled`, and `Superseded` are exceptional states. Dispatch is an audit event, not a state. A requested repair keeps the task at `Candidate` while a new fix head and Review cycle are recorded. The second `ChangesRequested` verdict requires `Replan Required`. `Accepted` binds the latest reviewed head; `Integrated` additionally requires a merge commit and passing integration validation.

## Canonical shapes

A Task owns `goal`, `acceptance`, `dependencies`, `mode`, `state`, `role`, `risks`, `ownership`, `models`, `workspace`, `execution`, `baseSha`, `commits`, `validation`, `reviewRef`, `reviewHistory`, `qualitySummary`, and `close`. Standard and Strict implementation produces a candidate commit; fix commits form a descendant chain from it. Each `execution.attempts[]` item records `attempt`, `kind` (`candidate` or `fix`), `actualModel`, `headSha`, `validationFailures`, `reviewRef`, and `reviewStatus`.

A Strict Phase owns `phaseId`, `baselineSha`, `integrationBranch`, `contractsFrozen`, `contracts`, `baselineReviewRef`, `taskDag`, `leases`, and `close`. The task DAG contains task IDs and dependencies; missing nodes and cycles block dispatch.

Review uses one immutable schema for both subjects:

```json
{
  "schemaVersion": 1,
  "reviewId": "REVIEW-17",
  "subject": {
    "kind": "candidate",
    "taskId": "TASK-17",
    "baseSha": "<sha>",
    "headSha": "<sha>"
  },
  "reviewerModel": "gpt-5.6-sol:high",
  "status": "Accepted",
  "cycle": 1,
  "findings": [{"priority":"P2","origin":"implementation","summary":"<kept only in Review>"}],
  "validation": []
}
```

A baseline subject replaces the task range with `{"kind":"baseline","phaseId":"PHASE-2","sha":"<sha>"}`. Legacy embedded reviews and separate baseline/candidate schemas are invalid.

Task workspace overrides record `auto`, `current`, or `isolated`, its resolved strategy, safety rationale, estimated size, and any permission decision. An isolated strategy is `code-worktree`, `package-worktree`, or `unity-clone`; each requires explicit approval before provisioning when its estimated workspace exceeds 10 GiB. Current checkout uses `approval: not-required`; an oversized backend awaiting a decision records `pending` without a path. That gate does not block lifecycle preparation or read-only roles. A safe sequential fallback records `backend: current`, `approval: not-required`, and names the pending or declined workspace in `reason`; after refusal with no safe implementation or validation fallback, retain the requested isolated backend without a path, record `approval: declined`, and set the owning Task to `Blocked`.

Model fields live under `models`: `requested` is an optional user pin, `assigned` is fixed before spawn, `actual` records execution, and `fallbackReason` is required only when actual differs from assigned.

Candidate tasks require `baseSha`, an actual model, embedded handoff, and real candidate/fix Git commits. Each fix descends from the previous commit and the candidate descends from `baseSha`; a task branch need not be the orchestrator's current HEAD. Accepted tasks use `reviewRef` to bind an immutable candidate Review whose `subject.taskId`, `baseSha`, and `headSha` match the current Task. A Strict Phase similarly uses `baselineReviewRef` for a baseline Review whose subject binds its phase and SHA. Base must differ from and be an ancestor of candidate head. Evidence paths remain inside the repository. Strict checks also validate actual commit-range paths against ownership.

Consumer defaults live in `profile.models`. Explicit repo pins live in `profile.requestedModels`; task-role pins live in `profile.taskModels`. Resolve assignment as task pin, then the only automatic worker ladder `gpt-5.6-luna:max` → `profile.workerPolicy.elevatedModel` (`gpt-5.6-terra:max`). Copy an effective pin into both task `models.requested` and `models.assigned`. Sol Medium/High/Max are explicit worker pins only; historical Sol Medium packets remain valid. Plan/contract defects return to Refine without a worker-model escalation. Reviewer and orchestrator remain `gpt-5.6-sol:high`; validator and explorer use `gpt-5.6-luna:high`; summarizer uses `gpt-5.6-luna:medium`.

`reviewHistory` retains every immutable candidate Review reference; `reviewRef` remains the final accepted pointer. Findings carry `priority` P0-P3 and `origin` `implementation`, `plan-contract`, `validation`, or `integration`. `qualitySummary` contains only derived counts and model/outcome fields, never finding text. Missing new fields and unclassified findings mark an artifact legacy/incomplete without invalidating schema version 1.

Telemetry events are derived local state under the Git common directory, not canonical task artifacts. Raw events are never tracked automatically. Quality imports bind task ID and `baseSha..headSha`; regression evidence requires a recorded Integrated event. Only explicit sanitized report exports may be committed.
