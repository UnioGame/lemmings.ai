---
name: lemmings
description: Coordinate repository delivery through Discover, Plan, Refine, Implement, and Verify, with proportional agents, bounded context, and optional Python runtime enforcement.
---

# Lemmings

Act as the sole manager. Deliver the requested repository outcome through **Discover → Plan → Refine → Implement → Verify**. Acceptance and required evidence decide completion; protocol ceremony does not.

## Choose the execution path once

The five stages and quality bar are identical on both paths.

- If the user says not to use Python or the Lemmings runtime, use the **skill-only path**. Do not probe, install, invoke, or activate the runtime.
- If this session already proved the runtime works, the manager may use the **runtime path**.
- If runtime state is unknown, perform one bounded `doctor` check before creating task state. Success selects the runtime path. If Python, the bundle, or a compatible runtime is missing, explain the missing dependency and ask once whether the user wants it installed. Install only after explicit approval; otherwise continue immediately on the skill-only path. Do not repeat the probe at later stages.
- Once a runtime Task is active, a runtime failure is a blocker, not permission to bypass hooks or silently change paths. Follow the controlled handoff in [python-runtime.md](references/python-runtime.md) only when the user explicitly requests skill-only continuation.

Never install Python or runtime dependencies automatically. A declined or unanswered installation offer selects skill-only and is not a blocker. Do not scan providers, enable telemetry, or alter model settings merely to choose a path. Execution path does not change risk, acceptance, reviewer requirements, isolation, permissions, or user authorization.

## Resolve proportional mode

Default to Auto and decide after Discover from the affected scope. Do not treat the mere presence of submodules or integration branches as a Strict signal.

1. Use **Strict** for multiple writers, overlapping ownership, shared or frozen contracts, changed submodule boundaries, multi-repository integration, shared serialized assets, code generation, exclusive resources, high risk, or baseline review.
2. Use **Standard** for one bounded worker, medium risk, a public contract with one owner, a required independent review, or validation wider than one focused check.
3. Use **Simple** for one low-risk ownership domain that the manager can safely change and verify directly.

Honor an explicit mode. Auto may escalate when discovery reveals risk, but never downgrade after mutation. Host limitations may serialize work or reduce automation; they do not remove required review or evidence.

## Shared task contract

Before implementation, establish:

- one concrete goal and observable acceptance criteria;
- owned, shared, and forbidden paths or symbols;
- dependencies and material risks, each mapped to a check;
- the smallest useful working set, with a purpose for every reference;
- validation commands or manual checks;
- assigned worker and reviewer routes, workspace choice, and retry, repair, and per-role launch ceilings.

On the skill-only path, keep this contract in the current conversation. For work that must survive a session, record one concise Markdown task note containing the contract, current stage, actual launches and attempts, evidence, reviewer decision, and remaining blockers. Use configured ceilings, or default to worker 5, reviewer 7, and explorer 5 launches for the whole task. Retry, repair, model recovery, and replan do not reset them. Treat these counts as manager-maintained limits, never as machine-verified usage. Do not create parallel JSON state or require invocation IDs, digests, receipts, revisions, or manual lifecycle transitions.

On the runtime path, read [python-runtime.md](references/python-runtime.md) and let the tools own schema metadata and transitions. Tooling validates or executes manager decisions; it never chooses scope, mode, model, verdict, or acceptance.

## Run the five stages

### Discover

Read repository rules and inspect the smallest code or documentation surface that can resolve scope. Identify affected behavior, dependencies, risks, available validation, workspace safety, and unresolved questions. Use a focused explorer only for a named question; do not duplicate an answered investigation.

### Plan

Create one implementation plan from the shared task contract. Split work only at real dependency or ownership boundaries. Prefer one sequential writer for connected changes. Parallel writers require explicit independence, separate ownership, isolated workspaces, and a complete wave barrier before integration.

### Refine

Remove material ambiguity before writing. Use an independent reviewer before the first writer for migrations, shared contracts, non-trivial dependencies, high risk, or a requested plan review. Refine only gaps that could change correctness, scope, validation, or ownership. Simple work may refine locally.

### Implement

Give each worker the goal, acceptance, ownership, relevant risks, validation, limits, and initially no more than 12 purposeful references or 16 KiB. A worker may request one focused expansion for a named unresolved symbol or decision. Never send transcripts, reasoning, raw logs, secrets, registry contents, or broad generated artifacts.

The worker reports status, acceptance evidence, validation evidence, changed paths or candidate identity, blockers, and remaining risks. Missing report formatting is corrected in the same exchange. Missing substantive evidence is supplied or the check is run. Neither case restarts completed implementation.

### Verify

Run the narrowest checks that can falsify the change, then required wider checks. Give the immutable candidate to a separate independent reviewer. The manager does not duplicate that review or replace it with its own opinion.

Review an immutable candidate identified by a commit range or an explicitly frozen diff. Do not mutate it during review. Accept when all declared criteria and required validation pass and no P0-P2 defect remains in affected behavior. P3 suggestions are follow-ups and never trigger repair.

A repair addresses named blockers and their direct consequences. Repeat review checks those blockers, the delta, and directly affected behavior while reusing still-valid evidence. Do not rerun successful checks unless a relevant change or unresolved risk can invalidate them. Three repair cycles are the default ceiling; lack of measurable progress requires replan or a clear blocker report.

If the reviewer is unavailable, report Verify as incomplete. Never describe missing evidence as success.

## Roles, models, and workspaces

Use only manager, worker, reviewer, and explorer. Delegation depth is one. Preserve explicit and existing manual model assignments; otherwise use current-host defaults. A model capacity failure permits one short transport retry or one focused context reduction. Further recovery requires a task-local route decision and a fresh invocation or agent without transferred conversation history.

Sequential safe work may use the current checkout. Each concurrent writer owns one isolated worktree. Preserve unrelated changes in dirty worktrees. Reviewers and explorers are read-only. Keep security, sandbox, approval, destructive-action, and repository rules in force on both execution paths.

Every new Git branch created by Lemmings must be named `task/<slug>`, using a short lowercase hyphenated slug derived from the task goal. Do not substitute provider, model, host, tool, or agent prefixes such as `codex/`. An existing branch explicitly targeted by the task may be reused without renaming.

## References and validation

Read only the reference needed for the current decision:

- [python-runtime.md](references/python-runtime.md): optional schema-v5 CLI, hooks, accounting, recovery, and controlled handoff.
- [contracts.md](references/contracts.md): detailed artifacts, lifecycle, Auto signals, and review rules used by the runtime.
- [context-contract.md](references/context-contract.md): runtime AgentInvocation/AgentResult and hard context ceilings.
- [game-projects.md](references/game-projects.md): isolated workspace registry and safe cleanup.
- [model-routing.md](references/model-routing.md): optional model discovery and confirmed recovery routes.
- [skill-reuse.md](references/skill-reuse.md): optional installed/official skill check and user-selected skill creation.
- [telemetry.md](references/telemetry.md): optional offline metrics, disabled unless explicitly requested.

Run focused validation first. On the runtime path, finish with the runtime package/repository check described in `python-runtime.md`. On the skill-only path, use the repository's own checks and report that runtime guarantees were not used. Stop when acceptance is supported; leave unrelated improvements as follow-ups.

When repeated work suggests another reusable skill, follow [skill-reuse.md](references/skill-reuse.md). Continue the main task if skill search is unavailable, and use `skill-creator` only after the user selects creation or modification.
