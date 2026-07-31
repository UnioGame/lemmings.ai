# Contracts

All JSON artifacts use `schemaVersion: 1`.

- Roadmap stores only priority and dependencies.
- Strict phase stores baseline, frozen contracts, leases, and close evidence.
- Task stores plan, ownership, execution/handoff, commits, model assignment, validation/debt, review pointer, and close evidence.
- Review is separate immutable evidence for the exact candidate/fix head.

Do not create tracked dispatch, separate handoff, separate integration evidence, adapters, or compatibility fields. Generate a scorecard only for a benchmark or at least two comparable observations.

Model fields live under `models`: `requested` is an optional user pin, `assigned` is fixed before spawn, `actual` records execution, and `fallbackReason` is required only when actual differs from assigned.
