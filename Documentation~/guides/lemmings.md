# Lemmings orchestration guide

Lemmings applies only the lifecycle needed by the risk. Simple work uses one agent and no artifacts. Standard work uses one mutable task packet, a candidate commit, narrow validation, and immutable Sol review. Strict work adds a phase baseline, frozen contracts, isolated worktrees, leases, and integration evidence.

Require an independent plan review only for a Strict phase, a frozen public contract, or an explicit user request.

A Strict phase is dispatchable only after `baselineReview` is Accepted by `gpt-5.6-sol:high` and references immutable evidence. `lemmings phase prepare` leaves that review Planned unless `--baseline-review-evidence` is supplied explicitly.

## Pipeline and states

The five stages are Prepare, Dispatch, Execute/Candidate, Review/Repair, and Integrate/Close. Dispatch is derived from Ready tasks and retained only as an audit event. Handoff lives under `task.execution`; integration evidence lives under `task.close` or `phase.close`.

States are Planned, Ready, Active, Candidate, Accepted, and Integrated. Exceptions are Blocked, Replan Required, Cancelled, and Superseded. A ChangesRequested review leaves the task Candidate and advances `review.cycle`. After the second failed review, use Replan Required.

Accepted means the immutable review approves the current candidate/fix head. Integrated additionally requires a merge commit and passing integration validation.

## Models

The orchestrator and reviewer use `gpt-5.6-sol:high`. Complex workers use `gpt-5.6-sol:medium`. A task records `models.requested`, `models.assigned`, and `models.actual`. A user pin wins. If actual differs from assigned, it must be allowed by the consumer profile and have `fallbackReason`.

## Hooks

PreToolUse validates dispatch/model/worktree binding and exact path ownership. Reviewer profiles must be configured read-only; hook write denial additionally applies whenever the host payload exposes reviewer identity, but does not replace the sandbox. Known read-only shell commands are allowed. Script blocks and unknown shell write-sets warn in Standard and block in Strict. SubagentStart injects bounded context; SubagentStop requires embedded handoff, candidate/fix commit, actual model, and validation or owned debt. PostToolUse inspects actual paths and advises on violations. There is no Stop continuation.

Repo consumers install the Lemmings plugin through their marketplace. The plugin auto-discovers `hooks/hooks.json`; do not copy the hook configuration into consumer `.codex` state because duplicate registration executes every hook twice.

## CLI

Use `lemmings status` and `lemmings check` for routine work. Worktrees are managed through `lemmings worktree allocate|inspect|release`. Strict preparation uses `lemmings phase prepare` and `lemmings wave plan`; final integration uses `lemmings close`. `lemmings scorecard` creates output only for a benchmark or at least two observations.
