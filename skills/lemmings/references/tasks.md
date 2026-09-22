# Task journal

The journal records every task and its status so work can be tracked, reviewed, and resumed after a session ends. It has two parts:

- **`docs/tasks/TASKS.md`**, the index: one short row per task and the only place that holds the current status. Read it whole to see the program of work and the next wave.
- **`docs/tasks/<ID>.md`**, the task file: the brief, a timestamped log of every status change, and evidence. Open it only for the task you are working on. It is also the brief you give the worker (`dispatch --brief docs/tasks/<ID>.md`).

Configure a different directory with `.agents/lemmings.json` → `"tasks": {"dir": "..."}`. Commit the journal so the team sees it. Templates: [TASKS.md](../templates/TASKS.md) and [PROJ-01.md](../templates/PROJ-01.md).

## Who writes

Only the manager writes to the journal. Workers and reviewers report to the manager and never edit it. Workers must not list journal files in their owned paths.

## Index format

```markdown
| ID | Task | Where | Who | Depends on | Status |
| --- | --- | --- | --- | --- | --- |
| NCORE-13 | Compact delta wire format | `unigame.staticecs.network` | W (codex-worker) | 11 | Done (`50fc170`): 44 → 16 B/entity — [details](NCORE-13.md) |
| NCORE-15b | Cells: CPU cost and scope-change errors | network + server | W (custom-worker → custom-worker-strong) | 15 | In review (`864161b`) — [details](NCORE-15b.md) |
```

- **ID**: unique, like `PROJ-12`. Use a suffix for a follow-up (`PROJ-12a`); never repeat a row.
- **Task**: a short title. Long descriptions go in the task file.
- **Where**: the repository, package, or area.
- **Who**: the role (`M` manager, `W` worker, `R` reviewer, `E` explorer) and the agent. After an escalation it shows the chain, for example `W (custom-worker → custom-worker-strong)`.
- **Depends on**: comma-separated ids. A short form such as `05` means the row's own prefix (`PROJ-05`). Use `—` for none.
- **Status**: starts with exactly one of `Not started`, `In progress`, `In review`, `Repair N`, `Escalated`, `Done`, `Deferred`, `Blocked`. It may add a commit in backticks, a result of at most one short sentence, and the details link. `Done` must name the commit that proves it. Measurements, tables, and reasoning belong in the task file or a linked context document, not in the cell.

## Task file format

```markdown
# PROJ-13 · Compact delta wire format

## Brief
Goal / Acceptance / Owned paths / Checks / Risks / Context

## Log
- 2026-09-21 14:05 In progress — codex-worker on task/proj-13
- 2026-09-21 14:31 In review — `50fc170` to codex-reviewer
- 2026-09-21 14:40 Repair 1 — P1: varint overflow above index 2^21
- 2026-09-21 15:10 Done — merged, EditMode 332/333 `9f8e7d6`

## Evidence
Metrics, benchmark tables, reviewer findings, links to context documents.
```

Each log line is `- YYYY-MM-DD HH:MM <Status> — <short note>`. The last log line's status must equal the index status.

## When to write

| Moment | Index | Task file log |
| --- | --- | --- |
| Plan creates a brief | add the row as `Not started` | create the file with the brief |
| A writer starts | `In progress`, set Who | line with agent and branch |
| Candidate goes to review | `In review` with the candidate commit | line with commit and reviewer |
| Reviewer requests changes | `Repair N` | line with the blocking findings |
| Escalation | `Escalated`, update Who | line with old → new agent and why |
| Accepted and integrated | `Done` with the merge or candidate commit and a one-line result | line with checks; details under Evidence |
| Postponed or stuck | `Deferred` or `Blocked` with the reason | same reason |

Simple work gets a single row that goes straight to `Done`. For a trivial fix you may skip the journal unless the user asked for it. On resume, read the index, continue the active rows from their task files, and then take the next wave.

## Helper commands (optional)

With Python, `lemmings tasks ...` performs the same edits deterministically:

- `tasks add <ID> --title ... [--where] [--who] [--depends "05, 06"] [--brief brief.md]` adds the row and creates the task file.
- `tasks update <ID> --status "Repair 1" [--note ...] [--commit <sha>] [--who ...]` rewrites the status cell and appends the log line.
- `tasks next` lists `Not started` tasks whose dependencies are `Done`: the next Parallel wave.
- `tasks list` lists every task with its status and a count per status.
- `tasks check` reports errors for duplicate ids, unknown or cyclic dependencies, a status outside the vocabulary, `Done` without a commit, broken links, and an index that disagrees with the log. It warns when a task started before its dependencies were `Done` or when its commit is not in this repository.
