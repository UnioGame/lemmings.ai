# Contracts and lifecycle

Write schema v3 artifacts. Schema v2 is unsupported and must be rejected; there is no read fallback or in-place migration. Task, Phase, and Review are the only canonical artifacts. Use their `revision` as compare-and-set; only the manager writes them.

Task records `requestedMode`, `resolvedMode`, risk class, reasons, capability degradations, writer count, review requirement, ownership, exclusive resources, compact working set, host/model assignment, workspace id/policy, validation, evidence, commits, review history, and close disposition. It never stores an absolute workspace path.

Task `specialization` is optional manager guidance only; the selected route in `models.assigned` remains authoritative. `reviewPolicy` accepts `single` or `cross`; cross-review reports use `reviewRef` plus `crossReviewRefs` and degrade non-blockingly to single review when a distinct provider/model is unavailable.

Auto resolves Strict before Standard before Simple. Strict signals are parallel writers, shared/frozen or overlapping contracts/domains, submodules/codegen/multiple repositories, shared serialized assets, exclusive resources, high risk, an integration branch, or baseline review. Standard signals are one specialist worker, one-owner public contract, medium risk, candidate/repair/review, or broad validation. Explicit pins remain fixed. Auto may escalate after discovery and may not downgrade after mutation.

State flow is `Draft → Ready → Active → Candidate → Accepted → Integrated`. Repair returns Candidate work to Active once. A second failed review becomes `Replan Required`. `Accepted` is not integration. `Integrated` requires candidate/fix SHA, merge/integration SHA, integration validation, evidence, and workspace disposition. It does not require `qualitySummary`, telemetry history, or a still-existing worktree.

Manager and worker may run a shell command only when it exactly matches `validation.commands`; its possible outputs must fit `allowedOutputs`. Reviewer and explorer are read-only. For `apply_patch`, check every parsed path. No provable path set blocks Strict and warns Standard. Candidate acceptance reads the real `base..head` path set once, checks ownership, and compares it with `AgentResult.changedPaths`; do not copy that list into Task. `execution.handoff` is an optional dependency note, never an acceptance gate.

`routingRecovery` is orthogonal to lifecycle state. `pending-confirmation` and `paused` block dispatch; `approved` contains only the selected task-scoped route chains, cursors, confirmation digest, trigger, and up to 12 compact attempts. Project routes, rejected options, catalogs, prompts, and telemetry never enter the Task. Manager updates and recovery advance use Task revision CAS.

Before a writer wave, compute dependency-ready tasks. Select at most two, require an allowed `parallelReason`, distinct isolated workspaces, non-overlapping owned/shared paths, and non-conflicting exclusive resources. With no isolation or slots, serialize the same plan. Wait for the complete wave, accept matching results, then integrate deterministically.

Strict adds a Phase with baseline/integration head, frozen contracts, task DAG, leases, optional baseline review, and close dispositions. Phase close requires all leases released and every workspace idle, removed, retained, or external.
