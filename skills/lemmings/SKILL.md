---
name: lemmings
description: Coordinate and optionally measure repository delivery through Discover, Plan, Refine, Implement, and Verify with proportional Simple, Standard, or Strict orchestration. Use when a user invokes $lemmings, asks to discover or plan repository work, refine a task before implementation, enable or inspect Lemmings, assign models, delegate implementation, review a commit range, isolate parallel writers, protect shared contracts, collect integration evidence, or evaluate pipeline performance and regressions.
---

# Lemmings

This smart skill is the sole orchestrator. Do not delegate orchestration to a second skill, a command file, or an agent role. It owns lifecycle selection, task controls, workspace resolution, and dispatch.

Use task-scoped controls: `$lemmings on|off|auto|simple|standard|strict|status`; `$lemmings models <role>=<model>` or `$lemmings models task <task-id> <role>=<model>`; and `$lemmings workspace auto|current|isolated|status` or `$lemmings workspace task <task-id> auto|current|isolated`. `auto` is the hybrid default. A task model or workspace override wins without changing repository defaults.

`$lemmings on` enables this skill for the current task. If optional hooks are installed, atomically write their tracked-artifact references to `<git-common-dir>/lemmings/active.json`; `$lemmings off` removes that local marker and stops using the skill for the task. Neither command installs tooling or hooks. `$lemmings status` reports the effective mode, models, workspace, current lifecycle step, artifacts, review head, validation debt, and blockers.

Choose the lightest safe mode:

- Use Simple for one agent making a local change. Create no Lemmings artifacts.
- Use Standard for one writer with sequential exploration or validation. Maintain one task packet and one immutable review.
- Use Strict for parallel writers, shared contracts, Unity serialized assets, submodules, code generation, or external resources. Add a Phase baseline, leases, integration evidence, and isolated worktrees only when parallelism or workspace safety requires them.

Preserve an explicit user selection until they change it. Optional tooling is discovered in this order: `.git/lemmings/environment.json`, repo-relative `tooling.root` in `.codex/lemmings.json`, a package whose `package.json` name is `unigame.ai.lemmings`, then native Git/shell fallback. Run the module from the discovered root with `python -m lemmings`; Python is optional.

Preserve valid user model pins. The reviewer always uses `gpt-5.6-sol:high`. The orchestrator defaults to `gpt-5.6-sol:high` and may use higher Sol effort only when the user explicitly requests it. Assign `gpt-5.6-luna:max` to a bounded Ready worker task with clear acceptance, owned paths, frozen contracts, focused validation, and no unresolved architectural decision. Assign `gpt-5.6-terra:max` to the same worker role for an elevated task spanning several related files or subsystems, or requiring large-context analysis, when scope and contracts remain well specified and the risk is not critical. Assign `gpt-5.6-sol:medium` before spawn when root cause is ambiguous, architecture or public contracts change, tests are weak, or risks include shared/serialized resources, concurrency, networking, persistence, security, payments, or critical performance. Keep the validator on `gpt-5.6-terra:medium`. A bounded repair may stay on its assigned model; escalate an underestimated Luna task to Terra, and route P0/P1, invalidated contracts or plan, or demonstrated architectural misunderstanding to Sol Medium. A second failed review remains `Replan Required`. Record `requested`, `assigned`, and `actual`; record a fallback reason only when actual differs from assigned.

The orchestrator must follow `Discover → Plan → Refine → Implement → Verify`:

- Discover the bounded code paths, baseline, constraints, dependencies, risks, and blocking unknowns.
- Plan the goal, non-goals, acceptance criteria, ownership, model assignments, dependencies, and risk-to-test mapping.
- Refine by challenging assumptions, resolving blocking unknowns, reviewing plans only when policy requires it, freezing shared contracts, and declaring the task Ready.
- Implement only Ready work through derived dispatch, owned changes, focused validation, embedded handoff, and candidate/fix commits.
- Verify the actual candidate range, repair bounded findings, integrate Accepted work, rerun integration validation, and close or record owned debt.

Use a hybrid workspace by default. Use the current checkout only for safe serial work; use isolated workspaces for Strict or concurrent writers. For game projects, choose among a code worktree, package worktree, and Unity clone as [game-projects.md](references/game-projects.md) directs. Estimate space before provisioning: if more than 10 GiB, ask permission. If declined, use serial current-checkout work; if that is unsafe, set the task state to Blocked and explain why.

Worktree or Unity-clone approval gates only workspace provisioning. Missing, pending, or declined approval never disables the Lemmings lifecycle, canonical artifacts, or task workers: continue Discover, Plan, and Refine; allow read-only exploration, review, and validation workers; and use implementation workers sequentially in the current checkout when that workspace is safe and permitted. Block only the write or validation step that genuinely requires unavailable isolation, not the pipeline or unrelated workers.

Telemetry is optional and off by default. When `lemmings metrics status` reports `basic` or `full`, enter each lifecycle boundary with `lemmings metrics stage <name>` and finish every run with `lemmings metrics finish --outcome <outcome>`. Telemetry failure never weakens or blocks policy enforcement. Do not record prompts, transcripts, reasoning, tool payloads, source, diffs, secrets, or absolute paths.

Do not dispatch an implementation worker before Refine has made the task Ready. Internally, `Prepare` contains Discover, Plan, and Refine; `Dispatch` plus `Execute/Candidate` implements; `Review/Repair` plus `Integrate/Close` verifies. Simple compresses the cycle without artifacts. Standard records one Task and one immutable Review. Strict adds a Phase baseline, frozen contracts, leases, close evidence, and isolation where the task actually requires it. Only Task, Phase, and Review are canonical artifacts; embed handoff, validation, and close evidence in them. A verification finding may return bounded work to Implement; after the second failed review, use Replan Required and restart from the earliest invalidated step. Treat Accepted and Integrated as distinct states.

Before finishing, run `lemmings check`; use `lemmings check --all` for a full Strict lifecycle audit. Read [contracts.md](references/contracts.md) when authoring artifacts, [game-projects.md](references/game-projects.md) for game workspace selection, and [telemetry.md](references/telemetry.md) when measuring or comparing the pipeline.
