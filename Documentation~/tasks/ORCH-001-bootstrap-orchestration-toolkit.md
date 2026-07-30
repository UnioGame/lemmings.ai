# ORCH-001: Bootstrap orchestration toolkit

## Metadata

| Field | Value |
| --- | --- |
| Roadmap ID | `ORCH-001` |
| Priority | `P1` |
| Status | `In Progress` |
| Risk | `Medium` |
| Tools repository base | Empty remote; `main` unborn |
| Package-container base | Recorded locally before mutation; omitted from the portable task file |
| Root repository base | Recorded locally before mutation; omitted from the portable task file |
| Isolation | Single writer; preserve existing dirty root checkout |
| Updated | `2026-07-30` |

## Agent Assignments

| Role / Scope | Model | Effort | Source | Command | State |
| --- | --- | --- | --- | --- | --- |
| Orchestrator / all repositories | `gpt-5.6-sol` | `high` | direct user | — | effective |
| Reviewer / mandatory final gate | `gpt-5.6-sol` | `high` | required invariant | — | effective |
| Forward-test / simple task | `gpt-5.6-terra` | `medium` | test plan | — | completed |
| Forward-test / parallel routing | `gpt-5.6-terra` | `medium` | test plan | — | completed |
| Forward-test / Unity conflicts | `gpt-5.6-sol` | `medium` | Complex Worker default | — | completed |
| Forward-test / unavailable model | `gpt-5.6-terra` | `low` | test plan | — | completed |

## Repository Integration Order

| Order | Repository | Branch / Worktree | Result |
| ---: | --- | --- | --- |
| 1 | `unigame.ai.tools` | `main` / nested package path | Commit and push canonical content |
| 2 | Package-container repository | dedicated `codex/<task-slug>` branch from recorded base | Register `.gitmodules` entry and tools gitlink |
| 3 | Root consumer repository | dedicated `codex/<task-slug>` branch from recorded base | Add `AGENTS.md` pointer and update package-container gitlink |

The package-container commit depends on a reachable tools commit. The root commit depends on the package-container commit. Exact private parent revisions stay in local Git history/evidence and are not copied to a portable or potentially public task file. Parent commits are local integration artifacts unless separately requested for push.

## Goal

Create and publish a UPM-compatible, documentation-only repository containing a reusable orchestration skill, full guide, roadmap, and decision-complete task template.

## Non-Goals

- No Unity Runtime or Editor code.
- No `GameClient/Packages/manifest.json` dependency.
- No plugin packaging.
- No license choice without repository-owner direction.
- No inclusion of unrelated user changes.

## Decisions

- Canonical repository: `https://github.com/UnioGame/unigame.ai.tools.git`.
- Default branch: `main`.
- Skill name: `orchestrate-agent-tasks`.
- Skill mode commands: `on`, `off`, `auto`, `status`.
- User model assignments support thread and task scope.
- Orchestrator defaults to `gpt-5.6-sol/high`; higher effort requires an explicit user request.
- Parallel writers require isolated worktrees.
- Tests use requirement/risk coverage.

## Ownership

### Write Set

- `GameClient/Game.Packages/unigame.ai.tools/**`
- `GameClient/Game.Packages/.gitmodules`
- `GameClient/Game.Packages/unigame.ai.tools` gitlink
- root `AGENTS.md` orchestration pointer
- root `GameClient/Game.Packages` gitlink

### Excluded Dirty State

- User-owned Unity assets and project settings in `mtt.client`.
- Any unrelated nested package changes.

## Implementation Plan

- [x] Confirm remote exists and has no refs.
- [x] Clone remote and create `main`.
- [x] Initialize skill with `skill-creator/init_skill.py`.
- [x] Author package metadata and repository rules.
- [x] Author orchestration skill and UI metadata.
- [x] Author full guide, roadmap, and task template.
- [x] Validate skill, JSON, Markdown links, and diff.
- [x] Forward-test simple, parallel, Unity-conflict, and model-override scenarios.
- [x] Complete independent Sol High review.
- [ ] Commit and push `unigame.ai.tools/main`.
- [ ] Register nested submodule in `Game.Packages`.
- [ ] Add root `AGENTS.md` pointer and update parent gitlinks.
- [ ] Create isolated parent commits without unrelated changes.

## Acceptance Criteria

- [ ] Remote `main` contains the complete package.
- [x] `quick_validate.py` passes.
- [x] All local Markdown links resolve.
- [x] User model assignments are explicit, scoped, validated, and never silently substituted.
- [x] Guide covers parallel worktrees, Unity/submodule boundaries, edge cases, and risk-based tests.
- [x] Forward-tests demonstrate expected mode/model routing.
- [ ] Parent repositories contain only intended integration commits.
- [ ] Existing user changes remain uncommitted and untouched.

## Risk-To-Test Matrix

| Risk / Criterion | Test | Expected Evidence |
| --- | --- | --- |
| Invalid skill metadata | `quick_validate.py` | Pass |
| Invalid package metadata | JSON parse | Pass |
| Broken guide/template links | Markdown link check | Zero missing local targets |
| Incorrect mode handling | Fresh-agent forward-test | `on/off/auto/status` semantics preserved |
| Silent model substitution | Fresh-agent forward-test | Spawn blocked and incompatibility reported |
| Unsafe parallel write plan | Fresh-agent forward-test | Worktrees or serialization selected |
| User changes included | Git staged-diff review | Only declared paths |

## Progress

- Empty GitHub repository cloned successfully.
- Skill scaffold created through the required skill-creator initializer.
- Repository content is authored and passes local static checks.
- Four fresh-agent scenarios verified simple-task bypass, parallel ownership/model routing, Unity conflict serialization, and unavailable-model blocking.
- Independent Sol High review found cross-repository metadata, Reviewer invariants, assignment auditability, and evidence gaps; all findings were addressed before commit.

## Validation Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Remote probe | Pass | Git endpoint returned no refs |
| Skill initialization | Pass | `init_skill.py` created skill and UI metadata |
| Skill validation | Pass | `quick_validate.py`: `Skill is valid!` |
| Package metadata | Pass | `package.json` parsed successfully |
| Local Markdown links | Pass | Zero unresolved local targets |
| Whitespace | Pass | `git diff --check` after EOF cleanup |
| Forward: simple task | Pass | `auto` kept a one-file typo single-agent without task/roadmap files |
| Forward: parallel docs | Pass | Exact write sets and isolated worktrees required; user model pins preserved |
| Forward: Unity conflict | Pass | Shared prefab/meta, submodule, Addressables, Editor, and device serialized |
| Forward: unavailable model | Pass | Invalid pinned spawn blocked; independent valid work continued; no substitution |
| Independent review | Pass | Sol High findings resolved; no code or files edited by Reviewer |

## Reviewer Findings

- Closed: record exact bases, branches, and serial dependencies for all three repositories.
- Closed: keep mandatory Reviewer fixed at `gpt-5.6-sol/high`.
- Closed: replace scalar model metadata with auditable per-role assignments.
- Closed: link audit claims to dated task evidence.
- Closed before review completion: remove extra EOF blank lines reported by `git diff --check`.

## Rollback

- New repository content is isolated from gameplay code.
- Parent integration can be reverted by removing only the new gitlink and `.gitmodules` entry.
- Do not reset or clean unrelated parent worktrees.
