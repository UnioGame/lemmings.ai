# Delivery checkpoint

Source package remains at baseline f228b20; do not overwrite the active root horde-cards runtime or unrelated game changes.

- Manager branch: codex/lemmings-platform-delivery. CORE/defaults/frozen profiles/CLI/installer/README committed or staged here.
- A2 candidate 3d7c5ba: independent review accepted; not integrated yet.
- B2 candidate 7b09bac: protocol/auth/profile invariants accepted, terminal replan for production cache performance.
- B3 candidate 191bc3f: native JSON fast path, linear JSONC, correct provider-keyed model IDs and connected-cache filtering.16 discovery tests +9 profile tests; large4.5MB fixture; measured real offline scan0.868seconds before connected filter. Independent review is pending.
- Host twice rejected fresh reviewer spawn with agent thread limit. Old provider_completion and workspace_completion remain pending_init despite repeated interrupts. No model quota failure. Cancelled reviewer invocation90e351eeae225100086dbe90 was never executed.
- Resume with fresh reviewer for B3, new saved invocation attempt2; same immutable candidate. Review only40-line source followup7b09bac..191bc3f; prior protocol/auth work already accepted. No paid probe or personal config edit.
- Then integrate whole A2+B3 wave into manager preserving commit ancestry; targeted integration evidence at exact HEAD and canonical lifecycle. Update phase integrationHead.
- C/D Draft dependencies already point to A2+B3. Create separate package worktrees at that phase head. Serialize if host slots limited. C content prepared in l5-pack-content.md and l5-rule-sources.md; D contract in l5-runner-capabilities.md. Neither rules.py/packs nor runners.py is implemented yet.
- Finish full tests, CLI workflows, skill/link/JSON checks, forward cases, immutable assembled review, exact integration. Move bootstrap .agents/lemmings.json to docs/tasks/l5.profile.json before final delivery; do not ship task-specific manual model defaults.
- Only after acceptance fast-forward original package if still clean. Do not reinstall root skill while its other task is active. Deactivate only this package runtime marker. Preserve unaccepted/dirty workspaces.

User subsequently authorized sequential continuation with manager self-review. Independent reviewer availability no longer blocks this delivery. Preserve all other safety and exact integration guarantees.
