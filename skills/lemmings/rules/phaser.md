# Phaser: task-scoped rules

- Manager: establish Phaser from a direct dependency, import or CDN script and pin the actual major/version. A transitive lockfile entry is insufficient. Identify the owning Scene and asset/loading contract.
- Worker: preserve scene boot/create/update/shutdown/destroy ordering and restart behavior. Remove owned listeners, timers, tweens, physics/collider and DOM hooks on shutdown where required; avoid duplicate subscriptions after restart. Do not destroy shared textures or caches owned elsewhere.
- Preserve loader completion/error handling, input coordinate transforms, resize/scale conventions and physics timing. Do not migrate Phaser APIs or install a test framework during an unrelated change.
- Measure active objects, update/physics loops, allocations, draw calls and texture residency. Pool/batch or throttle only against the relevant game scenario; keep behavior deterministic where tests depend on it.
- Exclude node_modules, dist, bundler caches, minified bundles and full asset manifests. Read the touched Scene/loader/asset IDs. Use an independent dev-server port and browser session for each writer.
- Reviewer: use existing narrow type/lint/tests, then a bounded real-browser scenario for loading, restart, input, resize or rendering changes. Node-only tests do not prove WebGL/canvas behavior; reuse already captured matching evidence.

Version-specific source: https://docs.phaser.io/phaser/concepts/scenes
