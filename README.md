# UniGame AI Tools

UPM-compatible, documentation-only package with a reusable Codex orchestration skill, phase/wave delivery artifacts, and validation contracts.

## Included

- `skills/orchestrate-agent-tasks` — canonical skill: baseline, wave dispatch, candidate commits, Sol review, and integration gates.
- `Documentation~/guides/subagent-task-orchestration.md` — полный русский delivery-пайплайн.
- `Documentation~/tasks/templates/` — phase, dispatch, task, handoff, review, integration, scorecard и profile contracts.
- `Documentation~/tasks/ROADMAP.md` — delivery-приоритеты ORCH-005…012.

## Quick Start

From this repository, invoke:

```text
$orchestrate-agent-tasks auto
```

Thread controls:

```text
$orchestrate-agent-tasks on
$orchestrate-agent-tasks off
$orchestrate-agent-tasks auto
$orchestrate-agent-tasks status
```

Pin subagent models:

```text
$orchestrate-agent-tasks models set explorer=gpt-5.6-terra:low workers=gpt-5.6-sol:medium
```

The package contains no Unity Runtime or Editor assemblies and does not need to be added to a Unity project's `Packages/manifest.json`.
