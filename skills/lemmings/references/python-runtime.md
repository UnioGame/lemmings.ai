# Optional Python runtime

Use this reference only after the manager selects the runtime path. The runtime requires Git and Python 3.10 or newer; provider TOML discovery requires Python 3.11 or newer. It adds atomic schema-v5 state, hooks, receipts, resumable transitions, and deterministic readiness checks. It does not make planning or acceptance decisions.

## Selection and activation

If runtime availability is unknown, run one bounded `lemmings doctor` or `python .agents/skills/lemmings/scripts/run.py doctor --repo <repo>` check. Do not create state during the probe. A pre-activation failure identifies the missing dependency. Ask once whether the user wants installation; install only after explicit approval. A declined or unanswered offer selects skill-only unless the user required runtime.

Use schema v5 only. Reject v2 Task, Phase, Review, profile, or marker data with `schemaVersion 2 is unsupported by the schema-v5 runtime; replace the legacy bundle`; do not migrate it silently. Standard and Strict activate the exact Task with `runtime activate`; Simple does not need a marker. Only an active manager-owned marker enables enforcement hooks.

Create the ordinary Task with:

```text
lemmings task prepare --input <brief> --task <task> --repo <workspace>
```

TaskBrief v1 contains the goal, acceptance, ownership, dependencies, risks and exact risk-to-test mapping, working set, validation, and explicit manager decisions for mode, review, workspace, role routes, and accounting. The tool fills Git/schema metadata and hashes; it never overwrites an existing Task.

The runtime lifecycle is `Draft → Ready → Active → Candidate → Accepted → Integrated`. The manager alone changes canonical Task/Phase state through compare-and-set operations. Task, Phase, and immutable Review remain the only canonical protocol artifacts.

The high-level interface is `flow start|advance|submit|replan|finish|status`. It returns `status`, `revision`, and deterministic `actions`; low-level commands remain available for diagnosis. `flow` never chooses scope, models, ownership, verdict, or acceptance.

New owners default to `invocation-v1`. `host-v1` is accepted only when TaskBrief/PhaseBrief freezes `hostCapabilities.<host>.usageAccounting=true` for every executing host; unsupported configurations fail before Task/Phase creation.

## Dispatch and results

Create each invocation against the target worker checkout so hashes describe its actual bytes. Freeze the effective profile, selected rules, routes, limits, base SHA, Task revision, and context hashes. Initial context is limited to 12 references and 16 KiB; recorded expansion may reach the frozen ceilings of 24 references and 32 KiB.

AgentInvocation v5 supplies one objective, acceptance, owned and forbidden paths, purposeful references, validation, remaining grant, dispatch kind, and retry/repair bindings. AgentResult v5 returns status, candidate head when applicable, changed paths, acceptance and validation evidence, findings, blockers, and remaining risks. Do not transfer conversation history between invocations.

Reports may omit schema version, attempt, empty optional lists, and worker changed paths; ingestion derives those from the explicitly selected invocation and Git. Never select an invocation by recency. Preserve explicit values and reject conflicts. A malformed envelope is corrected and resubmitted for the same invocation without rereading code. Missing evidence requires that evidence or check, not another implementation cycle.

Submit a successful worker through:

```text
lemmings candidate submit --task <task> --invocation-id <id> --result <report> --repo <candidate-workspace>
```

The command binds the saved result, derives the exact HEAD, validates ownership/evidence, promotes Candidate, and performs readiness in recoverable stages. A failed worker result is recorded without promotion. Repeating a completed stage is free and does not duplicate state.

## Review and integration

Start and submit candidate review with:

```text
lemmings review start --task <task> --head <candidate>
lemmings review submit --task <task> --result <report> --review <artifact> [--host-receipt <receipt>]
```

Use the saved reviewer invocation; a reused start must not dispatch another agent. The assigned reviewer route is the execution authority and must match both invocation and Review. The reviewer report supplies verdict, actual host/model, and finding dispositions for repairs. Tools own subject bindings, digests, cycle, and Task revisions.

First candidate review inspects the full base-to-head range. Delta review binds the previous review and head, all material dispositions, and a non-empty previous-to-current range; it checks prior blockers, the delta, and directly affected behavior. Base, plan, validation, or scope changes require full review. P3 never triggers repair.

Candidate readiness records the exact head, plan and validation digests, worker result, ownership, clean-tree checks, bounded diagnostics, and explicit executor-unavailable debt. A failed command is never masked by debt. Validate integration at the exact `close.mergeCommit`; only passing evidence for that SHA permits Integrated.

## Accounting and recovery

Freeze one accounting mode before the first invocation:

- `host-v1` uses trusted host receipts bound to invocation, grant, and source. Missing or mismatched receipts settle the full grant and lock the role.
- `invocation-v1` is for hosts without trusted statistics. It counts invocation creation across retry, repair, model recovery, and replan, with default Task limits worker 5, reviewer 7, explorer 5. Replay of the same record is free.

Never switch or reset a frozen mode, treat model-authored usage as measured, or change old frozen policies. Persist a route failure before recovery. One transient transport retry is allowed; otherwise follow the manager-confirmed task-local route chain.

Interpret failures locally:

- `artifact`: correct the report and resubmit the same invocation.
- `evidence`: refresh only the named stale or missing evidence.
- `conflict`: inspect and resume the saved result; never overwrite it.
- implementation defect: start repair for a concrete blocker or failed validation.

## Controlled handoff to skill-only execution

An explicit user request may move an active Task to the skill-only path. Stop new dispatch, wait for current agents, and preserve candidate identity, evidence, findings, assignments, attempts, and remaining blockers in the Task or one compact Markdown continuation note. Do not reset budgets or modify frozen policies.

Deactivate only a marker owned by this manager after confirming no invocation or workspace is active. Prefer `lemmings runtime deactivate`. If Python use itself is forbidden, locate the marker through `git rev-parse --path-format=absolute --git-common-dir`, verify its Task identity and idle state from existing records, then remove only `<git-common-dir>/lemmings/active.json`. If ownership or idleness cannot be proven, stop and report the blocker. Keep all schema-v5 artifacts as history and continue from the current five-stage position.

## Commands

The CLI also exposes `invocation create|extend|context-use|fail|accept`, `candidate prepare`, `repair start`, `review apply`, `integration validate`, workspace lifecycle commands, profiles, rules, model discovery/recovery, runner operations, and optional metrics. Read [contracts.md](contracts.md), [context-contract.md](context-contract.md), [game-projects.md](game-projects.md), or [model-routing.md](model-routing.md) only when that operation needs their detail.

Finish runtime-backed work with the narrowest relevant checks and:

```text
python .agents/skills/lemmings/scripts/run.py check --repo <repo>
```

Use `--all` for a complete Strict Phase and `--distribution` only when verifying installed bundle bytes.
