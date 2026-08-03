# Workflow

## Modes

Simple uses no artifacts. Standard requires a task packet, candidate/fix commits, validation or owned debt, and immutable Sol review. Strict adds an accepted baseline, frozen contracts, non-overlapping ownership, unique worktrees, resource leases, and close evidence.

Strict activates for parallel writers, shared contracts, Unity serialized assets, submodules, code generation, or external resources.

Before a Strict wave, require `baselineReview.status: Accepted`, reviewer model `gpt-5.6-sol:high`, and immutable JSON evidence whose `phaseId` and real `baselineSha` bind to the phase. Preparing a phase must parse that evidence and must not self-attest acceptance.

Require independent plan review only for a Strict phase, a frozen public contract, or an explicit user request.

## Lifecycle

The orchestrator-facing lifecycle is:

`Discover -> Plan -> Refine -> Implement -> Verify`

1. **Discover** exits when the relevant code paths, baseline, constraints, dependencies, material risks, and blocking unknowns are known well enough to plan bounded work.
2. **Plan** exits when goal, non-goals, acceptance criteria, ownership, models, dependencies, integration order, and risk-to-test mapping are explicit at the detail required by the selected mode.
3. **Refine** exits with `Ready` only after assumptions and blocking unknowns are resolved, required plan review is accepted, shared contracts are frozen when applicable, and the plan is implementable without inventing policy.
4. **Implement** exits with a real Candidate containing owned changes, focused validation or owned debt, actual model, embedded handoff, and candidate/fix commits.
5. **Verify** exits with Accepted or Integrated evidence after validation and immutable review of the actual candidate range. Integration requires the merge and integration validation.

Do not dispatch an implementation worker before Refine exits Ready. A bounded verification finding returns to Implement and then Verify. A changed scope, invalid baseline or contract, or second failed review enters Replan Required and restarts at the earliest invalidated lifecycle step.

Internal execution stages remain compatible with the CLI and contracts:

| Orchestrator lifecycle | Internal stages |
|---|---|
| Discover, Plan, Refine | Prepare |
| Implement | Dispatch, Execute/Candidate |
| Verify | Review/Repair, Integrate/Close |

Simple compresses Discover, Plan, and Refine into bounded reasoning and creates no artifacts. Standard records the cycle in one task packet. Strict adds phase baseline and contract evidence without changing the five orchestrator steps.

When optional telemetry is basic or full, call `lemmings metrics stage <step>` as each orchestrator lifecycle step begins. Entering a step closes the preceding interval. `lemmings close` ends Integrated Standard/Strict work; otherwise call `lemmings metrics finish`. Do not infer missing task bindings: unbound hook events remain explicit incomplete evidence.

Task states are `Planned -> Ready -> Active -> Candidate -> Accepted -> Integrated`, with `Blocked`, `Replan Required`, `Cancelled`, and `Superseded` exceptions. Dispatch is an audit event. Review remains nested in Candidate until Accepted. A second failed review requires Replan Required.

## Safety

Run writers in isolated worktrees for Strict or parallel delivery. One writer owns one worktree. Map every material risk to a test or explicit debt with reason, owner, future gate, and blocking status. Reviewers are read-only. Known read-only shell commands pass; unknown shell writes warn in Standard and block in Strict. No Stop continuation hook exists.
