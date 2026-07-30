# Task template compatibility entry point

Use the canonical [task packet](templates/task-packet.md). It includes phase/wave state, four model assignments, owned/forbidden/shared sets, candidate/fix protocol, validation debt, bounded context, review, and integration requirements.

`autoqa-markdown-v1` continues to parse historical AutoQA packets, but
validation is strict by default. A historical packet may opt into warning-only
missing structured lifecycle fields only with tracked
`legacyCompatibility: true`; never enable compatibility profile-wide.
