# Host and model routing

The host adapter reports isolation, parallel agents, cancellation, structured output, usage accounting, capacity probing, model catalog, tool-call limits, and approvals. Capabilities affect execution shape, never the resolved safety guarantees. A missing `capacityProbe` means reactive recovery after a host error; it does not block dispatch.

## Project routes

Canonical project configuration is `.agents/lemmings.json`. `modelRoutes` is a per-host ordered map for worker, reviewer, and explorer. Each route uses opaque `providerId`, `modelId`, optional `variantId`, and optional `specializations` string tags. A Task `specialization` is a manager hint: matching tags receive priority while every route for the role remains an allowed fallback. The manager is the current agent and is not reconfigured by this file.

`lemmings models propose --catalog <catalog.json> --routes <routes.json>` validates one host catalog read-only and returns before/after plus config, catalog, and proposal digests. Show the diff to the user. Write nothing until explicit confirmation. `models apply` with the same inputs and `--confirm <proposalDigest>` rejects stale catalog or config state and atomically changes only project routes. It never changes workspace, prompts, topology, concurrency, telemetry, or Task state.

## Capacity failures

Normalize host failures to a compact `RouteFailure`: category, invocation id, `RouteRef`, resumable flag, and optional retry/reset time. Categories are `quota_exhausted`, `rate_limited`, `model_unavailable`, `auth_or_billing`, `context_limit`, and `transient_transport`.

- Retry one rate limit of at most 30 seconds or one transient transport failure.
- Reduce context once for `context_limit`; do not switch providers blindly.
- For other capacity failures, stop new dispatch and propose two to four choices: same model from another source, minimum role replacement, remaining-role remap, or wait when reset time is known.

The manager authors and explains the choices. Tooling validates catalogs and digests but never ranks or selects them.

## Task-local recovery

`models recover propose` accepts the current Task, RouteFailure, manager-authored options, and one or more current host catalogs. Each route option contains ordered worker, reviewer, and explorer `RouteRef` chains plus quality/cost/speed impact and known limitations. `RouteRef` is `{hostId, providerId, modelId, variantId?}`.

After the user selects one option, `models recover apply --option <id> --confirm <proposalDigest>` atomically records only the selected plan in `Task.routingRecovery`, increments Task revision, and assigns its first route. It never changes `.agents/lemmings.json`. Config, Task, catalog, or proposal drift makes confirmation stale.

One confirmation covers the selected chains until the Task ends. `models recover advance` may only move to the next already approved route. It records at most 12 compact route/result attempts. Exhaustion pauses dispatch and requires a new proposal. A wait option also pauses dispatch. Never poll capacity in the background or return to the original route midway through the Task.

A replacement worker receives a fresh invocation in the same workspace with a deterministic checkpoint: HEAD, Git status, changed paths, and existing evidence. Do not transfer conversation history. A replacement reviewer receives the same immutable candidate range; never waive required review or replace it with manager self-review.

For `reviewPolicy: "cross"`, the manager records the primary review in `reviewRef` and additional reports in `crossReviewRefs`. Distinct `providerId/modelId` identities are required; variants of one model do not count. If a second identity is unavailable, change the policy to `single` and record `cross-review-unavailable` in `capabilityDegradations`; this degradation alone never blocks delivery.

After completion, offer permanent `models propose/apply` only as a separate user-confirmed operation. A large catalog alone never raises Auto mode, and no unconfirmed route overrides a pin.

## Discovery and reusable presets

`models scan` reads standard Codex and OpenCode configuration, profile files and documented catalogs. `--offline` uses local evidence. Generated inventory is in `~/.lemmings/state.json`; credentials never belong there. Provider IDs are exact: a profile naming `opencode-go` cannot reference a provider named `opencode_go`. Report mismatches; do not rewrite personal configuration. Missing or stale catalog evidence is distinct from authentication failure.

A route separately reports configured, catalogued, compatible, authConfigured and probed. A public catalog is not proof of subscription access or remaining quota. `models probe --route route.json` is an explicit targeted inference request. No probe runs during installation, ordinary discovery, profile inspection or proposal. Models sharing a `quotaGroup` may share one subscription limit; different names are not independent capacity.

The manager inspects the snapshot and proposes up to three named options with ordered worker/reviewer/explorer routes and concrete tradeoffs. Tooling never calls a model to rank models. Use existing validated profiles and observed task results as evidence; leave unknown quality/cost as unknown. Native dispatch is always the zero-config fallback when no manual route is selected, not a fallback that silently overrides a failed user pin.

Manual project `modelRoutes` and named `profiles` live in `.agents/lemmings.json`. Optional personal profiles live in `~/.lemmings/profiles.json`. Generated proposals/selections live in `~/.lemmings/state.json`. Precedence: task explicit pin, project manual, personal manual, explicitly selected generated profile, current host. `models propose --name NAME --routes routes.json` returns a digest; `models apply --proposal proposal.json --confirm DIGEST` saves it; `profiles use NAME` selects it for future Tasks. Neither scan nor save changes manual settings. Inspect explains the effective per-role sources. Selection authorizes only the listed ordered chains, never arbitrary substitutions.

Task effectiveConfig freezes profile name, role chains, sources and rule digests. Existing --profile remains a JSON path; --preset selects a name. A profile change never changes an active Task. Recovery still requires fresh invocation and recorded approved route; first terminate the prior writer. Exhausting a selected chain needs a new manager proposal.

## Execution adapters

The native adapter delegates a saved invocation through the current host's bridge. A CLI without a native bridge returns dispatch-required for the manager. Codex CLI uses Responses-compatible routes and existing Codex profile configuration. OpenCode CLI uses its own provider/model registry for supported Responses, Chat Completions and Messages routes. Go's catalog is not restricted to the four Responses models visible through a Codex-specific profile list; consult [Go documentation](https://opencode.ai/docs/go/) and each model's protocol. Do not relabel every Go model as Responses.

`run` executes one saved, manager-assigned invocation; it does not orchestrate, retry, change models or accept results. All adapters return AgentResult v4. A role with unsupported tool/write restrictions must fail visibly. External executions start fresh sessions, deny nested delegation, keep process/lock evidence and wait for confirmed termination before replacement. Read-only roles cannot receive unrestricted shell or mutation tools. A prompt is not an OS sandbox. CLI availability, provider access and actual model execution are separate evidence; offline fixture tests do not prove live subscription access.
