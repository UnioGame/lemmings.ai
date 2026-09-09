# Flutter: task-scoped rules

- Manager: require actual Flutter SDK dependency evidence in pubspec.yaml; determine SDK constraints and existing state/routing/test conventions. Flutter does not imply Flame.
- Worker: preserve widget keys, state ownership, semantics/accessibility and the existing architecture. Dispose owned controllers, listeners and subscriptions; account for widget lifetime around async completion. Do not introduce a state framework, mass-format the app or hand-edit generated localization/codegen output.
- Preserve assets, fonts, localization and platform integration contracts. Lockfile changes require an actual dependency change; avoid package/SDK upgrades as cleanup.
- Measure rebuilds, layout/paint cost, expensive visual layers, image sizing and frame timing. Use const/rebuild boundaries where they help; do not add speculative caching or isolate work without evidence.
- Exclude .dart_tool, build, dependency caches and generated platform files from context. Start with the owning widget/state object and a targeted analyze/test command. Separate emulator/device sessions and writable build state by workspace.
- Reviewer: check lifecycle and user-visible states. Prefer an existing focused widget/unit test and affected-file diagnostics. Golden changes require inspecting the intended visual difference; animation, interaction and platform changes may need a bounded device/browser check. Analyze alone does not prove UI behavior.

Version-specific source: https://docs.flutter.dev/perf/best-practices
