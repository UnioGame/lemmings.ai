---
name: lemmings
description: Coordinate repository delivery with proportional Simple, Standard, or Strict orchestration. Use when a user invokes $lemmings, asks to enable or inspect Lemmings, assigns models, delegates repository implementation, needs commit-range review, parallel writer isolation, shared-contract safety, or integration evidence.
---

# Lemmings

Choose the lightest safe mode:

- Use Simple for one agent making a local change. Create no Lemmings artifacts.
- Use Standard for one writer with sequential exploration or validation. Maintain one task packet and one immutable review.
- Use Strict for parallel writers, shared contracts, Unity serialized assets, submodules, code generation, or external resources. Add a phase baseline, isolated worktrees, leases, and integration evidence.

Preserve user model pins. Assign `gpt-5.6-sol:high` to orchestrator and reviewer and `gpt-5.6-sol:medium` to complex worker unless the user pins that role. Record `requested`, `assigned`, and `actual`; record a fallback reason only when fallback occurs.

Run the five stages: Prepare, Dispatch, Execute/Candidate, Review/Repair, Integrate/Close. Keep dispatch derived, embed handoff under task execution, and embed integration evidence under close. Treat Accepted and Integrated as distinct states. Review the actual candidate/fix head. After a second ChangesRequested verdict, move to Replan Required.

Before finishing, run `lemmings check`; use `lemmings check --all` for a full Strict lifecycle audit. Read [workflow.md](references/workflow.md) for gates and hooks, [contracts.md](references/contracts.md) when authoring artifacts, and [commands.md](references/commands.md) for exact CLI usage.
