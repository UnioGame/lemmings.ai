# Telemetry

Tracked Task/Review quality metrics are mandatory for Standard and Strict delivery and do not depend on telemetry. Telemetry and hooks add local timing data, remain optional and off by default, and never change lifecycle, routing, acceptance, or safety policy.

## Levels

- `off` records nothing new and preserves existing history.
- `basic` records lifecycle, tool, agent, model, review, validation, and delivery timing.
- `full` additionally accepts normalized CI quality signals and post-integration regression annotations.

Exact tokens and model API latency are unsupported in schema version 2. Reports must show them as unsupported, never zero or estimated. Actual cost may arrive as an imported quality signal.

## Lifecycle discipline

Always finish a tracked run with `lemmings metrics finish --task <task-json> --outcome ...`; this derives and writes `qualitySummary` and outputs cumulative artifact quality even in `off` mode. If telemetry is active, enter `discover`, `plan`, `refine`, `implement`, and `verify` at their real boundaries. Repeating the current stage is a no-op.

Pass the stable task ID until its packet exists, then pass the real packet path. A missing path-like value is rejected rather than recorded as an ID. Simple work may remain taskless. For Strict work, invoke the first `metrics stage` from the task's actual current checkout or isolated workspace so its binding records the real execution location.

Strict waves bind isolated tasks to their declared worktrees and serial-current tasks to the current checkout. A hook without an unambiguous binding remains unbound and lowers report completeness. Never assign it to a task heuristically.

## Evidence and privacy

Raw events live under `.git/lemmings/telemetry-v2` as immutable, deduplicated files. Leave older histories untouched and never read them as v2. Do not store prompts, transcripts, hidden reasoning, tool inputs/outputs, code, diffs, secrets, authorization headers, or absolute paths in events or exports.

Full telemetry imports existing validation/CI results; it does not run checks. A quality observation uses schema version 2 and binds `taskId`, `baseSha`, `headSha`, and `recordedAt` to a `signals` array. Each signal requires `name`, `category`, numeric `value`, `unit`, `direction`, `status`, and `sourceRef`; optional `baseline` and `threshold` are numeric. Categories are `tests`, `coverage`, `analyzer`, `complexity`, `performance`, `size`, or `cost`. Direction is `higher-better`, `lower-better`, or `target`; status is `pass`, `fail`, or `unknown`.

Import through the task packet path when possible so `taskId`, `baseSha`, and candidate/fix or integration-merge `headSha` are cross-checked. An ID uses a unique local binding when one exists; without one it cannot validate packet SHAs.

A post-integration annotation requires kind, P0-P3 severity, confirmed or suspected relation, and a non-secret external or repository-relative issue/CI reference. Supply the factual detection timestamp; omitting it measures from annotation time. Only confirmed regressions affect the blocking KPI, so use `suspected` until causality is established. Sanitize all imported labels and references; Lemmings rejects common secret patterns and absolute paths but this does not replace producer-side redaction.

## Interpretation

Keep wall-clock lead time separate from summed agent time. Treat churn as task-size context, not productivity. Do not create a combined efficiency score or automatically change a mode/model.

Artifact-quality comparisons require at least five Integrated, complete, fully classified tasks per model in one explicit `telemetryCohort`. Incomplete or unclassified tasks remain visible but excluded. A comparison recommendation appears only when at least two eligible model cohorts exist; it never changes routing automatically. Timing-aware benchmark recommendations additionally require complete bound lifecycle stages, passing imported quality evidence, and no confirmed P0/P1 regression. Use `lemmings metrics report --benchmark` for explicit benchmark evidence.
