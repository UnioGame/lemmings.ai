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

Preserve valid user model pins. The reviewer and Discover/architecture/Plan/Refine orchestrator use `gpt-5.6-sol:high`; higher orchestrator effort is explicit-only. Assign `gpt-5.6-luna:max` to a bounded Ready worker task with clear acceptance, owned paths, frozen contracts, focused validation, and no unresolved architectural decision. Assign `gpt-5.6-terra:max` to the same worker role for large context, several subsystems, or a failed Luna attempt caused by insufficient implementation understanding. The automatic worker ladder ends at Terra Max. `gpt-5.6-sol:medium`, `gpt-5.6-sol:high`, and `gpt-5.6-sol:max` are valid explicit worker pins only; a request such as “execute on Sol High” must be copied to both `models.requested` and `models.assigned`. A user pin always wins. A plan or contract defect returns to Refine and never escalates the worker model. Use `gpt-5.6-luna:high` for bounded exploration, `gpt-5.6-terra:medium` for declared validation, and `gpt-5.6-luna:medium` for evidence compression. A second failed review remains `Replan Required`. Record `requested`, `assigned`, and `actual`; record a fallback reason only when actual differs from assigned.

The orchestrator must follow `Discover → Plan → Refine → Implement → Verify`:

- Discover the bounded code paths, baseline, constraints, dependencies, risks, and blocking unknowns.
- Plan the goal, non-goals, acceptance criteria, ownership, model assignments, dependencies, and risk-to-test mapping.
- Refine by challenging assumptions, resolving blocking unknowns, reviewing plans only when policy requires it, freezing shared contracts, and declaring the task Ready.
- Implement only Ready work through derived dispatch, owned changes, focused validation, embedded handoff, and candidate/fix commits.
- Verify the actual candidate range, repair bounded findings, integrate Accepted work, rerun integration validation, and close or record owned debt.

Use a hybrid workspace by default. The Lemmings lifecycle and subagents may run in the current checkout without special user approval; implementation workers may also work there sequentially when ownership and dirty-state safety allow it. Multiple implementation writers must never write concurrently in the current checkout: give each an isolated workspace. Before provisioning any `code-worktree`, `package-worktree`, or full `unity-clone`, estimate its copied workspace size and obtain explicit approval when it exceeds 10 GiB, for Unity and non-Unity repositories alike. Pending or declined approval gates only that provisioning: continue Discover, Plan, and Refine; allow read-only workers, reviewers, and validators; and use sequential implementation in the current checkout when safe. Block only the step that genuinely requires the unavailable workspace. Use [game-projects.md](references/game-projects.md) for game-specific selection.

Tracked quality metrics are mandatory for Standard and Strict tasks and work when local telemetry is off. For every candidate or fix, append `execution.attempts[]` with attempt number, kind, actual model, head SHA, failed validation count, and the immutable Review reference/status; append every Review reference to `reviewHistory`. The reviewer classifies every finding by P0-P3 and by `implementation`, `plan-contract`, `validation`, or `integration`. At pipeline close, always run `lemmings metrics finish --task <task-json> --outcome <outcome>`; it writes `qualitySummary` and prints the task and cumulative model report. Present its worker attempts, findings by origin/priority, repeated review and repair counts, validation failures, first-pass result, and Luna-to-Terra escalation in the final handoff. Legacy/incomplete tasks remain visible but do not enter comparisons.

Local timing telemetry is optional and off by default. When `lemmings metrics status` reports `basic` or `full`, also enter each lifecycle boundary with `lemmings metrics stage <name>`. Telemetry failure never weakens tracked quality or policy enforcement. Do not record prompts, transcripts, reasoning, tool payloads, source, diffs, secrets, or absolute paths.

Do not dispatch an implementation worker before Refine has made the task Ready. Internally, `Prepare` contains Discover, Plan, and Refine; `Dispatch` plus `Execute/Candidate` implements; `Review/Repair` plus `Integrate/Close` verifies. Simple compresses the cycle without artifacts. Standard records one Task and one immutable Review. Strict adds a Phase baseline, frozen contracts, leases, close evidence, and isolation where the task actually requires it. Only Task, Phase, and Review are canonical artifacts; embed handoff, validation, and close evidence in them. A verification finding may return bounded work to Implement; after the second failed review, use Replan Required and restart from the earliest invalidated step. Treat Accepted and Integrated as distinct states.

Before finishing, run `lemmings check`; use `lemmings check --all` for a full Strict lifecycle audit. Read [contracts.md](references/contracts.md) when authoring artifacts, [game-projects.md](references/game-projects.md) for game workspace selection, and [telemetry.md](references/telemetry.md) when measuring or comparing the pipeline.
