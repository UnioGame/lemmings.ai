# Canonical artifacts v2

All artifacts require `schemaVersion: 2`. Reject every other version as unsupported; do not infer or migrate old fields.

The repository Profile also requires `distributionVersion: 2.0.0`, the six canonical role models, worker routing, and the fixed Context-contract budget. Consumer mode, paths, and globs may be project-specific.

## Task

Require `taskId`, `goal`, non-empty `acceptance`, `dependencies`, `risks`, `state`, `ownership`, `models`, `workspace`, typed `workingSet`, `validation`, `execution`, `reviewHistory`, and `close`. Keep interfaces, tests, dependency handoffs, validation evidence, and attempts as arrays under `execution`; keep risk-to-test, commands, allowed outputs, and debt as arrays under `validation`. Map each material risk to a declared test. Record execution attempts, embedded handoff, immutable review references, and close evidence in the Task.

Working-set entries require non-empty `ref` and `purpose`. Candidate/Accepted/Integrated states require base and candidate/fix SHAs, actual model, handoff, and validation evidence or owned debt. Accepted/Integrated require immutable review evidence. Integrated requires complete tracked quality plus merge and integration-validation evidence.

## Phase

Strict work requires `phaseId`, baseline SHA, integration branch, frozen-contract references, baseline Review, task DAG, leases, and close evidence. Task dependencies must match the DAG. Parallel writers must have disjoint ownership and isolated workspaces.

## Review

Require an immutable exact subject range, reviewer model, cycle, verdict, validation, and findings. Every finding requires `summary`, priority `P0`-`P3`, and origin `implementation`, `plan-contract`, `validation`, or `integration`.
