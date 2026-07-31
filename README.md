# lemmings

lemmings coordinates Codex agents with the lightest workflow that is safe for a task. It ships as a Codex plugin and a Python CLI. The project name in documentation, commands, skills, and plugin UI is always `lemmings`.

## What is included

- `$lemmings` skill for enabling, disabling, and configuring orchestration per task.
- Hooks for model assignments, write ownership, bounded context, candidate evidence, and read-only review.
- Agent profiles for the orchestrator, reviewer, complex worker, worker, explorer, validator, and summarizer.
- `lemmings` CLI for repository runtime, validation, worktrees, phases, waves, model pins, and close evidence.
- Reusable task, phase, review, and roadmap templates.

## Requirements

- Git repository.
- Python 3.10 or newer.
- Codex with workspace plugins and hooks enabled.
- One local checkout of this package that is visible from the consuming repository.

## Install in a repository

### 1. Register the existing package checkout

Create `.agents/plugins/marketplace.json`. Point `source.path` at the package checkout already used by the repository; do not add a second copy solely for the plugin.

```json
{
  "name": "workspace",
  "interface": {
    "displayName": "Workspace"
  },
  "plugins": [
    {
      "name": "lemmings",
      "source": {
        "source": "local",
        "path": "./GameClient/Game.Packages/lemmings"
      },
      "policy": {
        "installation": "INSTALLED_BY_DEFAULT",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```

### 2. Enable workspace hooks and agents

Create `.codex/config.toml`:

```toml
[agents]
max_concurrent_threads_per_session = 3

[features]
hooks = true
```

Copy the required role profiles from `assets/repo-integration/auto.qa/.codex/agents/` into the consumer's `.codex/agents/` directory, or define equivalent profiles using the same model assignments.

### 3. Add a repository profile

Create `.codex/lemmings.json`:

```json
{
  "schemaVersion": 1,
  "mode": "auto",
  "roadmap": "docs/tasks/ROADMAP.md",
  "taskGlobs": ["docs/tasks/lemmings/*.json"],
  "reviewGlobs": ["docs/tasks/lemmings/reviews/*.json"],
  "worktreeRoot": "../lemmings-worktrees",
  "models": {
    "orchestrator": "gpt-5.6-sol:high",
    "reviewer": "gpt-5.6-sol:high",
    "complex-worker": "gpt-5.6-sol:medium",
    "worker": "gpt-5.6-terra:medium",
    "explorer": "gpt-5.6-terra:low",
    "validator": "gpt-5.6-terra:medium",
    "summarizer": "gpt-5.6-terra:low"
  },
  "fallback": {
    "allowed": []
  },
  "risks": []
}
```

### 4. Install the CLI

Install the Python package from the same checkout:

```text
python -m pip install --user -e GameClient/Game.Packages/lemmings
lemmings --help
```

If the executable is not found, add the Python user Scripts directory to `PATH` and open a new terminal.

### 5. Reload Codex

Open a new Codex task after adding or updating the plugin. Skills, hooks, and agent profiles are loaded when a task starts.

## Enable or disable orchestration

Task-scoped skill commands:

```text
$lemmings on
$lemmings off
$lemmings auto
$lemmings status
```

Repository runtime commands:

```text
lemmings runtime on
lemmings runtime off
lemmings runtime status
```

`auto` is the recommended default. A local single-agent change stays Simple. Standard adds a task packet, candidate commit, and independent review. Strict additionally requires frozen contracts, isolated writer worktrees, ownership checks, and integration evidence.

## Assign models

The orchestrator and reviewer use Sol High. Complex implementation uses Sol Medium by default. Users can pin worker models globally or per task:

```text
$lemmings models set worker=gpt-5.6-sol:medium
$lemmings models task TASK-17 worker=gpt-5.6-sol:medium
$lemmings models status
$lemmings models reset
```

## Typical workflow

1. Describe the goal, acceptance criteria, risks, dependencies, and files in scope.
2. Let `auto` select Simple, Standard, or Strict.
3. For Standard or Strict, execute bounded task packets and create candidate commits.
4. Run focused validation before broad checks.
5. Review the actual candidate or fix commit range with the read-only Sol High reviewer.
6. Integrate only Accepted candidates, rerun integration validation, and record remaining debt.
7. Finish with `lemmings check`; use `lemmings check --all` for a complete Strict lifecycle audit.

## More documentation

- [Orchestration guide](Documentation~/guides/lemmings.md)
- [Skill contract](skills/lemmings/SKILL.md)
- [CLI commands](skills/lemmings/references/commands.md)
- [Workflow and hooks](skills/lemmings/references/workflow.md)
- [Artifact contracts](skills/lemmings/references/contracts.md)
