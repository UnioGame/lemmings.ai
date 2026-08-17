# Context-contract v2

Derive dispatch context deterministically from the canonical Task, optional Phase, and requested role. Use compact JSON.

## Required projections

- Explorer: one question/focus and at most three starting references.
- Worker: goal, acceptance, dependencies, owned/shared/forbidden paths, risks, frozen contracts, working set, assigned model, and declared validation.
- Validator: acceptance, risks, working set, risk-to-test map, commands, and allowed outputs.
- Reviewer: exact `baseSha..headSha`, acceptance, embedded handoff, actual model, and validation evidence.
- Summarizer: task identifier and explicitly supplied evidence only.

Never include transcripts, reasoning, tool payloads, raw logs, secrets, absolute paths, or unrelated Task fields.

## Budget

Canonical profile values are 16,384 encoded bytes, 12 working-set entries, and one focused expansion. Exceeding a limit emits a warning and still injects the packet. Missing required schema-v2 fields blocks dispatch. Record only packet bytes, section count, working-set count, expansions, and warning count.

Each working-set entry is `{ "ref": "repo/path#Symbol", "purpose": "why needed" }`. Expansion requests must name one symbol or decision.
