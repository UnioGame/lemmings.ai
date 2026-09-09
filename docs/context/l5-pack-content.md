# Proposed content for optional rule packs

Manager-authored draft for C. Convert each section into the corresponding rules/*.md, keep primary links from l5-rule-sources.md, and adjust for the actual resolver contract. These are optional task-scoped instructions, not new project configuration. Preserve repository conventions and pinned versions. Do not duplicate core routing/lifecycle policy.

## unity.md
- Manager: locate ProjectVersion.txt, relevant asmdefs and the owning script/asset first. Choose code, serialization or visual validation according to the changed risk. A standalone UPM package may need an explicit Unity override.
- Worker: preserve asset GUIDs and paired .meta files on moves/renames. Treat scenes, prefabs, ScriptableObjects and Addressables references as authored contracts; use established Editor tooling for structural edits and inspect only the relevant objects/properties. Do not regenerate whole scenes or upgrade packages.
- Preserve assembly boundaries, serialization compatibility and lifecycle ownership. Unsubscribe events, dispose subscriptions and release owned resources on the project's established lifecycle; do not release shared assets globally.
- Measure before optimizing. Avoid new allocations, LINQ/boxing and repeated component/resource lookup in demonstrated hot paths; pool only when measurements justify it. Check texture import, batching and Addressables changes on the intended target.
- Exclude Library, Temp, Obj, Logs, builds and bulk YAML/binary assets from context. Read a GUID, component or short diagnostic slice, not the whole project. Each worktree uses its own writable Library and Editor directory; never share one open project between writers.
- Reviewer: verify identity/serialization/asmdef effects and lifecycle cleanup from the diff. Reuse exact reported checks. Focused EditMode/PlayMode or existing Editor diagnostics may suffice for code; prefab/layout/render changes need targeted Editor/visual evidence. Headless success alone does not prove visuals.

## unreal.md
- Manager: read the .uproject/.uplugin, owning module and pinned engine association; identify reflected C++/Blueprint or serialized/binary contracts before assigning ownership.
- Worker: keep module dependencies, build target guards, UCLASS/USTRUCT/UPROPERTY/UFUNCTION metadata and generated-header conventions consistent. A reflected rename may require redirects and Blueprint validation; do not hand-edit generated reflection output.
- Treat .uasset/.umap and OFPA external actor/object files as authored assets with exclusive ownership. Preserve source-control locks and use existing Editor tooling for asset changes. Do not broadly regenerate/resave assets or assume packaged plugin Binaries are disposable.
- Profile affected tick paths, allocation/object lifetime, async loading, draw calls and resource residency against a stated scenario. Disable unnecessary ticking only where semantics permit; follow UObject/GC ownership instead of inventing cleanup rules.
- Exclude Intermediate, Saved logs/cooks, DerivedDataCache and bulk binaries from reads. Use one module/asset query and bounded UBT/Editor diagnostics. Writable build/import caches and Editor resources belong to each workspace.
- Reviewer: check reflection/Blueprint compatibility and authored external actor changes. Run the existing narrow module/target build or targeted automation; validate touched Blueprint/asset references in the Editor where needed. Compilation does not prove asset or visual correctness.

## godot.md
- Manager: read project.godot and determine major version and GDScript versus C#. Locate the changed scene/script and referenced resources only.
- Worker: preserve authored .tscn/.tres, resource paths, NodePath, ownership and UID identity. Distinguish generated .godot/.import directories from authored per-asset .import settings and .uid sidecars; never delete them as a blanket cache cleanup.
- Keep signal connections, timers/tweens and node/resource lifetimes consistent across enter/ready/exit/free. Avoid duplicate connections after re-entry; use the pinned version's APIs rather than migrating syntax or renderer settings.
- Measure process/physics hot paths, allocations, draw calls and import costs on the target. Change update frequency or pooling only when behavior and measurements support it.
- Exclude import caches, exports, generated C# build outputs and full scene trees. Inspect the relevant node/subresource and short error slice; preserve project import conventions and isolate writable caches.
- Reviewer: verify scene references and lifecycle behavior. Prefer existing focused script/C# checks or a bounded headless launch. Confirm visual, input, physics or renderer changes in the appropriate engine scenario; headless checks do not establish rendering correctness.

## defold.md
- Manager: read game.project, the owning collection/component and pinned engine/Bob/build scripts. Trace only the relevant resource URLs, factories or GUI nodes.
- Worker: preserve authored collections, scripts, GUI, atlases and resource references. Respect init/update/fixed_update/on_message/on_input/final and collection-proxy lifecycle ordering; match hashes/URLs and release owned timers, sounds, factories or async resources appropriately.
- Preserve input focus and GUI coordinate/layout conventions. Keep native extension/platform changes behind the existing target configuration; do not introduce a new extension or change engine/Bob versions incidentally.
- Profile Lua allocations, message volume, active components, atlas/texture use and draw calls before pooling or batching. Avoid creating avoidable tables/strings in proven hot loops.
- Exclude .internal, build outputs, extension build downloads and bulk textures/audio. Read one collection/component or atlas entry; keep logs bounded and writable caches/workspaces independent.
- Reviewer: check resource resolution and component/collection lifecycle. Use the existing narrow Bob/build/test command with the pinned version; validate GUI/input or native-extension behavior on the actual target where static checks cannot establish it.

## flutter.md
- Manager: require actual Flutter SDK dependency evidence in pubspec.yaml; determine SDK constraints and existing state/routing/test conventions. Flutter does not imply Flame.
- Worker: preserve widget keys, state ownership, semantics/accessibility and the existing architecture. Dispose owned controllers, listeners and subscriptions; account for widget lifetime around async completion. Do not introduce a state framework, mass-format the app or hand-edit generated localization/codegen output.
- Preserve assets, fonts, localization and platform integration contracts. Lockfile changes require an actual dependency change; avoid package/SDK upgrades as cleanup.
- Measure rebuilds, layout/paint cost, expensive visual layers, image sizing and frame timing. Use const/rebuild boundaries where they help; do not add speculative caching or isolate work without evidence.
- Exclude .dart_tool, build, dependency caches and generated platform files from context. Start with the owning widget/state object and a targeted analyze/test command. Separate emulator/device sessions and writable build state by workspace.
- Reviewer: check lifecycle and user-visible states. Prefer an existing focused widget/unit test and affected-file diagnostics. Golden changes require inspecting the intended visual difference; animation, interaction and platform changes may need a bounded device/browser check. Analyze alone does not prove UI behavior.

## phaser.md
- Manager: establish Phaser from a direct dependency, import or CDN script and pin the actual major/version. A transitive lockfile entry is insufficient. Identify the owning Scene and asset/loading contract.
- Worker: preserve scene boot/create/update/shutdown/destroy ordering and restart behavior. Remove owned listeners, timers, tweens, physics/collider and DOM hooks on shutdown where required; avoid duplicate subscriptions after restart. Do not destroy shared textures or caches owned elsewhere.
- Preserve loader completion/error handling, input coordinate transforms, resize/scale conventions and physics timing. Do not migrate Phaser APIs or install a test framework during an unrelated change.
- Measure active objects, update/physics loops, allocations, draw calls and texture residency. Pool/batch or throttle only against the relevant game scenario; keep behavior deterministic where tests depend on it.
- Exclude node_modules, dist, bundler caches, minified bundles and full asset manifests. Read the touched Scene/loader/asset IDs. Use an independent dev-server port and browser session for each writer.
- Reviewer: use existing narrow type/lint/tests, then a bounded real-browser scenario for loading, restart, input, resize or rendering changes. Node-only tests do not prove WebGL/canvas behavior; reuse already captured matching evidence.

## pixijs.md
- Manager: require direct pixi.js dependency/import/CDN evidence and establish the pinned major. PixiJS is a renderer; do not infer a scene manager, physics engine or game framework.
- Worker: honor version-specific creation/init/destroy APIs (v8 Application.init is asynchronous). Keep mount/unmount, ticker subscriptions, resize observers, DOM canvas and GPU-resource ownership paired, including framework remounts. Preserve shared textures/assets; destroy or unload only owned resources.
- Keep coordinates, interaction/event APIs, resize/DPR behavior and async asset readiness consistent. Do not migrate Pixi major versions or replace the surrounding application framework as cleanup.
- Profile frame time, render target/filter cost, texture memory, batching and resolution on a stated device/scenario. Cap DPR or change pooling/caching only when the target budget and visual requirements justify it.
- Exclude node_modules, dist, caches, generated/minified bundles and bulk texture atlases. Read the owning renderer/view/ticker and relevant asset IDs. Keep browser/dev-server resources independent across workspaces.
- Reviewer: check init/error/teardown paths, shared resource safety and the pinned API. Prefer existing targeted checks plus a bounded browser render/input/resize/remount test. A successful typecheck does not establish GPU cleanup or visual correctness.

## platforms.md
- Load only for task-selected targets; the host OS is not the game's target. Manager records target, representative device/browser/scenario and the actual risk to check. Follow checked-in build/SDK integrations and current authoritative platform documentation; do not invent universal store limits.
- Web: consider startup/download size, memory and GPU limits, texture compression, renderer/context loss, resize/DPR, touch/pointer/keyboard input and embed restrictions. Audio/fullscreen and similar actions may require a user gesture. Validate the affected integration in its real embedding environment when relevant.
- Android/iOS: consider pause/resume, focus/audio, thermal/memory budgets, touch/safe areas, resource formats and architecture/native-plugin compatibility. Use existing build variants and signing configuration; do not expose credentials or change publishing setup for a code-only task.
- Desktop: consider window/focus/display scaling, input devices, supported graphics paths, asset loading and resource teardown. Validate the actual supported OS/architecture when native behavior changes.
- Publishing SDKs: preserve readiness, loading/progress, lifecycle and asynchronous completion contracts; ads, purchases and saves need explicit failure/cancel/idempotency behavior in their owning integration. Use sandbox/test modes and the repository's QA path where available; no incidental production publication.
- Optimize against measured scenario/device/metric; report what the narrow check establishes and what still needs target/visual evidence. Prefer existing scripts and affected targets over all-platform builds. Keep signing data, full logs, binaries, dependency caches and generated outputs out of agent context; retain artifact references and bounded diagnostic summaries.
