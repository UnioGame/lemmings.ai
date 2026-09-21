# Lemmings helper

A small standard-library Python tool (3.10+) for the operations an agent should not improvise. It keeps no task state and makes no decisions. Every command prints one JSON object and exits non-zero when `ok` is false.

```text
python .agents/skills/lemmings/scripts/run.py <command> [--repo PATH] ...
```

## doctor

`doctor` reports Python, Git, and which host CLIs (`codex`, `claude`, `opencode`) are on PATH. It also lists the configured agents and flags agents whose host is missing, as well as native agent files that `agents sync` has not updated.

## agents

- `agents list` shows every agent with its role, host, model, `use`, default flag, escalation chain, and native subagent name.
- `agents sync [--check]` writes a native subagent definition for each configured agent that runs on Codex or Claude Code. For Codex this is `.codex/agents/lemmings-<name>.toml` with `model` and `model_reasoning_effort`; for Claude Code it is `.claude/agents/lemmings-<name>.md` with `model`. It also removes stale generated files. Files it did not generate are never overwritten. The installer runs `agents sync` automatically; run it again after editing `.agents/lemmings.json`.

## workspace

| Command | Effect |
| --- | --- |
| `workspace estimate` | Tracked bytes plus the Unity `Library` a new copy will regenerate. |
| `workspace create <slug> [--clone] [--base REV] [--branch NAME] [--approve-large]` | Creates branch `task/<slug>` from `--base` in `<root>/<slug>` as a linked worktree (default) or a standalone clone. Refuses above the size limit unless the user approved it and you pass `--approve-large`. |
| `workspace list` | Registered workspaces with `exists`, `head`, and `dirty`. |
| `workspace remove <slug> [--keep-branch]` | Removes a clean workspace. For a worktree it uses `git worktree remove` and then deletes the task branch only if it is merged (`git branch -d`); an unmerged branch is kept and reported. For a clone, every local branch head must already exist in the primary repository, and there must be no stash. |

The default root is `../lemmings-worktrees`. The registry lives in `<git-common-dir>/lemmings/workspaces.json`. Integrate a clone by fetching its branch from the primary repository, for example `git fetch <clone-path> task/<slug>`.

## scope

```text
scope --base <sha> [--head HEAD | --worktree] --owned <path-or-glob>... [--forbidden <path-or-glob>...]
```

Lists every path touched between base and head (renames count on both sides; `--worktree` adds uncommitted and untracked files) and the paths that break the rules. A plain path owns itself and everything below it. `*`, `?`, and `**` are globs. Paths that escape the repository are rejected.

## dispatch

```text
dispatch (<worker|reviewer|explorer> | --agent <name>) --brief brief.md [--cwd <worktree>] [--host codex|claude|opencode] [--model M] [--effort E] [--timeout 1800] [--dry-run]
```

Runs one agent (by name, or the role's default) in a fresh session of another host CLI, using that CLI's own login and configuration (no secrets are read or copied). The brief goes in on stdin after a short role preamble. Reviewers and explorers are read-only: Codex `--sandbox read-only`, Claude without Edit/Write, OpenCode without edit/bash. Workers get workspace-write in `--cwd`.

The result contains `status` (`completed`, `failed`, `model-mismatch`, `empty-report`, or `native` when the agent should run as a native subagent instead), `agent`, `report` (the agent's final answer), `verdict` (parsed from the reviewer's `VERDICT:` line), `observedModels`, `modelConfirmed`, `usage`, `elapsedSeconds`, `fallbackUsed`, and `runDir`. Treat `model-mismatch` as a routing error to report, not something to work around.

Every run is recorded in `<git-common-dir>/lemmings/runs/<timestamp>-<role>-<agent>-<id>/` (`brief.md`, `output-N.log`, `report.md`, `result.json`). The optional `packages/lemmings-telemetry` package summarizes these logs.

## Agents

Lemmings ships default agents in `skills/lemmings/defaults.json`, which works without any configuration:

- **Codex:** `codex-worker` (gpt-5.6-luna, high) escalates to `codex-worker-strong` (gpt-5.6-sol, high). `codex-reviewer` uses gpt-5.6-sol, high; `codex-explorer` uses gpt-5.6-luna, medium.
- **Claude Code:** `claude-worker` (sonnet) escalates to `claude-worker-strong` (opus). `claude-reviewer` uses opus; `claude-explorer` uses haiku.

Each shipped agent serves only its own host (`for`). A Codex manager never depends on the Claude CLI, and a Claude manager never depends on Codex.

`.agents/lemmings.json` → `agents` adds agents or overrides shipped ones by name. A project agent with `default: true` replaces the shipped default of its role for the hosts in its `for`. `"defaults": false` drops the shipped agents entirely. Example that adds cheaper and specialized agents:

```json
{
  "agents": {
    "deepseek": {"role": "worker", "host": "codex", "profile": "byteplus", "model": "deepseek-v4-pro-260425",
                 "use": "Routine, well-specified implementation", "default": true, "escalateTo": "luna-max"},
    "luna-max": {"role": "worker", "host": "codex", "model": "gpt-5.6-luna", "effort": "max",
                 "use": "Hard or cross-cutting changes; takes over when a cheaper worker fails"},
    "sol":      {"role": "reviewer", "host": "codex", "model": "gpt-5.6-sol", "effort": "high", "default": true,
                 "escalateTo": "opus"},
    "opus":     {"role": "reviewer", "host": "claude", "model": "opus",
                 "use": "Security, concurrency, and data-loss risks",
                 "fallback": [{"host": "codex", "model": "gpt-5.6-sol"}]},
    "scout":    {"role": "explorer", "host": "native", "use": "Codebase questions"}
  },
  "workspace": {"root": "../lemmings-worktrees", "largeThresholdGiB": 10}
}
```

- **name**: lowercase words joined by hyphens, and not `worker`, `reviewer`, or `explorer`. The native subagent is called `lemmings-<name>`.
- **role**: `worker`, `reviewer`, or `explorer`.
- **for**: the manager hosts that may use this agent, `codex` and/or `claude` (default: both).
- **host**: `native`, `codex`, `claude`, or `opencode`. `native` means the manager's own host on its default model, so it cannot pin a model. When the host equals the manager's host, the agent runs as a native subagent. Any other host, or a Codex agent with a `profile`, runs through `dispatch`. OpenCode models use `provider/model`.
- **model**, **effort**: passed to the host (`--model`; Codex `model_reasoning_effort`, Claude `--effort`, OpenCode `--variant`). If omitted, the host's default is used.
- **profile**: optional Codex profile, for example another provider. Only `codex exec --profile` can apply it, so such an agent always runs through `dispatch`.
- **use**: what this agent is good at. The manager reads it to choose an agent for each brief.
- **default**: the role's first choice for the hosts in `for`. If a host has only one agent for a role, that agent becomes its default automatically. `dispatch <role>` needs `--manager codex|claude` when both hosts have defaults.
- **escalateTo**: another agent of the same role. It takes over when the manager decides the current agent cannot finish the task. Chains are followed in order; cycles are rejected.
- **fallback**: routes tried automatically by `dispatch` only when a CLI is missing, fails, or times out. A fallback is never used after a model mismatch, and the result reports `fallbackUsed`. Passing `--host` or `--model` disables fallback. Escalation is the manager's quality decision; fallback is a transport retry.
