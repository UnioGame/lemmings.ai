# Optional telemetry

Telemetry is off by default, local, out-of-band, and fail-open. Auto resolution, dispatch, review verdict, workspace cleanup, lifecycle transitions, and Integrated never depend on telemetry, `metrics finish`, history, or `qualitySummary`. Missing Python, malformed exports, slow collection, and incomplete reports must not affect delivery.

Do not run telemetry on tool hooks. Do not place events, reports, usage, registry data, or paths in agent prompts. When enabled, record only `run_started`, `invocation_finished`, and `run_finished`; omit prompts, source, transcripts, reasoning, secrets, and workspace paths. Missing token fields remain null.

Import usage locally/offline with `lemmings metrics usage --host codex|opencode|kilo --file <export.json>`. OpenCode and Kilo exports accept input/output/reasoning and cache read/write token fields; Codex accepts its public usage aliases. Normalize without reading conversation text into model context.

Run reports and benchmarks manually after completion. Compare equivalent task cohorts and track quality, tokens, latency, provisioning time, pool hit rate, reuse failures, idle disk, cleanup latency, quarantine count, and total wall clock. Treat numeric savings as hypotheses until repeated comparable tasks support them. Never automatically expand pool limits or rewrite model routes from telemetry; propose changes offline for user approval.
