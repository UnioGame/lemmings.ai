# Provider completion replan

L5-B exhausted its one repair and is Replan Required. L5-B2 is a fresh bounded task at e109f815c15944e8a38d8900cd7474c84b28196c. Route schema and four owned files remain unchanged. The only additive API extension is probe_route(route, *, repo=None, home=None). No new framework.

## Verified metadata (2026-09-10)
Official https://opencode.ai/docs/go/ Endpoints table proves protocol by exact model ID. /zen/go/v1/models has id/object/created/owned_by only. Local ~/.cache/opencode/models.json is a provider map with opencode-go.models keyed by model; provider npm/API do not prove every model protocol. Use a small documented exact-ID mapping with source/date and unknown fallback, optionally explicit per-model metadata. Do not infer by model-name family or provider-wide Responses.
Responses: grok-4.6, gpt-5.6-luna, muse-spark-1.3-contributor, muse-spark-1.2-contributor.
Chat completions: glm-5.3-flash, glm-5.3, glm-5.2, glm-5.1, kimi-k3, kimi-k2.7-code, kimi-k2.6, longcat-2.0, deepseek-v4-pro, deepseek-v4-flash, deepseek-v4-flash-vision-exp, mimo-v2.5, mimo-v2.5-pro, hy4-preview, hy3, omen-alpha.
Messages: minimax-m3, minimax-m2.7, minimax-m2.5, qwen3.8-max, qwen3.8-flash, qwen3.7-max, qwen3.7-plus, qwen3.6-plus.
Go requires coding-client User-Agent and stable x-opencode-session per conversation. Metadata GET is allowed during scan; no POST/inference. Configured auth may be resolved internally but never exposed, sent to an untrusted route endpoint or printed; bounded timeout, no exception bodies. 403/unavailable remains stale/unavailable. No CLI refresh/inference in tests.

## Acceptance
1. Read production-shaped public catalog AND standard local OpenCode cache; retain all IDs. Proven protocols become compatible with supported executor; new/unproven IDs remain unknown. Configured Codex aliases remain distinct; diagnostics only. Catalog access is not account access.
2. probe_route takes an actual scan route unchanged. Resolve exact identity to trusted configured or documented endpoint and credential source internally. Do not trust caller endpoint/apiKey; reject unsafe destination changes. Test responses/messages/chat success and missing auth/unknown/HTTP failure without secret output, exact selected model and no other inference. Standard auth.json/provider env references supported by discovery need consistent probe behavior.
3. Proposal build/apply canonicalizes from current inventory or rejects changed executor/protocol/profileName/compatibility. Digest binds inventory/manual inputs. Persist only trusted schema; manual priority unchanged.
4. Tests use real id-only Go response, local cache shape, authenticated/unavailable GET and scan -> selected route -> probe, and tampered proposal execution fields. Existing focused tests pass. No paid requests.

Fresh worker: Terra Max after two Luna candidates failed material protocol integration review (complexity, not quota recovery). One repair budget for this new plan. Reviewer checks exact immutable range plus inherited B range as integration context.

## Probe repository seam (plan review correction)
CLI passes its canonical --repo to probe_route. Resolve repository-local config only from that repo and match exact host/provider/model/profile/protocol against a fresh offline scan for that repo before reading its trusted endpoint/auth. No repo means personal/documented providers only; never guess cwd for repo-local credentials. No caller endpoint or credential fields accepted. Add two-repository same-provider collision test proving selected repo controls endpoint and foreign execution-field mismatches are rejected. Go mapping is scoped to verified Go endpoint/provider identity, never arbitrary providers reusing a model ID. Manager owns CLI call-site update; B2 owns optional repo API.
