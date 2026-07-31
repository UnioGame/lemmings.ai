# Lemmings

Lemmings coordinates repository work through proportional agent orchestration. It keeps small tasks lightweight and adds planning, isolation, review, and integration evidence only when risk requires them.

The package contains:

- the `$lemmings` skill;
- agent role profiles and task templates;
- an optional `lemmings` CLI;
- optional policy hooks for ownership, models, context, and evidence.

The skill works without Python. Python 3.10 or newer is needed only for the CLI and hooks.

## Install the skill

Link or copy this package's `skills/lemmings` directory into the consumer repository:

```text
.agents/skills/lemmings
```

Open a new Codex task and verify discovery:

```text
$lemmings status
```

Use automatic proportional orchestration by default:

```text
$lemmings auto
```

## Apply Lemmings

The orchestrator always follows:

```text
Discover -> Plan -> Refine -> Implement -> Verify
```

Implementation starts only after Refine declares the task Ready.

| Mode | Use when | Process |
|---|---|---|
| **Simple** | One bounded local change | One agent, no orchestration artifacts |
| **Standard** | One writer with sequential exploration or validation | Task packet, candidate commit, focused validation, independent review |
| **Strict** | Parallel writers, shared contracts, serialized assets, submodules, code generation, or external resources | Frozen baseline, isolated worktrees, ownership, leases, review, and integration evidence |

Default model routing:

- Orchestrator: `gpt-5.6-sol:high`.
- Reviewer: `gpt-5.6-sol:high`, read-only.
- Complex Worker: `gpt-5.6-sol:medium`.
- Explicit user model assignments take priority.

## Skill commands

These commands affect the current Codex task:

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

## CLI

Install the optional CLI from this checkout:

```text
python -m pip install --user -e <path-to-lemmings>
lemmings --help
```

Repository state and validation:

```text
lemmings on
lemmings off
lemmings status
lemmings check
lemmings check --all
```

Repository mode:

```text
lemmings mode auto
lemmings mode simple
lemmings mode standard
lemmings mode strict
lemmings mode status
```

`mode` is stored in `.codex/lemmings.json`. The `status` command reports both configured and effective modes.

Model assignments:

```text
lemmings models set worker=gpt-5.6-sol:medium
lemmings models task TASK-17 worker=gpt-5.6-sol:medium
lemmings models status
lemmings models reset
```

Worktrees and Strict lifecycle:

```text
lemmings worktree allocate|inspect|release
lemmings phase prepare
lemmings wave plan
lemmings close
lemmings scorecard
```

## Hooks

Hooks are optional. When connected by a consumer repository, they enforce model assignments, writer ownership, worktree isolation, bounded subagent context, read-only review, and candidate evidence.

Use `lemmings on` and `lemmings off` to enable or disable repository hook enforcement. Without hooks, the skill and orchestration workflow remain fully usable.

## Documentation

- [Orchestration guide](Documentation~/guides/lemmings.md)
- [Skill contract](skills/lemmings/SKILL.md)
- [Command reference](skills/lemmings/references/commands.md)
- [Workflow and hooks](skills/lemmings/references/workflow.md)
- [Artifact contracts](skills/lemmings/references/contracts.md)
