# Lemmings orchestration guide

Lemmings applies only the lifecycle needed by the risk. Simple work uses one agent and no artifacts. Standard work uses one mutable task packet, a candidate commit, narrow validation, and immutable Sol review. Strict work adds a phase baseline, frozen contracts, isolated worktrees, leases, and integration evidence.

Require an independent plan review only for a Strict phase, a frozen public contract, or an explicit user request.

A Strict phase is dispatchable only after `baselineReview` is Accepted by `gpt-5.6-sol:high` and references immutable evidence binding `phaseId` and a real `baselineSha`. `lemmings phase prepare` parses that artifact and otherwise leaves the review Planned.

## Pipeline and states

The orchestrator follows `Discover -> Plan -> Refine -> Implement -> Verify`.

| Step | Orchestrator responsibility | Exit condition |
|---|---|---|
| Discover | Bound relevant code, baseline, constraints, dependencies, risks, and unknowns | Enough evidence exists to plan without broad exploration |
| Plan | Define goal, non-goals, acceptance, ownership, models, dependencies, integration order, and tests | The selected mode has an implementable plan |
| Refine | Challenge assumptions, resolve blockers, perform required plan review, and freeze shared contracts | The task is Ready |
| Implement | Dispatch Ready work, make owned changes, validate narrowly, and create candidate/fix commits | Candidate evidence is complete |
| Verify | Validate, review the actual range, repair findings, integrate, and rerun integration checks | Work is Accepted or Integrated, or explicit debt/replan is recorded |

Do not dispatch an implementation worker before Refine declares the task Ready. A bounded finding loops from Verify to Implement and back. Invalid scope, baseline, or contracts restart the earliest affected step; the second failed review uses Replan Required.

The existing internal stages remain unchanged: Prepare contains Discover, Plan, and Refine; Dispatch plus Execute/Candidate implements; Review/Repair plus Integrate/Close verifies. Dispatch is derived from Ready tasks and retained only as an audit event. Handoff lives under `task.execution`; integration evidence lives under `task.close` or `phase.close`.

States are Planned, Ready, Active, Candidate, Accepted, and Integrated. Exceptions are Blocked, Replan Required, Cancelled, and Superseded. A ChangesRequested review leaves the task Candidate and advances `review.cycle`. After the second failed review, use Replan Required.

Accepted means the immutable review approves the current candidate/fix head. Integrated additionally requires a merge commit and passing integration validation.

## Models

The orchestrator and reviewer use `gpt-5.6-sol:high`. Complex workers use `gpt-5.6-sol:medium`. A task records `models.requested`, `models.assigned`, and `models.actual`. A user pin wins. If actual differs from assigned, it must be allowed by the consumer profile and have `fallbackReason`.

## Hooks

PreToolUse validates dispatch/model/worktree binding and exact path ownership. Reviewer, explorer, and summarizer profiles are read-only; validators may run only declared validation commands and may not patch. Hook denial applies whenever the host exposes role identity but does not replace the sandbox. Known static read-only shell commands are allowed; script blocks, substitution, dynamic evaluation, and unknown write-sets warn in Standard and block in Strict. Repository checks compare the actual `baseSha..head` Git diff with owned/shared/forbidden rules. SubagentStart injects bounded context; SubagentStop requires role-appropriate evidence. There is no Stop continuation.

Repo consumers install the Lemmings plugin through their marketplace. The plugin auto-discovers `hooks/hooks.json`; do not copy the hook configuration into consumer `.codex` state because duplicate registration executes every hook twice.

## CLI

Use `lemmings status` and `lemmings check` for routine work. Worktrees are managed through `lemmings worktree allocate|inspect|release`. Strict preparation uses `lemmings phase prepare` and `lemmings wave plan`; final integration uses `lemmings close`. `lemmings scorecard` creates output only for a benchmark or at least two observations.
