# Lemmings roadmap

| Priority | Task | Gate |
| --- | --- | --- |
| P1 | Benchmark 6.5 on ttl-cache and a multi-module task (native Codex; Claude reviewer via dispatch) | Final correctness ≥ 90%, no pipeline stalls, no scope violations |
| P1 | Forward-test Parallel mode with real worktrees on Windows and Linux | Wave completes; merged checks pass |
| P1 | Telemetry for native subagents: parse Claude Code and Codex transcripts offline in `lemmings-telemetry` | Role runs, verdicts, repairs, and tokens match a benchmark run |
| P2 | Describe dependent waves in `SKILL.md` (integrate wave N before starting wave N+1) | Forward test with a two-wave task |
| P2 | Try Parallel mode on a Unity project with per-worktree Library | Approval gate works; no Editor lock conflicts |
| P3 | Compare routes from dispatch telemetry once enough runs exist | At least five tasks per route |
