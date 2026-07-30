# Workflow and enforcement

## Gates

1. Planning: goal, scope, dependencies, ownership, acceptance and risks are complete.
2. Baseline: branch, SHA, dirty state, submodules and rollback point are recorded.
3. Contract Freeze: Sol accepts public/shared contracts and shared-path ownership.
4. Dispatch: worktrees, models, resource leases, budgets and path conflict checks pass.
5. Candidate: candidate commit, handoff and task validation exist.
6. Review: Sol High reviews the actual commit range and evidence.
7. Integration: accepted commits merge in DAG order and phase checks pass.
8. Cleanup: worktrees, markers and temporary resources are inventoried.
9. Phase Close: roadmap, validation debt and routing scorecard are current.

## Context and hooks

The context packet contains the task packet, nearest `AGENTS.md`, frozen contracts, direct ADRs/knowledge, affected interfaces/tests, and prerequisite handoffs. Allow one concrete expansion request naming the missing decision or symbol.

Pre-dispatch enforcement blocks absent baseline, non-Ready task, manifest drift, model mismatch, closed resource gate, shared worktree writer, owned-path overlap, unauthorized shared-contract changes, and recursive delegation. Stop enforcement requires candidate commit, handoff, actual model, validation evidence, clean branch, and owned-path compliance. A reviewer profile is read-only and returns findings/verdict only. Continue once on missing review/integration evidence; a repeated stop or cancellation ends the run.

## Worktrees and scorecard

Allocate one branch/worktree per writer from the phase base. Keep shared contract and integration edits with Sol. `cleanup inspect` only reports clean/dirty, merged/unmerged, last commit, and a safe suggested command.

At phase close compare preferred/fallback/actual model outcomes: completion without replan, finding severity/count, fix cycles, tokens, latency, paid cost, validation failures, and escaped defects. Do not reduce model tier below the retained quality threshold.
