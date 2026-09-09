# Lemmings

![Lemmings package icon](assets/icon.png)

Lemmings is a small repository delivery pipeline. The current agent is the manager; it uses bounded workers, reviewers and explorers only when the task needs them. Install it and ask for work. Provider discovery, role presets, engine SDKs and telemetry are optional.

## Quick start

Requires Git and Python 3.10+ for the core CLI. Provider TOML discovery needs Python 3.11+ (`tomllib`); on an older Python the scan reports that limitation without blocking ordinary work. No Unity or external agent CLI is required to install.

From this package:

```text
python skills/lemmings/scripts/install.py --repo <your-repository>
```

Windows and Bash launchers are also provided:

```text
./scripts/install.ps1 -Repo <your-repository>
./scripts/install.sh --repo <your-repository>
```

Then ask the agent: **“Use Lemmings to implement this change and verify the result.”** A small change uses the current agent and current host defaults. The installed command is:

```text
python .agents/skills/lemmings/scripts/run.py doctor
```

Examples below use `lemmings` as shorthand for `python .agents/skills/lemmings/scripts/run.py`. No separate CLI installation is necessary. Add `--repo PATH` when outside the target repository.

The installer copies the self-contained skill and three roles. It preserves existing v4 settings and manual model/effort overrides, fills missing defaults, and rolls back owned files on failure. Existing active runtime, invocation, process or workspace ownership blocks replacement. It never installs engine SDKs or changes provider credentials. Schema versions before 4 require an explicit replacement; they are not silently migrated. Plugin hooks are configured separately.

## Proportional delivery

| Mode | Use case | Required structure |
| --- | --- | --- |
| Simple | One low-risk ownership domain | Direct implementation and focused validation |
| Standard | One bounded writer, medium risk or independent review | Task, candidate and required evidence |
| Strict | Parallel writers, shared contracts/assets, submodules, integration branch or high risk | Phase, dependency-ready isolated wave, immutable review and integration validation |

Auto resolves the mode after discovery and can escalate. Explicit mode pins remain explicit. The manager chooses; tooling validates or atomically executes the recorded decision. Two workers is the default concurrency, bounded by host slots including the manager. Integrate only after the entire independent wave completes.

```mermaid
flowchart LR
  A[Request] --> B[Manager discovers scope]
  B --> C{Auto mode}
  C -->|Simple| D[Direct change]
  C -->|Standard / Strict| E[Task and isolated writers]
  D --> F[Focused validation]
  E --> F
  F --> G[Required immutable review]
  G --> H[Integration checks at exact commit]
```

Task lifecycle: `Draft → Ready → Active → Candidate → Accepted → Integrated`. Failed work remains available for repair or inspection. Hook success alone never accepts a candidate.

## Use case: discover existing subscriptions

Ask the manager: **“Scan my configured providers, save the inventory and propose up to three worker/reviewer profiles. Keep my manual profiles first.”**

```text
lemmings models scan
lemmings models scan --offline
lemmings models scan --host-catalog host-catalog.json --output inventory.json
lemmings models inspect --inventory --provider <provider-id> --limit 20
lemmings profiles list
```

Default output is a compact provider/count summary; `models inspect --inventory` returns a bounded provider slice. `models scan --details` explicitly emits the full sanitized snapshot. The scan reads standard Codex/OpenCode configuration and documented catalogs; it sends no inference requests. Generated state is stored in `~/.lemmings/state.json`, with no credential values. Offline/partial scans preserve earlier catalogue evidence as stale and report limitations. When configured or authenticated providers are known, the shared OpenCode cache is restricted to those provider identities; models from unrelated cached vendors are not treated as connected subscriptions. Provider IDs must match exactly: `opencode_go` and `opencode-go` are different config keys. A mismatch produces a diagnostic, not an automatic personal-config rewrite.

| Evidence | Meaning |
| --- | --- |
| configured | Present in local provider/profile settings |
| catalogued | Listed by a catalogue source |
| compatible | Protocol/executor combination is supported |
| authConfigured | Authentication appears configured; access is not proven |
| probed | An explicit targeted access/inference probe succeeded |

