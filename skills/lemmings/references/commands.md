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

These thread-scoped commands are not CLI aliases. Persist repository runtime state only with `lemmings runtime on|off|status`; `auto` remains skill-level mode selection.

Use the CLI where repository persistence or deterministic validation is required:

```text
lemmings runtime on|off|status
lemmings models set|task|status|reset
lemmings check [--all]
lemmings status
lemmings worktree allocate|inspect|release
lemmings phase prepare
lemmings wave plan
lemmings close
lemmings scorecard
```

`models set` records an explicit repo-role pin in `requestedModels`; `models task` records the higher-priority task-role pin in `taskModels`. Neither command rewrites immutable role defaults in `models`.
