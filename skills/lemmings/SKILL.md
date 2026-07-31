---
name: lemmings
description: Coordinate repository delivery with proportional Simple, Standard, or Strict orchestration. Use when a user invokes $lemmings, asks to enable or inspect Lemmings, assigns models, delegates repository implementation, needs commit-range review, parallel writer isolation, shared-contract safety, or integration evidence.
---

# Lemmings

Choose the lightest safe mode:

- Use Simple for one agent making a local change. Create no Lemmings artifacts.
- Use Standard for one writer with sequential exploration or validation. Maintain one task packet and one immutable review.
- Use Strict for parallel writers, shared contracts, Unity serialized assets, submodules, code generation, or external resources. Add a phase baseline, isolated worktrees, leases, and integration evidence.

Preserve valid user model pins. The reviewer always uses `gpt-5.6-sol:high`. The orchestrator defaults to `gpt-5.6-sol:high` and may use higher Sol effort only when the user explicitly requests it. The complex worker defaults to `gpt-5.6-sol:medium`; users may pin worker roles. Record `requested`, `assigned`, and `actual`; record a fallback reason only when fallback occurs.

Treat Prepare, Dispatch, Execute/Candidate, Review/Repair, and Integrate/Close as logical stages. In Simple they collapse to local prepare, execute, validate, and close: create no delegation, candidate commit, or Sol review unless the user asks. In Standard and Strict, keep dispatch derived, embed handoff under task execution, and embed integration evidence under close. Treat Accepted and Integrated as distinct states. Review the actual candidate/fix head. After a second ChangesRequested verdict, move to Replan Required.

Before finishing, run `lemmings check`; use `lemmings check --all` for a full Strict lifecycle audit. Read [workflow.md](references/workflow.md) for gates and hooks, [contracts.md](references/contracts.md) when authoring artifacts, and [commands.md](references/commands.md) for exact CLI usage.
