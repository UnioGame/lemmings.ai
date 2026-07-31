# lemmings

> Proportional orchestration for Codex agents: start with one lightweight skill and add automation only when the repository needs it.

The default installation is intentionally small. The `$lemmings` skill works without Python, hooks, a CLI, or generated task files.

## Choose your setup

| Setup | What you get | Python |
|---|---|---:|
| **Skill only** | `$lemmings`, mode selection, model routing, planning and review guidance | **No** |
| **Plugin** | Skill discovery plus packaged agent resources | **No** |
| **Plugin + hooks** | Automatic ownership, model, context, and evidence checks | **Yes** |
| **CLI** | `lemmings check`, runtime state, worktrees, phases, waves, and model pins | **Yes** |

> [!TIP]
> Start with **Skill only**. Add hooks when policy must be enforced automatically. Add the CLI when deterministic repository checks are useful in local development or CI.

## Quick start: install only the skill

This is the recommended installation. It exposes `$lemmings` to Codex and has no runtime dependencies.

### 1. Reuse the existing checkout

Keep one checkout of this repository. Do not create a second copy solely to expose the skill.

### 2. Link the skill into the consumer repository

Codex discovers repository skills under `.agents/skills/`. Link the existing `skills/lemmings` directory there.

**Windows PowerShell**

```powershell
New-Item -ItemType Directory -Force .agents/skills | Out-Null
New-Item -ItemType Junction `
  -Path .agents/skills/lemmings `
  -Target (Resolve-Path GameClient/Game.Packages/lemmings/skills/lemmings)
```

**macOS or Linux**

```bash
mkdir -p .agents/skills
ln -s ../../GameClient/Game.Packages/lemmings/skills/lemmings .agents/skills/lemmings
```

Adjust the source path when the checkout lives elsewhere. If links are unavailable, copy the directory instead:

```powershell
Copy-Item GameClient/Game.Packages/lemmings/skills/lemmings `
  .agents/skills/lemmings -Recurse
```

> [!NOTE]
> A link follows future package updates automatically. A copied skill must be refreshed manually.

### 3. Open a new Codex task

Skills are discovered when a task starts. Verify the installation in a new task:

```text
$lemmings status
```

Use proportional mode selection by default:

```text
$lemmings auto
```

That is the complete skill-only installation. Python is not involved.

## Skill commands

```text
$lemmings on
$lemmings off
$lemmings auto
$lemmings status
```

- `on` forces orchestration for the current task.
- `off` prevents new orchestration artifacts and delegation for the current task.
- `auto` chooses the lightest safe mode from the current risks.
- `status` reports the effective task and repository mode.

Model pins remain under user control:

```text
$lemmings models set worker=gpt-5.6-sol:medium
$lemmings models task TASK-17 worker=gpt-5.6-sol:medium
$lemmings models status
$lemmings models reset
```

## Modes

| Mode | Use when | Required evidence |
|---|---|---|
| **Simple** | One agent, local bounded change | Focused validation |
| **Standard** | One writer with sequential exploration or validation | Task packet, candidate commit, independent review |
| **Strict** | Parallel writers, shared contracts, submodules, code generation, or external resources | Frozen baseline, isolated worktrees, ownership, leases, review and integration evidence |

The core lifecycle is:

```text
Prepare -> Dispatch -> Execute/Candidate -> Review/Repair -> Integrate/Close
```

## Optional: install the full plugin

Use the plugin when the workspace should load packaged resources from the existing checkout. The plugin itself does not require Python while hooks remain disabled.

Create `.agents/plugins/marketplace.json` in the consumer repository:

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

Point `source.path` at the checkout already used by the repository. Open a new Codex task after adding or updating the plugin.

### Repository profile

Hooks and the CLI use `.codex/lemmings.json` as their deterministic repository contract. A minimal profile is:

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
    "complex-worker": "gpt-5.6-sol:medium"
  },
  "fallback": {
    "allowed": []
  },
  "risks": []
}
```

## Optional: hooks

Hooks turn written policy into automatic checks around agent and tool execution.

### What hooks enforce

- A spawned worker matches its assigned model and task role.
- Strict or parallel writers use isolated worktrees.
- Writers stay inside declared owned paths.
- Reviewers remain read-only and inspect the actual candidate range.
- Subagents receive bounded task context.
- Candidate completion includes commit, validation or owned debt, actual model, and embedded handoff.
- Post-tool inspection warns when the real diff violates ownership.

### Why hooks need Python

Codex invokes the hook entrypoint from `lemmings/hooks.py`. Python runs that policy engine, reads the repository profile and runtime marker, and returns an allow, warn, or block decision.

Python is only the hook runtime:

- Python 3.10 or newer is required.
- `pip install` is **not** required for hooks.
- Windows uses `py -3`; other platforms use `python3`.
- The hook imports its modules directly from the plugin checkout.

Enable hooks in `.codex/config.toml`:

```toml
[agents]
max_concurrent_threads_per_session = 3

[features]
hooks = true
```

Verify the interpreter before enabling them:

```text
py -3 --version
python3 --version
```

Only the command appropriate for the current platform needs to succeed.

### How to work without Python

Leave hooks disabled and use the skill-only workflow. The orchestration guidance, mode selection, task contracts, model routing, and review process remain available.

Without hooks, enforce these boundaries through agent profiles and normal repository tools:

1. Give each writer explicit owned paths.
2. Use `git worktree` for parallel writers.
3. Keep reviewers in read-only sandboxes.
4. Validate the exact candidate commit range.
5. Record missing validation as debt with an owner and future gate.

The difference is enforcement: the skill guides the workflow, while hooks can block invalid actions automatically.

## Optional: CLI

The CLI is a separate convenience layer. It requires Python because the `lemmings` executable is implemented by the Python package.

Install it from the existing checkout:

```text
python -m pip install --user -e GameClient/Game.Packages/lemmings
lemmings --help
```

If the executable is not found, add the Python user Scripts directory to `PATH` and open a new terminal.

Common commands:

```text
lemmings check
lemmings check --all
lemmings status
lemmings runtime on
lemmings runtime off
lemmings worktree inspect
lemmings models status
```

The skill works normally when the CLI is not installed. In that setup, use Git and repository test commands directly instead of `lemmings check` and worktree helpers.

## Recommended rollout

1. Install the skill without Python.
2. Use `$lemmings auto` on real tasks.
3. Add agent profiles when role-specific models and sandboxes become useful.
4. Add hooks only when automatic policy enforcement justifies the Python dependency.
5. Add the CLI for repeatable local or CI validation.

## More documentation

- [Orchestration guide](Documentation~/guides/lemmings.md)
- [Skill contract](skills/lemmings/SKILL.md)
- [CLI commands](skills/lemmings/references/commands.md)
- [Workflow and hooks](skills/lemmings/references/workflow.md)
- [Artifact contracts](skills/lemmings/references/contracts.md)
