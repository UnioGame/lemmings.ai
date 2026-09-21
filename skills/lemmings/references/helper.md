# Lemmings helper

A small standard-library Python tool (3.10+) for the operations an agent should not improvise. It keeps no task state and makes no decisions. Every command prints one JSON object and exits non-zero when `ok` is false.

```text
python .agents/skills/lemmings/scripts/run.py <command> [--repo PATH] ...
```

## doctor

`doctor` reports Python, Git, which host CLIs (`codex`, `claude`, `opencode`) are on PATH, and the resolved route for each role.

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
dispatch <worker|reviewer|explorer> --brief brief.md [--cwd <worktree>] [--host codex|claude|opencode] [--model M] [--effort E] [--timeout 1800] [--dry-run]
```

Runs one role in a fresh session of another host CLI, using that CLI's own login and configuration (no secrets are read or copied). The brief goes in on stdin after a short role preamble. Reviewers and explorers are read-only: Codex `--sandbox read-only`, Claude without Edit/Write, OpenCode without edit/bash. Workers get workspace-write in `--cwd`.

The result contains `status` (`completed`, `failed`, `model-mismatch`, `empty-report`, `native`), `report` (the agent's final answer), `verdict` (parsed from the reviewer's `VERDICT:` line), `observedModels`, `modelConfirmed`, `usage`, `elapsedSeconds`, `fallbackUsed`, and `runDir`. Treat `model-mismatch` as a routing error to report, not something to work around.

Every run is recorded in `<git-common-dir>/lemmings/runs/<timestamp>-<role>-<id>/` (`brief.md`, `output-N.log`, `report.md`, `result.json`). The optional `packages/lemmings-telemetry` package summarizes these logs.

## Role routes

Assign hosts and models in `.agents/lemmings.json`. A missing role means `native`, which is the current host's own subagent. The helper is not used for native roles.

```json
{
  "roles": {
    "worker":   {"host": "native"},
    "reviewer": {"host": "claude", "model": "opus", "effort": "high",
                 "fallback": [{"host": "codex", "model": "gpt-5.6-sol"}]},
    "explorer": {"host": "native"}
  },
  "workspace": {"root": "../lemmings-worktrees", "largeThresholdGiB": 10}
}
```

- `host`: `native`, `codex`, `claude`, or `opencode`. OpenCode models use `provider/model`.
- `model`, `effort`: passed to the host (`--model`; Codex `model_reasoning_effort`, Claude `--effort`, OpenCode `--variant`). If omitted, the host's default is used.
- `profile`: optional Codex profile name.
- `fallback`: tried in order only when the CLI is missing, exits with an error, or times out. It is never used after a model mismatch, and the result reports `fallbackUsed`. Passing `--host` or `--model` on the command line disables fallback.
