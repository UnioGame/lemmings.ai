# Telemetry

Telemetry is local, optional, and off by default. It never changes orchestration mode, model routing, acceptance, or hook policy.

## Levels

- `off` records nothing new and preserves existing history.
- `basic` records lifecycle, tool, agent, model, review, validation, and delivery timing.
- `full` additionally accepts normalized CI quality signals and post-integration regression annotations.

Exact tokens and model API latency are unsupported in schema version 1. Reports must show them as unsupported, never zero or estimated. Actual cost may arrive as an imported quality signal.

## Lifecycle discipline

If telemetry is active, enter `discover`, `plan`, `refine`, `implement`, and `verify` at their real boundaries. Repeating the current stage is a no-op. Finish with the factual outcome; successful `lemmings close` records integration automatically.

For Standard work without an active `lemmings on --task` binding, pass the stable task ID until its packet exists, then pass the real packet path. A missing path-like value is rejected rather than recorded as an ID. Simple work may remain taskless. A successful Strict `wave plan` creates each worktree binding.

Strict waves bind each task to its declared worktree. A hook without an unambiguous binding remains unbound and lowers report completeness. Never assign it to a task heuristically.

## Evidence and privacy

Raw events live under `.git/lemmings/telemetry` as immutable, deduplicated files. Do not store prompts, transcripts, hidden reasoning, tool inputs/outputs, code, diffs, secrets, authorization headers, or absolute paths in events or exports.

Full telemetry imports existing validation/CI results; it does not run checks. Use the [quality observation template](../../../Documentation~/tasks/templates/quality-observation.json). Each signal requires `name`, `category`, numeric `value`, `unit`, `direction`, `status`, and `sourceRef`; optional `baseline` and `threshold` are also numeric. Categories are `tests`, `coverage`, `analyzer`, `complexity`, `performance`, `size`, or `cost`. Direction is `higher-better`, `lower-better`, or `target`; status is `pass`, `fail`, or `unknown`.

Import through the task packet path when possible so `taskId`, `baseSha`, and candidate/fix or integration-merge `headSha` are cross-checked. An ID uses a unique local binding when one exists; without one it cannot validate packet SHAs.

A post-integration annotation requires kind, P0-P3 severity, confirmed or suspected relation, and a non-secret external or repository-relative issue/CI reference. Supply the factual detection timestamp; omitting it measures from annotation time. Only confirmed regressions affect the blocking KPI, so use `suspected` until causality is established. Sanitize all imported labels and references; Lemmings rejects common secret patterns and absolute paths but this does not replace producer-side redaction.

## Interpretation

Keep wall-clock lead time separate from summed agent time. Treat churn as task-size context, not productivity. Do not create a combined efficiency score or automatically change a mode/model.

Comparative recommendations require at least five Integrated tasks in one explicit `telemetryCohort`, complete bound lifecycle stages, at least one passing quality signal per task, no failed quality signal, and no confirmed P0/P1 regression. Smaller, incomplete, or mixed samples remain descriptive. Use `lemmings scorecard` only for explicit benchmark or comparable cohort observations.
