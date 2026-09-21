---
name: lemmings
description: Deliver a repository change through Discover, Plan, Implement, and Verify, scaling from direct work to an independent reviewer or parallel workers in isolated worktrees. Use for coding tasks that benefit from delegation, independent review, or parallel implementation.
---

# Lemmings

You are the manager. Deliver the requested outcome with the least process that still proves it works. The acceptance criteria and passing checks decide when you are done; paperwork does not.

## 1. Discover

Read the repository rules (`AGENTS.md`, `CLAUDE.md`, contributing docs) and the smallest code surface that settles the scope. Identify the affected behavior, how to validate it, and the risks. If a game engine is present, read the matching file in [rules/](rules/) (`unity.md`, `unreal.md`, `godot.md`, `defold.md`, `flutter.md`, `phaser.md`, `pixijs.md`; `platforms.md` for an explicit platform target). Send an explorer only for a named question you cannot answer cheaply yourself.

## 2. Plan and choose a mode

Write a brief. It is the only task contract. Keep it short and pass it to agents as-is:

```markdown
Goal: <one sentence>
Acceptance:
- <observable criterion>
Owned paths: <paths/globs the writer may change>
Checks: <commands or manual checks that can falsify the change>
Risks: <only material ones, each with the check that covers it>
Context: <at most ~10 files/symbols worth reading first, each with a reason>
```

The mode is **Auto** unless the user names one. Honor a named mode.

| Mode | When | What happens |
| --- | --- | --- |
| **Auto** (default) | The user did not name a mode | You choose Simple, Standard, or Parallel after Discover, using the order below. |
| **Simple** | One low-risk area you can change and verify yourself | You implement and run the checks. No reviewer unless the user asks. |
| **Standard** | Medium risk, a public contract, broad validation, or the user wants review | One writer (you or a worker), then one independent reviewer. |
| **Parallel** | Independent pieces with non-overlapping owned paths that are worth doing concurrently | One worker per piece, each in its own worktree; review each; integrate; run the checks on the merged result. |

How Auto decides, from the affected scope only (a repository merely having submodules or many packages is not a signal). Check the modes in this order and take the first that fits:
1. **Parallel**: at least two pieces that are independent, have separate owned paths, and are each big enough that concurrency saves real time.
2. **Standard**: medium or high risk; a public or shared contract, migration, security, data, or concurrency change; generated code or serialized assets; changes across several areas; validation wider than one focused check; or the user asks for review. For a migration, shared contract, or high risk, also get a plan review before code is written.
3. **Simple**: everything else, meaning one low-risk area.

State the chosen mode and the reason in one line before implementing. Auto may escalate later: Simple becomes Standard when hidden risk shows up, and Standard becomes Parallel only before implementation starts. It never downgrades once changes exist. Host limits, such as no worktrees or no subagents, may serialize Parallel work but never remove the required review.

