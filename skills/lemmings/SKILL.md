---
name: lemmings
description: Coordinate repository delivery with proportional Auto, Simple, Standard, or Strict orchestration. Use when a user invokes $lemmings, delegates repository implementation, needs isolated writers, commit-range review, shared-contract safety, model routing, worktree pooling, integration evidence, or optional pipeline metrics.
---

# Lemmings

Act as the sole manager. Tooling validates or atomically executes an already recorded decision; it never chooses mode, model, task, batch, verdict, or acceptance.

Start with current-host defaults; installation needs only Git and Python. No provider scan, external CLI, engine SDK, preset, or telemetry is required for ordinary work. Shipped roles contain no model pins. Keep existing manual assignments.

Use schema v4 only. If any v2 Task, Phase, Review, profile, or runtime marker is supplied, stop with `schemaVersion 2 is unsupported by the schema-v4 runtime; replace the legacy bundle`. Do not migrate it or fall back to `.codex/lemmings.json`.

Run `Discover → Plan → Refine → Implement → Verify`. Default `requestedMode` to `auto` and resolve it after Discover from the affected task scope. A repository containing submodules or integration branches is not itself a Strict signal; record those reasons only when this task changes a submodule boundary/pointer or performs branch integration:

1. Strict for two writers, shared/frozen contracts, overlapping domains, submodules, codegen, multi-repository integration, shared serialized assets, exclusive resources, high risk, an integration branch, or baseline review.
2. Otherwise Standard for one bounded worker, a public contract with one owner, medium risk, candidate/repair/review, or validation wider than one focused check.
3. Otherwise Simple for one low-risk ownership domain that the manager can change directly.

Do not silently change an explicit mode pin. Auto may escalate after new discovery, but never downgrade after the first mutation. Host capability gaps change topology, not guarantees: serialize writers without isolation/slots; ignore late results without cancellation; use count/time limits without token accounting; ask the user when a required reviewer is unavailable.

When asked to discover subscriptions, run `models scan` (`--offline` without network), then read [model-routing.md](references/model-routing.md). Offer at most three evidence-based economy/balanced/review presets; state unknown quality, cost, quota and access rather than invent rankings. Saving a proposal does not activate it; manual pins always win. Scan/probe never edit provider configuration.

On a model capacity failure, stop new dispatch and read [model-routing.md](references/model-routing.md). Retry one short rate/transport failure or reduce context once when applicable; otherwise present two to four task-local role plans. Apply nothing before user confirmation. One confirmation permits only the selected ordered route chains for the current Task; keep the workspace, start a fresh invocation without model history, and request new confirmation when the chain is exhausted. Capacity probes and recovery never depend on telemetry.

Use only `manager`, `worker`, `reviewer`, and `explorer`. Delegation depth is one. Reserve the manager slot; run up to four isolated writers, bounded by `maxConcurrentWriters` and two read-only agents. Select each writer wave only from dependency-ready tasks, require explicit independence, and wait for every writer in the wave before accepting or integrating any result. Use the frozen Task budget: start with the profile grants, extend only for a named unresolved question with recorded progress, never exceed the frozen ceilings, and allow one transient transport retry.

Model routes may declare optional `specializations` tags and Tasks may declare one optional `specialization`. The tag is a manager hint: matching routes get priority, but untagged routes remain valid fallbacks. The assigned route in `models.assigned` is the only execution authority; tools never select or rank models. For high-risk work, the manager may set `reviewPolicy` to `cross`; use two distinct provider/model identities when available, otherwise change the policy to `single` and record `cross-review-unavailable` in `capabilityDegradations`.

Accept when the declared acceptance criteria and required validation pass and no blocking defect remains. P0-P2 findings describe concrete acceptance failures or substantial defects in affected behavior; P3 suggestions never trigger repair. Stop implementation and review at this threshold, complete required integration, and report optional follow-ups. Do not expand scope or add checks for polish. See [contracts.md](references/contracts.md) for the review decision rules.

Before the first writer for migrations, shared contracts, or non-trivial dependencies, dispatch the existing reviewer with `Review.subject.kind = plan`; bind it to the Task/Phase plan digest and resolve material gaps. Simple skips this review. The manager alone updates Task/Phase using `revision` compare-and-set. Accept `AgentResult v4` only when invocation id, attempt, Task revision, base SHA, context digest, and profile digest still match. Never transfer model conversation history across tasks or pooled workspaces. Freeze effective profile/rule selection with the first reviewer invocation before recording the accepted plan digest; a later plan-relevant selection change needs a new plan review.

