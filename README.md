# UniGame AI Tools

UPM-compatible, documentation-only package with reusable Codex skills, orchestration guidance, roadmaps, and task templates.

## Included

- `orchestrate-agent-tasks` — cost-aware multi-agent planning, delegation, review, testing, and integration.
- `Documentation~/guides/subagent-task-orchestration.md` — complete human-facing pipeline.
- `Documentation~/tasks/ROADMAP.md` — live priorities for this toolkit.
- `Documentation~/tasks/task-template.md` — decision-complete task template.

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
