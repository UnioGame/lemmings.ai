# Commands

```text
$lemmings on
$lemmings off
$lemmings auto
$lemmings status
$lemmings models set worker=gpt-5.6-sol:medium
$lemmings models task TASK-17 worker=gpt-5.6-sol:medium
$lemmings models status
$lemmings models reset
```

The skill commands control Lemmings usage in the current task/thread:

- `on` forces Lemmings enabled for the current work.
- `off` creates no new delegation or artifacts, but cannot bypass safety checks already active for the task.
- `auto` is the default and selects Simple, Standard, or Strict proportionally from current risk.
- `status` reports the effective thread and repository mode, including whether repo runtime is active.

The `$` distinguishes task-scoped skill commands from the executable. Persist repository hook state with `lemmings on|off`; inspect it with the unified `lemmings status`. The `auto` command remains skill-only.

Use the CLI where repository persistence or deterministic validation is required:

```text
lemmings on|off
lemmings mode auto|simple|standard|strict|status
lemmings models set|task|status|reset
lemmings check [--all]
lemmings status
lemmings worktree allocate|inspect|release
lemmings phase prepare
lemmings wave plan
lemmings close
lemmings scorecard
lemmings metrics off|basic|full|status
lemmings metrics stage discover|plan|refine|implement|verify
lemmings metrics finish --outcome completed|blocked|cancelled|replan
lemmings metrics import --task TASK-17 --file quality.json
lemmings metrics annotate --task TASK-17 --kind escaped-defect --severity P1 --relation suspected --reference BUG-123 --detected-at <ISO-8601>
lemmings metrics report [--task TASK-17] [--phase PHASE-2] [--since 30d] [--format json|markdown] [--output <path>]
lemmings metrics cleanup --older-than 90d [--execute]
```

`mode auto|simple|standard|strict` persists the repository selection in `.codex/lemmings.json` without replacing other profile settings. `mode status` reports the configured value, the effective risk-adjusted mode, hook activity, and the active runtime mode.

`models set` records an explicit repo-role pin in `requestedModels`; `models task` records the higher-priority task-role pin in `taskModels`. Neither command rewrites immutable role defaults in `models`.

Telemetry is independent of `lemmings on|off` and orchestration mode. It is disabled until `metrics basic` or `metrics full` is selected. `basic` records lifecycle and hook timing; `full` also accepts normalized quality and regression evidence. Report export requires an explicit `--output`. Cleanup is inspection-only unless `--execute` is present.
