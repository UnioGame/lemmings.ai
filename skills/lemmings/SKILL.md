---
name: lemmings
description: Coordinate repository delivery through Discover, Plan, Refine, Implement, and Verify with proportional Simple, Standard, or Strict orchestration. Use when a user invokes $lemmings, asks to discover or plan repository work, refine a task before implementation, enable or inspect Lemmings, assign models, delegate implementation, review a commit range, isolate parallel writers, protect shared contracts, or collect integration evidence.
---

# Lemmings

Choose the lightest safe mode:

- Use Simple for one agent making a local change. Create no Lemmings artifacts.
- Use Standard for one writer with sequential exploration or validation. Maintain one task packet and one immutable review.
- Use Strict for parallel writers, shared contracts, Unity serialized assets, submodules, code generation, or external resources. Add a phase baseline, isolated worktrees, leases, and integration evidence.

Persist the repository selection with `lemmings mode auto|simple|standard|strict`; inspect the configured and effective values with `lemmings mode status`. Preserve an explicit mode until the user selects another one.

Preserve valid user model pins. The reviewer always uses `gpt-5.6-sol:high`. The orchestrator defaults to `gpt-5.6-sol:high` and may use higher Sol effort only when the user explicitly requests it. The complex worker defaults to `gpt-5.6-sol:medium`; users may pin worker roles. Record `requested`, `assigned`, and `actual`; record a fallback reason only when fallback occurs.

The orchestrator must follow `Discover -> Plan -> Refine -> Implement -> Verify`:

- Discover the bounded code paths, baseline, constraints, dependencies, risks, and blocking unknowns.
- Plan the goal, non-goals, acceptance criteria, ownership, model assignments, dependencies, and risk-to-test mapping.
- Refine by challenging assumptions, resolving blocking unknowns, reviewing plans only when policy requires it, freezing shared contracts, and declaring the task Ready.
- Implement only Ready work through derived dispatch, owned changes, focused validation, embedded handoff, and candidate/fix commits.
- Verify the actual candidate range, repair bounded findings, integrate Accepted work, rerun integration validation, and close or record owned debt.

Do not dispatch an implementation worker before Refine has made the task Ready. Internally, `Prepare` contains Discover, Plan, and Refine; `Dispatch` plus `Execute/Candidate` implements; `Review/Repair` plus `Integrate/Close` verifies. Simple compresses the cycle without artifacts. Standard records it in one task packet. Strict adds the phase baseline, frozen contracts, isolated worktrees, leases, and close evidence. A verification finding may return bounded work to Implement; after the second failed review, use Replan Required and restart from the earliest invalidated step. Treat Accepted and Integrated as distinct states.

Before finishing, run `lemmings check`; use `lemmings check --all` for a full Strict lifecycle audit. Read [workflow.md](references/workflow.md) for gates and hooks, [contracts.md](references/contracts.md) when authoring artifacts, and [commands.md](references/commands.md) for exact CLI usage.