A public catalogue does not establish subscription entitlement, current quota, latency or model quality. Models under the same quota group may share one subscription limit. To explicitly test one selected route:

```text
lemmings models probe --route route.json
```

Use a sanitized route object from the scan. This command can consume provider usage; ordinary scan, installation and proposals never call it.

[OpenCode Go](https://opencode.ai/docs/go/) contains models with different protocols. A list of Codex Responses profiles is not the complete Go catalogue. Responses-compatible routes can use Codex CLI; Chat Completions or Messages routes need a compatible OpenCode provider. Missing models can therefore mean an alias/configuration mismatch, an incomplete local catalogue, or executor incompatibility. Inspect these separately before editing configuration. Codex discovery includes visible entries and reasoning variants from `models_cache.json`, named `[profiles.*]`, and standalone `*.config.toml` files. Profiles using the same model retain separate identities. Hidden cache entries and embedded model instructions are excluded. A public Go model ID gets a known protocol only from verified service metadata; newly added IDs remain unknown until their protocol is established.

## Use case: switch worker/reviewer profiles

Ask the manager: **“From the discovered routes, prepare economy, balanced and review profiles. Show known tradeoffs and unknowns; save the one I select.”** The manager creates up to three options; there is no hidden CLI model-ranking service.

The manager writes `routes.json` with worker/reviewer/explorer ordered route arrays using actual scanned IDs. It can combine different providers and existing Codex profile names. Each route records hostId, providerId, modelId, executor, protocol and optional variantId/profileName/quotaGroup. Copy evidence from the scan; do not invent model IDs or protocol support.

```text
lemmings models propose --name balanced --routes routes.json --output proposal.json
lemmings models apply --proposal proposal.json --confirm <proposalDigest>
lemmings profiles inspect balanced
lemmings profiles use balanced
```

Saving does not activate a profile. Selecting affects future Tasks and authorizes only its listed ordered fallback chains. It never authorizes arbitrary model substitution or a second simultaneous writer after a failed attempt.

Precedence, highest first:

1. Explicit task model pin.
2. Active project manual role assignments in `.agents/lemmings.json`.
3. Active personal manual assignments in `~/.lemmings/profiles.json`.
4. Explicitly selected generated preset.
5. Current host defaults.

Manual named files use a `profiles` map, with each name containing `roleRoutes` for worker/reviewer/explorer; an optional `activeProfile` selects the manual default. Existing project `modelRoutes` remains supported and retains priority. `profiles inspect` explains the effective sources so a manual pin that masks a generated choice is visible. Values installed by an older v4 release are also preserved: the installer cannot reliably distinguish them from your edits. To let a generated preset control a role, explicitly move or remove that role’s old manual assignment after inspecting its source.

For example, merge this named profile into your personal file after replacing the example IDs with routes reported by your scan. `profileName` refers to an existing Codex profile; the Lemmings preset name is separate.

```json
{
  "schemaVersion": 4,
  "activeProfile": "mixed",
  "profiles": {
    "mixed": {
      "roleRoutes": {
        "worker": [{"hostId": "opencode", "providerId": "YOUR_PROVIDER", "modelId": "YOUR_WORKER_MODEL", "executor": "opencode", "protocol": "chat-completions"}],
        "reviewer": [{"hostId": "codex", "providerId": "YOUR_PROVIDER", "modelId": "YOUR_REVIEW_MODEL", "executor": "codex", "protocol": "responses", "profileName": "YOUR_EXISTING_PROFILE"}],
        "explorer": []
      }
    }
  }
}
```

A new Task can select a preset when its first invocation is persisted:

```text
lemmings invocation create --task docs/tasks/change.task.json --role worker --attempt 1 --expected-revision 0 --preset balanced
```

The manager prepares a valid bounded Task first. `--profile settings.json` still means a configuration file path; `--preset balanced` means a profile name. Effective profile/rules are frozen per Task. Changing the active preset never reroutes an already running task.

Legacy explicit-catalog routing is retained:

```text
lemmings models inspect
lemmings models propose --catalog catalog.json --routes routes.json
lemmings models apply --catalog catalog.json --routes routes.json --confirm <proposalDigest>
```

## Use case: run an existing provider as a worker

Native host dispatch is the default. The manager can execute a saved invocation through an installed Codex or OpenCode CLI when a selected route declares that executor:

```text
lemmings run --task docs/tasks/change.task.json --invocation-id <saved-id> --route route.json --dry-run
lemmings run --task docs/tasks/change.task.json --invocation-id <saved-id> --route route.json --output result.json
lemmings invocation accept --task docs/tasks/change.task.json --result result.json --expected-revision <revision>
```

A standalone CLI cannot create a native host agent by itself; a native dispatch request is handed back to the manager/host bridge. External adapters start fresh sessions and enforce role tool restrictions. Unsupported restrictions/protocols fail visibly. Process termination must be confirmed before a replacement writer starts. Launching does not accept a candidate. Offline adapter tests use fake executables and do not prove live subscription access. Codex uses its filesystem sandbox; OpenCode provides tool permissions and permits only declared validation/Git commands for workers. CLI deadlines are enforced; tool-call counts remain instruction budgets when the CLI exposes no hard limiter. Native bridges must declare fresh-session, role, cancellation and no-delegation guarantees. Selected Codex profile connection/effort is carried into an isolated invocation without importing its unrelated tool grants.

On capacity failure, the manager retries one short transient error or reduces context once where appropriate. Otherwise it proposes task-local recovery choices. Existing `models recover propose|apply|advance` keeps the selected ordered chain in the Task. No history transfers between models. See [routing and recovery](skills/lemmings/references/model-routing.md).

Cross review uses distinct provider/model identities, not two variants of one model. If unavailable, the manager records `reviewPolicy: single` and `cross-review-unavailable`; this does not block delivery.

## Use case: engine rules load automatically

Core owns orchestration, permissions, ownership, context budgets and evidence. Optional [rule packs](skills/lemmings/rules/manifest.json) own technology-specific practices. Detection follows task paths to the nearest project root, including monorepos. It ignores generated/dependency/cache trees and transitive lockfile dependencies. No SDK is installed by detection.

```text
lemmings rules explain --path GameClient/Assets/UI
lemmings rules explain --path games/browser/src --platform web
lemmings rules explain --path tools/client --technology flutter --platform android
```

The manager stores the selection in Task `ruleSelection` (`paths`, `technologies`, `platforms`) before the first invocation. Explicit selections override detection. Selected refs and content hashes join the existing dispatch budget; unselected packs are not loaded. Platform means the task's build target, not the agent's OS.

| Technology | Example request | Pack focus |
| --- | --- | --- |
| [Unity](skills/lemmings/rules/unity.md) | Fix one UI prefab and its binding | GUID/meta identity, narrow serialized changes, asmdefs, lifecycle, targeted Editor checks, independent Library |
| [Unreal](skills/lemmings/rules/unreal.md) | Change a component used by Blueprint | Reflection/module contracts, binary ownership, OFPA authored assets, UBT/Blueprint checks |
| [Godot](skills/lemmings/rules/godot.md) | Repair a signal or scene reference | Version/C# distinction, UID/import sidecars, NodePath, headless vs visual evidence |
| [Defold](skills/lemmings/rules/defold.md) | Fix collection loading or GUI input | Lua lifecycle, resource URLs, atlases, Bob version, native extensions |
| [Flutter](skills/lemmings/rules/flutter.md) | Fix a widget/state cleanup bug | Actual Flutter SDK detection, existing state architecture, dispose/keys/semantics, focused widget checks |
| [Phaser](skills/lemmings/rules/phaser.md) | Fix a scene that leaks after restart | Direct version evidence, shutdown listeners/timers, shared textures, input/resize/loading checks |
| [PixiJS](skills/lemmings/rules/pixijs.md) | Fix renderer mounting and resize | Version-specific init/destroy, ticker/GPU/shared textures, DPR, browser checks |

[Platform rules](skills/lemmings/rules/platforms.md) add only relevant web/mobile/desktop/publishing constraints. Flutter is not automatically Flame; PixiJS is not automatically a scene/physics engine. Compilation/headless checks do not establish visual correctness. Preserve project conventions and validate the changed risk only.

## Use case: isolated parallel work and submodules

Ask the manager: **“Split independent changes into two isolated workers. Preserve the dirty primary checkout and review their exact commit ranges.”**

The manager records an exact repository/backend/base SHA/branch/destination/estimate plan. `workspace prepare` executes that plan; `workspace inspect` explains registry state. Use `codex/` branches by default. A package worktree uses the package's own Git root, not a whole superproject with a package-only size estimate.

```text
lemmings workspace estimate --backend package-worktree --package <package-git-root>
lemmings workspace inspect
lemmings workspace prepare --repo <package-git-root> --task docs/tasks/change.task.json --destination <worktree-path> --branch codex/change --expected-revision <registry-revision>
```

For package provisioning run in the package's own Git root, with `workspace.packagePath: "."`. The recorded Task workspace includes `workspaceId`, `backend`, `managedBy: "lemmings"`, `lifetime`, `repoRoot`, `destination`, `branch`, `estimatedGiB`, `approval` and a reason; Task `baseSha` is an exact commit. Resolve the registry revision with inspect. These values are checked against the actual repository and creation request.

```text
lemmings workspace release --workspace-id <id> --task docs/tasks/change.task.json --task-revision <task-revision> --expected-revision <registry-revision> --action pool
lemmings workspace remove --workspace-id <id> --task docs/tasks/change.task.json --task-revision <task-revision> --expected-revision <registry-revision>
```

Never use force/reset/clean/prune as routine lifecycle actions. A primary, dirty, user-owned, validation, unknown, active or unintegrated worktree cannot be removed automatically. Cleanup requires the canonical Task, matching revision, actual integrated commits and passing declared checks at that commit. A caller boolean is not integration evidence. Failed/cancelled work stays available for diagnosis. Quarantine records uncertainty without deleting files.

The pool retains at most two idle worktrees and 10 GiB per Git common directory. Provisioning above 10 GiB requires recorded authorization. Editors, import caches, ports and browser state belong to independent worker directories. A persistent validation workspace can retain expensive caches; it is not a spare writer checkout. See [workspace lifecycle](skills/lemmings/references/game-projects.md).

## Token economy and checks

Dispatch stays under 16 KiB and 12 hashed references. The manager supplies one focused context expansion only when a named decision remains unresolved. Default tool-call limits remain worker 24, reviewer 16, explorer 12. Read exact symbols/assets and targeted diagnostics; do not load complete scene YAML, binaries, generated bundles, dependency caches or full logs into model context.

Validation returns bounded diagnostics, an omitted-byte count, full-log artifact reference and actual exit status. Truncation never hides failure. Do not repeat passing checks without new changes or unresolved risk. [Context rules](skills/lemmings/references/context-contract.md) and selected packs provide details.

```text
lemmings check --task docs/tasks/change.task.json
lemmings check --all --phase docs/tasks/change.phase.json
lemmings check --distribution
lemmings integration validate --task docs/tasks/change.task.json --expected-revision <revision>
```

Integration validation requires HEAD equal to Task `close.mergeCommit` and a clean source tree before and after checks; only the canonical Task and declared validation outputs may differ. Plain `check` validates contracts; `--distribution` additionally compares installed instructions while allowing explicit model/effort overrides. Telemetry remains optional and offline (`lemmings metrics status`); scans and capacity handling do not depend on it.

Package development checks: `python -B -m unittest discover -s tests`, skill validation, Markdown links, package JSON and `git diff --check`. See [package rules](AGENTS.md) and [roadmap](Documentation~/tasks/ROADMAP.md).
