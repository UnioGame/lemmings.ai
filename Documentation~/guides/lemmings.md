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

## Optional telemetry

Telemetry is independent of policy runtime and orchestration mode. Its selection is repository-wide in the Git common directory and remains off until the user explicitly selects `lemmings metrics basic` or `lemmings metrics full`. Return it to `off` after a bounded measurement when other repository work must not be observed.

Basic telemetry records the five lifecycle boundaries, task outcome, wall-clock stage duration, subagent and tool timing, assigned/actual model, task state, review/fix cycles, validation failures, and validation debt. Full telemetry additionally accepts normalized test, coverage, analyzer, complexity, performance, size, and actual-cost observations plus explicit escaped-defect, rollback, revert, and resolution annotations. Lemmings imports these results; it never runs repository quality commands itself.

The orchestrator calls `lemmings metrics stage discover|plan|refine|implement|verify` when entering each real step. A repeated current stage is idempotent. `lemmings close` finishes Integrated work; `lemmings metrics finish --outcome completed|blocked|cancelled|replan` closes Simple or non-integrated work.

Raw data is derived local state under `.git/lemmings/telemetry`. Each event is an atomic immutable JSON file with a dedupe key, so parallel worktrees do not share a writable ledger. `lemmings on --task` and a successful `lemmings wave plan` create local worktree bindings. Events without an unambiguous binding stay unbound and reduce report completeness.

Hooks observe SessionStart, turn start/Stop, supported tool pairs, and subagent start/stop. Telemetry recording is fail-open and never modifies a policy decision. SessionEnd is not used for duration because it can arrive late. Transcripts are never parsed.

Events and exported reports exclude prompts, transcripts, reasoning, tool arguments/output, source-code content, diffs, secrets, authorization headers, and absolute paths. Report export occurs only with explicit `--output`. Cleanup only inspects until `--execute` is supplied. Defaults are a 90-day retention warning and a 100 MiB size warning; no automatic deletion occurs.

Quality imports use schema version 1 and bind `taskId`, `baseSha`, and current candidate/fix or integration-merge `headSha`. Use the task packet path for complete cross-checking; an ID alone can resolve it only through a unique local binding. Regressions require a recorded Integrated event. Only confirmed regressions count toward the blocking KPI; suspected relations remain visible. Reports calculate time from integration to detection and from detection to resolution. Imported labels and references must be producer-sanitized; built-in rejection of common secret patterns is an additional guard, not a general secret scanner.

Keep wall-clock lead time separate from summed agent time. Churn is task-size context, not productivity. Do not calculate a subjective combined score or change modes/models automatically. Comparative analysis becomes `eligible_for_review` only with at least five Integrated tasks in one `telemetryCohort`, complete bound lifecycle stages, at least one passing quality signal per task, no failed quality signal, and no confirmed P0/P1 regression. Otherwise it remains descriptive.

Exact token usage and model API latency are unsupported in the first schema and are reported as such, never as zero or estimates. Actual provider cost may be imported as a quality signal.

## CLI

Use `lemmings mode auto|simple|standard|strict` to persist the repository mode and `lemmings mode status` to compare its configured and effective values. Use `lemmings status` and `lemmings check` for routine work. Worktrees are managed through `lemmings worktree allocate|inspect|release`. Strict preparation uses `lemmings phase prepare` and `lemmings wave plan`; final integration uses `lemmings close`. `lemmings scorecard` creates output only for a benchmark or at least two observations sharing one explicit telemetry cohort.
