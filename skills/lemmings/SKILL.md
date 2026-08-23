---
name: lemmings
description: Coordinate repository delivery with proportional Auto, Simple, Standard, or Strict orchestration. Use when a user invokes $lemmings, delegates repository implementation, needs isolated writers, commit-range review, shared-contract safety, model routing, worktree pooling, integration evidence, or optional pipeline metrics.
---

# Lemmings

Act as the sole manager. Tooling validates or atomically executes an already recorded decision; it never chooses mode, model, task, batch, verdict, or acceptance.

Use schema v3 only. If any v2 Task, Phase, Review, profile, or runtime marker is supplied, stop with `schemaVersion 2 is unsupported by Lemmings 3.2; replace the legacy bundle`. Do not migrate it or fall back to `.codex/lemmings.json`.

Run `Discover → Plan → Refine → Implement → Verify`. Default `requestedMode` to `auto` and resolve it after Discover:

1. Strict for two writers, shared/frozen contracts, overlapping domains, submodules, codegen, multi-repository integration, shared serialized assets, exclusive resources, high risk, an integration branch, or baseline review.
2. Otherwise Standard for one bounded worker, a public contract with one owner, medium risk, candidate/repair/review, or validation wider than one focused check.
3. Otherwise Simple for one low-risk ownership domain that the manager can change directly.

Do not silently change an explicit mode pin. Auto may escalate after new discovery, but never downgrade after the first mutation. Host capability gaps change topology, not guarantees: serialize writers without isolation/slots; ignore late results without cancellation; use count/time limits without token accounting; ask the user when a required reviewer is unavailable.

On a model capacity failure, stop new dispatch and read [model-routing.md](references/model-routing.md). Retry one short rate/transport failure or reduce context once when applicable; otherwise present two to four task-local role plans. Apply nothing before user confirmation. One confirmation permits only the selected ordered route chains for the current Task; keep the workspace, start a fresh invocation without model history, and request new confirmation when the chain is exhausted. Capacity probes and recovery never depend on telemetry.

Use only `manager`, `worker`, `reviewer`, and `explorer`. Delegation depth is one. Reserve the manager slot; run at most two isolated writers and two read-only agents. Select each writer wave only from dependency-ready tasks, require explicit independence, and wait for the whole wave before integration. Allow one focused context expansion, one repair, and one transient transport retry.

Model routes may declare optional `specializations` tags and Tasks may declare one optional `specialization`. The tag is a manager hint: matching routes get priority, but untagged routes remain valid fallbacks. The assigned route in `models.assigned` is the only execution authority; tools never select or rank models. For high-risk work, the manager may set `reviewPolicy` to `cross`; use two distinct provider/model identities when available, otherwise change the policy to `single` and record `cross-review-unavailable` in `capabilityDegradations`.

The manager alone updates Task/Phase using `revision` compare-and-set. Accept `AgentResult v3` only when invocation id, attempt, Task revision, base SHA, context digest, and profile digest still match. Never transfer model conversation history across tasks or pooled workspaces.

Keep dispatch under 16 KiB and 12 context references. Send references plus hashes and role-unique rules, never Task/Phase copies, transcripts, reasoning, raw logs, telemetry, registry contents, secrets, or absolute paths. Summarize logs deterministically. Read [context-contract.md](references/context-contract.md) before dispatch.

For Standard/Strict, use the v3 templates and explicitly activate runtime for the Task; Simple has no marker. `Draft → Ready → Active → Candidate → Accepted → Integrated`; use `Repair`, `Replan Required`, `Blocked`, or `Cancelled` when applicable. Manager or worker may run an exact declared validation command. Before Candidate/Accepted, compare `AgentResult.changedPaths` with the real `base..head` diff and check that diff against ownership once. A handoff is only an optional dependency note. Review only when mode/risk requires it, and review the immutable candidate range. `Integrated` requires integration evidence, not telemetry or cleanup success.

Read only the reference needed for the current decision:

- [contracts.md](references/contracts.md): artifacts, Auto signals, lifecycle, CAS, and batch checks.
- [context-contract.md](references/context-contract.md): AgentInvocation/AgentResult and context limits.
- [game-projects.md](references/game-projects.md): workspace registry, pool, reuse, leases, and safe cleanup.
- [model-routing.md](references/model-routing.md): host capabilities and confirmation-gated routes.
- [telemetry.md](references/telemetry.md): optional offline usage and benchmark collection.

Run the narrowest falsifying validation, then `python -m lemmings check`; add `--all` for a complete Strict Phase and `--distribution` only when checking installed bundle bytes. Keep reusable policy here/references, canonical data only in Task/Phase/Review, and compact evidence in the Task.

Controls: `lemmings runtime activate|status|deactivate`; `lemmings models inspect|propose|apply|recover`; `lemmings workspace estimate|inspect|register|claim|release|remove`; optional `lemmings metrics ...`. Only a manager-directed v3 runtime marker enables hooks. There are no validator, summarizer, or orchestrator invocation roles.
