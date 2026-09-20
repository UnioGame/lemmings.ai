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

For Standard/Strict, use the v4 templates and explicitly activate runtime; Simple has no marker. State flow is `Draft → Ready → Active → Candidate → Accepted → Integrated`. For candidate review, prefer two operations:

1. `review start --task <task> --head <candidate>` prepares declared checks and saves the reviewer invocation. Use the returned invocation; a repeated call resumes the same reservation. Do not dispatch a second agent when `reused` is true.
2. `review submit --task <task> --result <report> --review <artifact> --host-receipt <receipt>` records the result, builds immutable Review bindings, and applies the verdict. The report is AgentResult v4 plus `verdict`, actual `hostId`/`reviewerModel`, and `findingDispositions` for repairs. Use an existing allowed or ignored artifact directory. Tools own revisions, digests, review subjects, and cycle numbers; never copy or edit them by hand. See [contracts.md](references/contracts.md) for recovery and low-level operations.

Results may omit transport fields: ingestion derives schema/attempt from the explicitly named invocation, worker changed paths from Git, and empty optional lists. `invocation accept --task <task> --result <report> --invocation-id <id>` records worker evidence without a hand-entered revision. Preserve explicit values; never invent acceptance/validation evidence or select an invocation by recency. Candidate review accepts `id` as a finding-ID alias. See [context-contract.md](references/context-contract.md) for compact reports.

For the ordinary Standard/Strict path, create the Task with `task prepare --input <brief> --task <task>`. TaskBrief v1 must contain the goal, acceptance, ownership, risks and exact risk-to-test mapping, compact working set, validation, plus the manager's explicit mode, review, workspace, role-route, and accounting decisions. The tool fills Git and schema metadata; it never chooses those decisions or overwrites an existing Task. Accept a successful worker with `candidate submit --task <task> --invocation-id <id> --result <report>`; it derives the exact HEAD, records evidence, promotes Candidate, and runs readiness recoverably. A failed result is recorded without promotion.

Freeze `host-v1` when the host supplies trusted receipts. Freeze `invocation-v1` before the first invocation when it does not: count invocation creation across retry, repair, model recovery, and replan, using task limits worker 5, reviewer 7, explorer 5 unless configured otherwise. Replay never consumes another slot. Never represent unknown tool calls as measured usage or switch/reset a frozen mode.

An `artifact` error means correct report formatting and resubmit the same invocation, without rereading code or launching review again. An `evidence` error names an input that changed: refresh only the invalid evidence. A `conflict` means inspect the saved result, never overwrite it. Code repair requires a concrete blocker or failed validation; use `repair start`. Candidate preparation is the final declared validation pass; worker diagnostics remain focused. Repeat review checks prior blockers, the delta, and directly affected behavior. Changed readiness alone does not require full review. Complete integration with `integration validate` on the exact `close.mergeCommit`; only passing evidence for that SHA permits `Integrated`.

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

Controls: `lemmings doctor`; `lemmings task prepare`; `lemmings candidate submit|prepare`; `lemmings review start|submit|apply`; `lemmings invocation create|extend|context-use|fail|accept`; `lemmings repair start`; `lemmings integration validate`; `lemmings runtime activate|status|deactivate`; `lemmings models scan|probe|inspect|propose|apply|recover`; `lemmings profiles list|inspect|use`; `lemmings rules explain`; `lemmings run`; `lemmings workspace estimate|prepare|inspect|register|claim|release|remove`; optional `lemmings metrics ...`. Model-authored usage is never trusted. Only a manager-directed v4 runtime marker enables hooks. There are no validator, summarizer, or orchestrator invocation roles.
