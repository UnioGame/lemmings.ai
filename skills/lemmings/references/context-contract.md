# Dispatch context

`AgentInvocation v4` is a value object created once per invocation. It contains identifiers, Task revision, attempt, role, base SHA, profile, task, and context digests, one objective, acceptance criteria, owned/forbidden paths, initially up to 12 `{ref,purpose,contentHash}` references, validation commands, the remaining role grant, and output schema version. A manager-recorded extension may raise this to the frozen ceilings of 24 references and 32 KiB.

Do not embed Task, Phase, Review, AGENTS, role prompt, source content, logs, telemetry, registry data, absolute paths, or the original user transcript. References identify the smallest starting set. A worker may request a focused expansion naming one unresolved symbol or decision. The manager records the unresolved question and progress before extending the Task budget, and supplies only that focused result. The frozen maximum is three expansions.

Initial tool-call grants are worker 24, reviewer 16, and explorer 12. Frozen cumulative ceilings are worker 48, reviewer 32, and explorer 24. Each invocation receives only the unspent approved remainder. Trusted host accounting settles actual calls and releases unused reservation; without a trustworthy counter, settle the full grant and do not extend that role automatically. Retry, repair, and model recovery share the same ledger. Persist `RouteFailure` with `invocation fail` before retry or recovery; trusted usage releases the unused reservation, while missing or untrusted usage settles the full grant. Deterministic code extracts diagnostics and truncates logs before model input.

`AgentResult v4` returns only invocation id, attempt, status, candidate head when applicable, changed paths, acceptance/validation evidence, findings, blockers, and remaining risks. It never repeats the assignment or returns transcript/reasoning.

After a capacity failure, create a new invocation. Continue in the same workspace and add only a deterministic checkpoint reference covering HEAD, Git status, changed paths, and existing evidence. Never transfer the failed model's conversation history or repeat the complete Task.

Create and persist the dispatch with `invocation create`; hooks only expose that saved record. The manager rejects late or stale results unless invocation id, attempt, Task revision, base SHA, task digest, context digest, current working-set content, and profile digest match. Expected worker edits to owned context files are checked through the candidate diff and do not stale the invocation. Accept the result only through `invocation accept`; hook success alone does not create Candidate or Accepted evidence. If structured output is unavailable, allow one local schema-correction attempt without rereading repository context.

## Context economy

Start from owned symbols, callers and the smallest relevant test. Use narrow searches with excludes before opening files. Do not read generated caches, minified bundles, binary assets, complete scene YAML, full dependency locks or whole build logs as context. For asset work inspect exact object IDs, properties and references; preserve surrounding serialization. Read a manifest only far enough to detect the direct technology/version. Do not duplicate an explorer's answered question.

Deterministic validation stores the complete output outside prompt context and returns exit status, a bounded excerpt, omitted byte count and artifact reference. A truncated log is not a passed check. Expand only the named unresolved diagnostic. Do not rerun successful tests without new changes or unresolved risk. Prefer targeted package/type/file checks; visual behavior requires a matching runtime check, not a claim from compilation.

New Task invocations freeze `effectiveConfig` with resolved profile, selected rule references and hashes. `--preset` names a profile; the existing `--profile` still identifies a settings JSON file. Preference changes affect future Tasks. Selected rule changes or modified frozen contents invalidate the invocation; replan deliberately. Add selected rule refs to the same initial 12-reference/16-KiB budget. Extend only through the frozen Task policy and never beyond 24 references/32 KiB.

Create the invocation with the target worker repository as `--repo`, so context hashes describe its checkout bytes (including Git line-ending conversion), not the manager checkout. The canonical Task may be supplied separately. Verify the recorded hashes before dispatch; do not silently rewrite a running invocation.

Keep hashed text references byte-stable across worktrees. This distribution pins LF for source, rules and JSON/TOML through `.gitattributes`; project owners can apply an equivalent policy to their own frozen contracts. Hash the target checkout bytes and never silently rewrite a user file to satisfy a digest.

For a plan-reviewed Task, create the first reviewer invocation to freeze effectiveConfig, then bind the Review subject to the saved Task planDigest. Keep Task.workingSet as the accepted plan input; candidate reviewer invocations refresh their own reference hashes from the candidate checkout without rewriting the accepted workingSet hashes.
