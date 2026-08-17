---
name: lemmings
description: Coordinate repository delivery with proportional Simple, Standard, or Strict orchestration. Use when a user invokes $lemmings, asks to enable or inspect Lemmings, delegates repository implementation, needs commit-range review, isolated writers, shared-contract safety, integration evidence, or pipeline metrics.
---

# Lemmings

Own orchestration as the sole manager. Never delegate orchestration. Select the lightest safe mode:

- Simple: one local writer, no canonical artifacts.
- Standard: one writer, one schema-v2 Task, immutable Reviews.
- Strict: parallel writers, shared contracts, serialized assets, submodules, codegen, or external resources; add a schema-v2 Phase, isolation, leases, baseline review, and integration evidence.

Run `Discover → Plan → Refine → Implement → Verify`. Dispatch implementation only after the Task is `Ready`. Freeze shared contracts before parallel work. Preserve explicit user mode/model/workspace pins. Treat `Accepted` and `Integrated` as different states; a second failed review becomes `Replan Required`.

Derive a role-specific ContextPacket v2 from Task/Phase. Never forward transcripts, reasoning, raw logs, secrets, absolute paths, or unrelated documents. Malformed schema-v2 artifacts block lifecycle operations. Context budget excesses warn but do not suppress packet injection. Read [context-contract.md](references/context-contract.md) before dispatch.

Require every worker to stay inside owned paths, test mapped risks, record actual model and validation, embed a concise handoff, and create a candidate/fix commit. Review the exact candidate range. Validate integration before close. Run `lemmings check`; use `lemmings check --all` for Strict work.

Read only the relevant reference:

- [contracts.md](references/contracts.md): schema-v2 artifact fields and state rules.
- [model-routing.md](references/model-routing.md): role defaults, pins, and escalation.
- [game-projects.md](references/game-projects.md): code worktree, package worktree, or Unity clone selection.
- [telemetry.md](references/telemetry.md): optional privacy-bounded v2 metrics.

Controls: `$lemmings on|off|auto|simple|standard|strict|status`; `$lemmings models ...`; `$lemmings workspace ...`. Enabling writes a schema-v2 runtime marker when hooks are installed. A v1 marker or artifact is unsupported and must be replaced, never migrated.
