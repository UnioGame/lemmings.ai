# Model routing

- Orchestrator and reviewer: `gpt-5.6-sol:high`.
- Bounded Ready worker: `gpt-5.6-luna:max`.
- Elevated bounded worker spanning related files/subsystems: `gpt-5.6-terra:max`.
- Validator: `gpt-5.6-terra:medium`.
- Explorer: `gpt-5.6-luna:high`.
- Summarizer: `gpt-5.6-luna:medium`.

Preserve a valid explicit pin. Record requested, assigned, and actual; require a fallback reason only when actual differs. Route ambiguous root cause, architecture/public-contract change, weak tests, or critical risk to Sol Medium before dispatch. Escalate an underestimated Luna worker to Terra. P0/P1, invalidated contracts/plan, or architectural misunderstanding require Sol Medium. A second failed review requires replanning.
