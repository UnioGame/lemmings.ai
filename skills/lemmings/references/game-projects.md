# Game projects and large workspaces

Use this when the repository contains a game engine project or is large enough that a copy is expensive.

## Choosing a workspace

- A single sequential writer may work in the current checkout if it has no unrelated uncommitted changes.
- Each concurrent writer needs its own worktree. Never let two writers or two Editors share one project directory.
- Prefer a linked worktree (`lemmings workspace create <slug>`). Use `--clone` only when a linked worktree does not work for the project, such as tooling that rejects `.git` files or packages that resolve paths through the primary checkout.
- Run `lemmings workspace estimate` first. The estimate includes the Unity `Library` that a fresh copy will rebuild. Above 10 GiB (or the configured `largeThresholdGiB`), ask the user before creating anything. If the user declines, work serially in the current checkout.

## Unity

- Every worktree has its own `Library`, `Temp`, and Editor lock. The first import can take a long time; budget for it, or keep one long-lived validation checkout for expensive Editor checks.
- Keep `.meta` files paired with their assets and preserve GUIDs on moves and renames.
- Treat scenes, prefabs, ScriptableObjects, and Addressables as authored data. Edit them with Editor tooling or minimal YAML changes, never by regenerating them.
- Do not read `Library`, `Temp`, `Obj`, `Logs`, build outputs, or whole scene YAML as context.

## Cleanup

Remove a workspace only after its branch is merged or deliberately abandoned by the user. `lemmings workspace remove` refuses dirty workspaces, deletes a task branch only when it is merged, and deletes a clone only when all of its branch heads already exist in the primary repository. Keep failed or unmerged workspaces for inspection and tell the user they exist.
