# Dispatch context

`AgentInvocation v3` is a value object created once per invocation. It contains identifiers, Task revision, attempt, role, base SHA, profile/context digests, one objective, acceptance criteria, owned/forbidden paths, up to 12 `{ref,purpose,contentHash}` references, validation commands, role limits, and output schema version. It is at most 16 KiB.

Do not embed Task, Phase, Review, AGENTS, role prompt, source content, logs, telemetry, registry data, absolute paths, or the original user transcript. References identify the smallest starting set. A worker may request one expansion naming one unresolved symbol or decision; the manager supplies only that focused result.

Limits are bounded by role: worker 24 tool calls/one expansion; reviewer 16/one; explorer 12/one. Hosts without token accounting use those counts and elapsed time. Deterministic code extracts diagnostics and truncates logs before model input.

`AgentResult v3` returns only invocation id, attempt, status, candidate head when applicable, changed paths, acceptance/validation evidence, findings, blockers, and remaining risks. It never repeats the assignment or returns transcript/reasoning.

After a capacity failure, create a new invocation. Continue in the same workspace and add only a deterministic checkpoint reference covering HEAD, Git status, changed paths, and existing evidence. Never transfer the failed model's conversation history or repeat the complete Task.

The manager rejects late or stale results unless invocation id, attempt, Task revision, base SHA, context digest, and profile digest match the dispatch record. If structured output is unavailable, allow one local schema-correction attempt without rereading repository context.
