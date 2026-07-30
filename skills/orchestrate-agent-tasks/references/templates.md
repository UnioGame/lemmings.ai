# Artifact templates

Use the package templates under `Documentation~/tasks/templates/`: `phase-baseline.md`, `dispatch-manifest.md`, `task-packet.md`, `handoff.md`, `sol-review.md`, `integration-evidence.md`, `routing-scorecard.md`, and `consumer-profile.json`.

Task adapters parse layouts and do not alter validation severity.
`generic-markdown-v1` is canonical; `autoqa-markdown-v1` parses established
AutoQA packets without migrating history. New packets are always strict.
Warning-only compatibility requires `legacyCompatibility: true` on the specific
tracked historical task; profile-wide legacy defaults are invalid. Profiles
supply path patterns, model routing, validation commands, shared-path owners,
gates, cleanup, context exclusions, mandatory candidate commits, and
integration strategy. Derived runtime state is untracked metadata, never a
replacement for tracked artifacts.
