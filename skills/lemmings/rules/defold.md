# Defold: task-scoped rules

- Manager: read game.project, the owning collection/component and pinned engine/Bob/build scripts. Trace only the relevant resource URLs, factories or GUI nodes.
- Worker: preserve authored collections, scripts, GUI, atlases and resource references. Respect init/update/fixed_update/on_message/on_input/final and collection-proxy lifecycle ordering; match hashes/URLs and release owned timers, sounds, factories or async resources appropriately.
- Preserve input focus and GUI coordinate/layout conventions. Keep native extension/platform changes behind the existing target configuration; do not introduce a new extension or change engine/Bob versions incidentally.
- Profile Lua allocations, message volume, active components, atlas/texture use and draw calls before pooling or batching. Avoid creating avoidable tables/strings in proven hot loops.
- Exclude .internal, build outputs, extension build downloads and bulk textures/audio. Read one collection/component or atlas entry; keep logs bounded and writable caches/workspaces independent.
- Reviewer: check resource resolution and component/collection lifecycle. Use the existing narrow Bob/build/test command with the pinned version; validate GUI/input or native-extension behavior on the actual target where static checks cannot establish it.

Version-specific source: https://defold.com/manuals/application-lifecycle/