Resolve optional rules from task paths with `rules explain`; use only the selected [technology packs](rules/manifest.json). Record task `ruleSelection` and freeze effective routing/rule hashes on first invocation. Load only selected packs and role-relevant sections, not every engine. Explicit project rules refine the optional defaults; mandatory core isolation, validation, ownership and permissions remain in force. Shared token rules are in [context-contract.md](references/context-contract.md).

Start dispatch at 16 KiB and 12 context references; the frozen ceilings are 32 KiB and 24 references. Send references plus hashes and role-unique rules, never Task/Phase copies, transcripts, reasoning, raw logs, telemetry, registry contents, secrets, or absolute paths. Summarize logs deterministically. Read [context-contract.md](references/context-contract.md) before dispatch.

For Standard/Strict, use the v4 templates and explicitly activate runtime for the Task; Simple has no marker. `Draft → Ready → Active → Candidate → Accepted → Integrated`; use `Repair`, `Replan Required`, `Blocked`, or `Cancelled` when applicable. Manager or worker may run an exact declared validation command. Use candidate preparation as the final declared validation pass; worker diagnostics should be focused on implementation needs. Reviewers reuse passing evidence unless a specific defect makes it insufficient. Before Candidate review reservation, run `candidate prepare` once for the immutable candidate: compare `AgentResult.changedPaths` with the real `base..head` diff, check ownership and clean-tree boundaries, execute declared checks, and persist compact evidence with plan/validation digests. A failed command cannot be replaced by debt; debt is only for executor-classified unavailability and binds the exact command and head. Readiness is reused only while all inputs are unchanged. Candidate reviewer dispatch is blocked until this gate passes. A handoff is only an optional dependency note. The first candidate review inspects the full change. Repeat review checks prior blockers, the new delta, and directly affected behavior; its full base-to-head binding identifies the candidate, not a request to reread unchanged code. Refresh readiness for a new head; this alone does not force full review. Changed base, plan/validation requirements, scope, or an invalid prior review basis require a full review. Start repairs atomically from immutable review or failed readiness; one open worker dispatch is allowed per cycle, and no progress moves to `Replan Required`. Run `integration validate` on the exact `close.mergeCommit`; only passing evidence for that SHA permits `Integrated`. Hook success never accepts a result: persist dispatch with `invocation create` and accept it with `invocation accept`.

Read only the reference needed for the current decision:

- [contracts.md](references/contracts.md): artifacts, Auto signals, lifecycle, CAS, and batch checks.
- [context-contract.md](references/context-contract.md): AgentInvocation/AgentResult and context limits.
- [game-projects.md](references/game-projects.md): workspace registry, pool, reuse, leases, and safe cleanup.
- [model-routing.md](references/model-routing.md): host capabilities and confirmation-gated routes.
- [telemetry.md](references/telemetry.md): optional offline usage and benchmark collection.
- [skill-reuse.md](references/skill-reuse.md): optional installed/official skill check and user-gated creation flow.

## Skill reuse proposals

When repeated work suggests a reusable skill, follow [skill-reuse.md](references/skill-reuse.md). Check local and installed skills first, then official vendor sources. Give the user a short recommended choice and use the existing `skill-creator` only after the user selects creation or modification. Continue the main Task when the check is unavailable.

Run the narrowest falsifying validation, then `python .agents/skills/lemmings/scripts/run.py check --repo <repo>`; add `--all` for a complete Strict Phase and `--distribution` only when checking installed bundle bytes. Keep reusable policy here/references, canonical data only in Task/Phase/Review, and compact evidence in the Task.

Controls: `lemmings doctor`; `lemmings candidate prepare`; `lemmings invocation create|extend|context-use|fail|accept`; `lemmings repair start`; `lemmings review apply`; `lemmings integration validate`; `lemmings runtime activate|status|deactivate`; `lemmings models scan|probe|inspect|propose|apply|recover`; `lemmings profiles list|inspect|use`; `lemmings rules explain`; `lemmings run`; `lemmings workspace estimate|prepare|inspect|register|claim|release|remove`; optional `lemmings metrics ...`. New invocations use host-v1 usage receipts; model-authored usage is never trusted. Only a manager-directed v4 runtime marker enables hooks. There are no validator, summarizer, or orchestrator invocation roles.
