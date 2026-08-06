# Game-project workspaces

Use this reference after the smart skill has selected `isolated` work for a game project. Keep the main workflow engine-neutral; this page adds Unity-oriented workspace selection.

| Strategy | Choose when | Avoid when |
| --- | --- | --- |
| `code-worktree` | Runtime code, tools, or project settings must move together | The project clone itself is the material risk or cannot share generated state safely |
| `package-worktree` | A self-contained package is the owned change | The package change depends on project assets, settings, or generated project outputs |
| `unity-clone` | Serialized assets, import state, code generation, or editor execution need a fully separate project | A narrower worktree proves the same safety at lower cost |

Resolve in that order from the affected scope, then verify the selected workspace can run the required editor and validation flow. One writer owns one isolated workspace. Never share a Unity `Library/` directory between concurrently writing clones.

## Approval boundary

Running Lemmings or dispatching subagents in the current checkout needs no special workspace approval. A sequential implementation worker may write there when ownership and dirty-state safety allow it. Creating a `code-worktree` or `package-worktree` also needs no special approval because it does not duplicate the full imported project state.

Ask the user only before creating a full `unity-clone` whose estimated project, package, and generated/import state exceeds 10 GiB. A declined clone does not disable the pipeline or unrelated workers: prefer a code/package worktree or safe serial work in the current checkout, and block only editor or validation work that cannot safely proceed without the clone.

## Hybrid default

For parallel work, keep writers editor-free in `code-worktree` or `package-worktree` backends and use one persistent, warmed `unity-clone` for full validation. Integrate accepted commits into the validation clone in task-DAG order. Allow at most one Editor or BatchMode process at a time unless the project proves independent caches, ports, licenses, and outputs. Never copy `Library/`, `Temp/`, build outputs, logs, or generated imports into task worktrees.

For a single task, prefer `current` when the checkout is safe and the user does not need isolation. Use `code-worktree` for a dirty primary checkout, risky Git operations, or validation that can run without the Editor. Use `unity-clone` only when serialized/project-wide state or full validation makes it necessary.

## Packages and submodules

Use `package-worktree` only when every owned write belongs to the package repository and the task can validate through its public contract. Record the package commit separately from the consumer repository pointer update. Do not assume a superproject worktree initializes or isolates submodules correctly; create the package worktree from the package repository itself, then update the consumer pointer during integration.

Bootstrap and tooling paths must come from `.git/lemmings/environment.json`, the repo-relative `tooling.root`, or package detection. Never hard-code a consumer checkout path. Do not bootstrap from generated package caches because they are disposable and may be read-only.

## Serialized ownership and metadata

Treat scenes, prefabs, imported assets, project settings, package manifests, lock files, and their metadata as shared resources unless the Phase assigns one owner. Preserve asset/metadata pairs. Serialize changes to shared project settings and generated registries. Semantic merge tools may help integration, but they do not make concurrent ownership safe.

## Resource leases

Lease exclusive Editor, BatchMode, build output, port, device, signing identity, paid API, and shared code-generation resources to one task at a time. Record owner, scope, acquisition, release condition, and cleanup evidence in the Strict Phase. A worker without the required lease remains Blocked; it must not probe or consume the resource speculatively.

Before provisioning a full Unity clone, estimate disk use including the project, required packages, and expected generated/import state. Above 10 GiB, ask the user for permission. If they decline, use a code/package worktree or run serially in the current checkout after confirming it is safe; otherwise block only the clone-dependent step. Do not silently weaken isolation or delete existing workspace data to make room.

On close, inspect worktrees, branches, validation state, leases, and generated outputs. Cleanup is a separate, explicit action: never remove a dirty or unmerged workspace automatically.
