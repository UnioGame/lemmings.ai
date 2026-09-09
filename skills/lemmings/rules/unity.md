# Unity: task-scoped rules

- Manager: locate ProjectVersion.txt, relevant asmdefs and the owning script/asset first. Choose code, serialization or visual validation according to the changed risk. A standalone UPM package may need an explicit Unity override.
- Worker: preserve asset GUIDs and paired .meta files on moves/renames. Treat scenes, prefabs, ScriptableObjects and Addressables references as authored contracts; use established Editor tooling for structural edits and inspect only the relevant objects/properties. Do not regenerate whole scenes or upgrade packages.
- Preserve assembly boundaries, serialization compatibility and lifecycle ownership. Unsubscribe events, dispose subscriptions and release owned resources on the project's established lifecycle; do not release shared assets globally.
- Measure before optimizing. Avoid new allocations, LINQ/boxing and repeated component/resource lookup in demonstrated hot paths; pool only when measurements justify it. Check texture import, batching and Addressables changes on the intended target.
- Exclude Library, Temp, Obj, Logs, builds and bulk YAML/binary assets from context. Read a GUID, component or short diagnostic slice, not the whole project. Each worktree uses its own writable Library and Editor directory; never share one open project between writers.
- Reviewer: verify identity/serialization/asmdef effects and lifecycle cleanup from the diff. Reuse exact reported checks. Focused EditMode/PlayMode or existing Editor diagnostics may suffice for code; prefab/layout/render changes need targeted Editor/visual evidence. Headless success alone does not prove visuals.

Version-specific source: https://docs.unity3d.com/Manual/AssetMetadata.html
