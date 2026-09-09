# Godot: task-scoped rules

- Manager: read project.godot and determine major version and GDScript versus C#. Locate the changed scene/script and referenced resources only.
- Worker: preserve authored .tscn/.tres, resource paths, NodePath, ownership and UID identity. Distinguish generated .godot/.import directories from authored per-asset .import settings and .uid sidecars; never delete them as a blanket cache cleanup.
- Keep signal connections, timers/tweens and node/resource lifetimes consistent across enter/ready/exit/free. Avoid duplicate connections after re-entry; use the pinned version's APIs rather than migrating syntax or renderer settings.
- Measure process/physics hot paths, allocations, draw calls and import costs on the target. Change update frequency or pooling only when behavior and measurements support it.
- Exclude import caches, exports, generated C# build outputs and full scene trees. Inspect the relevant node/subresource and short error slice; preserve project import conventions and isolate writable caches.
- Reviewer: verify scene references and lifecycle behavior. Prefer existing focused script/C# checks or a bounded headless launch. Confirm visual, input, physics or renderer changes in the appropriate engine scenario; headless checks do not establish rendering correctness.

Version-specific source: https://docs.godotengine.org/en/stable/tutorials/best_practices/version_control_systems.html
