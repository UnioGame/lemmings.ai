# Contracts

All JSON artifacts use `schemaVersion: 1`.

- Roadmap stores only priority and dependencies.
- Strict phase stores baseline, frozen contracts, leases, and close evidence.
- Task stores plan, ownership, execution/handoff, commits, model assignment, validation/debt, review pointer, and close evidence.
- Review is separate immutable evidence for the exact candidate/fix head.

Do not create tracked dispatch, separate handoff, separate integration evidence, adapters, or compatibility fields. Generate a scorecard only for a benchmark or at least two comparable observations.

Model fields live under `models`: `requested` is an optional user pin, `assigned` is fixed before spawn, `actual` records execution, and `fallbackReason` is required only when actual differs from assigned.

Candidate tasks require `baseSha`, an actual model, embedded handoff, and real candidate/fix Git commits. Each fix descends from the previous commit and the candidate descends from `baseSha`; a task branch need not be the orchestrator's current HEAD. Accepted tasks bind their embedded review task/base/head/status/evidence path to an existing immutable review file. Evidence paths must remain inside the repository.

Consumer defaults live in `profile.models`. Explicit repo pins live in `profile.requestedModels`; task-role pins live in `profile.taskModels`. Resolve assignment as task pin, then repo pin, then role default. Copy an effective pin into both task `models.requested` and `models.assigned`. Reviewer remains `gpt-5.6-sol:high`; an explicit orchestrator pin may raise Sol effort above High but may not downgrade it.
