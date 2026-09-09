# PixiJS: task-scoped rules

- Manager: require direct pixi.js dependency/import/CDN evidence and establish the pinned major. PixiJS is a renderer; do not infer a scene manager, physics engine or game framework.
- Worker: honor version-specific creation/init/destroy APIs (v8 Application.init is asynchronous). Keep mount/unmount, ticker subscriptions, resize observers, DOM canvas and GPU-resource ownership paired, including framework remounts. Preserve shared textures/assets; destroy or unload only owned resources.
- Keep coordinates, interaction/event APIs, resize/DPR behavior and async asset readiness consistent. Do not migrate Pixi major versions or replace the surrounding application framework as cleanup.
- Profile frame time, render target/filter cost, texture memory, batching and resolution on a stated device/scenario. Cap DPR or change pooling/caching only when the target budget and visual requirements justify it.
- Exclude node_modules, dist, caches, generated/minified bundles and bulk texture atlases. Read the owning renderer/view/ticker and relevant asset IDs. Keep browser/dev-server resources independent across workspaces.
- Reviewer: check init/error/teardown paths, shared resource safety and the pinned API. Prefer existing targeted checks plus a bounded browser render/input/resize/remount test. A successful typecheck does not establish GPU cleanup or visual correctness.

Version-specific source: https://pixijs.com/8.x/guides/concepts/garbage-collection
