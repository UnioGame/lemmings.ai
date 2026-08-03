# Lemmings

Lemmings coordinates repository work through:

```text
Discover -> Plan -> Refine -> Implement -> Verify
```

It keeps bounded work Simple and adds task evidence, review, worktrees, ownership, and integration checks only as risk grows.

## Install the skill

Link or copy `skills/lemmings` into the consumer repository as:

```text
.agents/skills/lemmings
```

Open a new Codex task and run:

```text
$lemmings status
$lemmings auto
```

The skill does not require Python.

## Modes and models

```text
lemmings mode auto|simple|standard|strict|status
lemmings models set worker=gpt-5.6-sol:medium
lemmings models task TASK-17 worker=gpt-5.6-sol:medium
lemmings models status|reset
```

- Orchestrator and reviewer: `gpt-5.6-sol:high`.
- Complex Worker: `gpt-5.6-sol:medium`.
- Explicit user model assignments take priority.

## Optional CLI and hooks

Python 3.10 or newer is needed only for deterministic CLI checks, hooks, worktree management, and telemetry:

```text
python -m pip install --user -e <path-to-lemmings>
lemmings --help
```

```text
lemmings on|off
lemmings status
lemmings check [--all]
lemmings worktree allocate|inspect|release
lemmings phase prepare
lemmings wave plan
lemmings close
```

Hooks enforce model assignments, ownership, isolation, bounded context, read-only review, and candidate evidence. The skill remains usable without them.

## Optional telemetry

Telemetry is local and off by default:

```text
lemmings metrics off|basic|full|status
lemmings metrics stage discover|plan|refine|implement|verify [--task TASK-17]
lemmings metrics finish --outcome completed|blocked|cancelled|replan
lemmings metrics report
```

`basic` measures lifecycle and agent/tool timing. `full` also imports existing quality checks and records post-integration regressions. It never stores prompts, transcripts, reasoning, tool payloads, code, diffs, or secrets.

## Documentation

- [Orchestration guide](Documentation~/guides/lemmings.md)
- [Skill contract](skills/lemmings/SKILL.md)
- [Commands](skills/lemmings/references/commands.md)
- [Telemetry](skills/lemmings/references/telemetry.md)