Pick the agent for each brief now (see [Agents and models](#agents-and-models)) and name it in the brief. Split work only at real ownership boundaries. Connected changes stay with one sequential writer. If the plan has a real ambiguity that could change correctness or scope, resolve it now (ask the user or send the brief to a reviewer for a plan check) before anyone writes code.

## 3. Implement

- **Simple**: make the change in the current checkout.
- **Standard**: implement yourself, or give the brief to one worker agent. The current checkout is fine for a single writer unless it has unrelated uncommitted changes; then create a worktree.
- **Parallel**: create one worktree per worker, dispatch all workers of the wave, and wait for every one of them before integrating anything.

Workers get the brief, not the conversation. A worker may ask one focused question when the brief is missing something; answer it and continue. A worker commits on its branch and reports: status, commit, changed paths, check results, and remaining risks. If the report is missing a piece, ask for that piece; never redo finished work just to fix a report.

New branches are named `task/<short-lowercase-slug>`. Reuse a branch the user explicitly targets.

## 4. Verify

1. Run the checks from the brief, narrowest first. A failing or truncated check is not a pass.
2. In Standard and Parallel, confirm the writer stayed inside its owned paths (use `lemmings scope` when the helper is available, otherwise `git diff --name-only <base>..<head>`).
3. In Standard and Parallel, send the brief plus the candidate range (`<base>..<head>`) to a separate reviewer agent: the default reviewer, plus any other reviewer whose `use` matches a material risk (for example security). Every chosen reviewer's P0–P2 findings block. Do not replace the review with your own opinion, and do not change the candidate while it is under review.
4. The reviewer returns `Accepted` or `ChangesRequested`:
   - P0–P2 findings block. Each names a concrete failure scenario (unmet criterion, failed check, correctness, security, data loss, or real regression) with evidence.
   - P3 findings are follow-ups. They never block acceptance and never trigger a repair.
5. On `ChangesRequested`, send only the blocking findings back to the writer. The re-review checks those findings and the new delta, not the whole change again. Each writer gets at most **2 repair rounds**; after that, escalate (below) or, when no escalation is left, stop and report the blocker or re-plan with the user.
6. In Parallel, merge the accepted branches one at a time and run the checks on the merged result.

If a required reviewer is unavailable, report Verify as incomplete. Never present missing evidence as success.

Finish with a short report: what changed, check results, review verdict, and any follow-ups. Remove worktrees you created once their branches are merged.

## Agents and models

Roles are worker (writes within owned paths), reviewer (read-only), and explorer (read-only). Agents never delegate further.

**Which agents exist.** Lemmings ships ready-to-use agents for both hosts, and they are yours to use with no setup:

| Your host | Worker (default) | Escalation worker | Reviewer | Explorer |
| --- | --- | --- | --- | --- |
| Codex | `lemmings-codex-worker` (gpt-5.6-luna, high) | `lemmings-codex-worker-strong` (gpt-5.6-terra, high) | `lemmings-codex-reviewer` (gpt-5.6-sol, high) | `lemmings-codex-explorer` (gpt-5.6-luna, medium) |
| Claude Code | `lemmings-claude-worker` (sonnet) | `lemmings-claude-worker-strong` (opus) | `lemmings-claude-reviewer` (opus) | `lemmings-claude-explorer` (haiku) |

A project may add or override agents in `.agents/lemmings.json` → `agents`. Each agent has a `role`, a `host` and `model`, a `use` text that says what it is good at, and `for`, the manager hosts that may use it. It may also have `default: true` and `escalateTo`, a stronger agent of the same role. Use only agents whose `for` includes your host. `lemmings agents list` prints the effective set.

**Choosing.** For each brief, pick the agent whose `use` best fits the work; otherwise use the role's default for your host. Name the chosen agent in the brief and in your report. Never swap in a different model silently; if the chosen agent cannot run, say so.

**Running.**
- If the agent's host is `native`, or is your own host (`codex` inside Codex, `claude` inside Claude Code) and the agent has no Codex `profile`, start the native subagent `lemmings-<name>`. `lemmings agents sync` generates it with the pinned model.
- Otherwise run `lemmings dispatch --agent <name> --brief <file>`, which starts that host's CLI. If a shipped model is unavailable on your account, report it and ask the user which model to assign; do not fall back silently. See [references/helper.md](references/helper.md).

**Escalation.** Escalate when the current agent cannot finish: it reports blocked, the same check or finding survives two repair rounds without progress, or it keeps failing to run.
1. Stop the current agent.
2. Give its `escalateTo` agent a fresh session in the same worktree and branch. Send the brief plus a short handoff: HEAD, what was tried, the failing checks, and the open findings. Never send the previous agent's transcript.
3. The new agent gets its own two repair rounds.
4. Escalate a reviewer the same way when it cannot deliver a verdict.
5. Report every escalation. When the chain ends, stop and report the blocker.

## Optional helper

The skill works without Python. When Python 3.10+ is available, `python <this skill>/scripts/run.py <command>` (or `lemmings <command>` if installed) provides:

- `doctor`: host CLIs and configured role routes;
- `workspace create|list|remove|estimate`: isolated worktrees or clones with safe removal;
- `scope`: changed paths against owned and forbidden rules;
- `dispatch`: a role on Codex, Claude Code, or OpenCode with a deadline, a run log, and a model identity check.

Details: [references/helper.md](references/helper.md). Large game repositories: [references/game-projects.md](references/game-projects.md).

## Safety

Preserve unrelated changes in a dirty checkout. Never force-push, reset, or clean without the user's explicit request. Ask before creating any workspace estimated above 10 GiB. Security, sandbox, and approval rules of the host always apply.

When a repeated process looks like it deserves its own skill, see [references/skill-reuse.md](references/skill-reuse.md).
