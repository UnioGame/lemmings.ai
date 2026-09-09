# Dispatch context

`AgentInvocation v4` is a value object created once per invocation. It contains identifiers, Task revision, attempt, role, base SHA, profile, task, and context digests, one objective, acceptance criteria, owned/forbidden paths, up to 12 `{ref,purpose,contentHash}` references, validation commands, role limits, and output schema version. It is at most 16 KiB.

Do not embed Task, Phase, Review, AGENTS, role prompt, source content, logs, telemetry, registry data, absolute paths, or the original user transcript. References identify the smallest starting set. A worker may request one expansion naming one unresolved symbol or decision; the manager supplies only that focused result.

Limits are bounded by role: worker 24 tool calls/one expansion; reviewer 16/one; explorer 12/one. Hosts without token accounting use those counts and elapsed time. Deterministic code extracts diagnostics and truncates logs before model input.

`AgentResult v4` returns only invocation id, attempt, status, candidate head when applicable, changed paths, acceptance/validation evidence, findings, blockers, and remaining risks. It never repeats the assignment or returns transcript/reasoning.

After a capacity failure, create a new invocation. Continue in the same workspace and add only a deterministic checkpoint reference covering HEAD, Git status, changed paths, and existing evidence. Never transfer the failed model's conversation history or repeat the complete Task.

Create and persist the dispatch with `invocation create`; hooks only expose that saved record. The manager rejects late or stale results unless invocation id, attempt, Task revision, base SHA, task digest, context digest, current working-set content, and profile digest match. Expected worker edits to owned context files are checked through the candidate diff and do not stale the invocation. Accept the result only through `invocation accept`; hook success alone does not create Candidate or Accepted evidence. If structured output is unavailable, allow one local schema-correction attempt without rereading repository context.

## Context economy

Start from owned symbols, callers and the smallest relevant test. Use narrow searches with excludes before opening files. Do not read generated caches, minified bundles, binary assets, complete scene YAML, full dependency locks or whole build logs as context. For asset work inspect exact object IDs, properties and references; preserve surrounding serialization. Read a manifest only far enough to detect the direct technology/version. Do not duplicate an explorer's answered question.

Deterministic validation stores the complete output outside prompt context and returns exit status, a bounded excerpt, omitted byte count and artifact reference. A truncated log is not a passed check. Expand only the named unresolved diagnostic. Do not rerun successful tests without new changes or unresolved risk. Prefer targeted package/type/file checks; visual behavior requires a matching runtime check, not a claim from compilation.

New Task invocations freeze `effectiveConfig` with resolved profile, selected rule references and hashes. `--preset` names a profile; the existing `--profile` still identifies a settings JSON file. Preference changes affect future Tasks. Selected rule changes or modified frozen contents invalidate the invocation; replan deliberately. Add selected rule refs to the same 12-reference/16-KiB budget rather than expanding the budget.

Create the invocation with the target worker repository as `--repo`, so context hashes describe its checkout bytes (including Git line-ending conversion), not the manager checkout. The canonical Task may be supplied separately. Verify the recorded hashes before dispatch; do not silently rewrite a running invocation.
