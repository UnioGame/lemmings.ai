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
