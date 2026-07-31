# Workflow

## Modes

Simple uses no artifacts. Standard requires a task packet, candidate/fix commits, validation or owned debt, and immutable Sol review. Strict adds an accepted baseline, frozen contracts, non-overlapping ownership, unique worktrees, resource leases, and close evidence.

Strict activates for parallel writers, shared contracts, Unity serialized assets, submodules, code generation, or external resources.

Require independent plan review only for a Strict phase, a frozen public contract, or an explicit user request.

## Lifecycle

`Prepare -> Dispatch -> Execute/Candidate -> Review/Repair -> Integrate/Close`

Task states are `Planned -> Ready -> Active -> Candidate -> Accepted -> Integrated`, with `Blocked`, `Replan Required`, `Cancelled`, and `Superseded` exceptions. Dispatch is an audit event. Review remains nested in Candidate until Accepted. A second failed review requires Replan Required.

## Safety

Run writers in isolated worktrees for Strict or parallel delivery. One writer owns one worktree. Map every material risk to a test or explicit debt with reason, owner, future gate, and blocking status. Reviewers are read-only. Known read-only shell commands pass; unknown shell writes warn in Standard and block in Strict. No Stop continuation hook exists.
