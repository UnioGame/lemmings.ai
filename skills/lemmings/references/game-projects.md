# Workspace lifecycle

The manager chooses `current`, `code-worktree`, `package-worktree`, task-specific `unity-clone`, persistent validation clone, or a user workspace. Tooling may execute explicit register/claim/release/remove decisions atomically. Never use force, reset-hard, automatic git clean, background cleanup, or SessionStart deletion/prune.

Registry: `<git-common-dir>/lemmings/workspaces-v4.json`. It owns absolute paths, common-dir identity, backend, manager, lifetime, active/idle/quarantined/retiring state, task/phase, branch/head/base, estimate/approval, timestamps, leases/processes, and quarantine reason. Task stores only `workspaceId`, backend, policy, estimate, lifecycle, and final disposition. Claim/release requires the expected registry revision, so only one manager can win.

The shared writer pool defaults to two idle worktrees and 10 GiB per Git common dir, LRU eviction. Reconcile only on provision, claim, release, status, or Phase close. Validation clones and user workspaces are excluded. A workspace over 10 GiB requires separate retention approval; otherwise safely remove it after integration.

Reuse the same task workspace through implementation, validation, immutable review, one repair, and revalidation. Do not release it before Integrated. Retain Active failures, Replan Required, and started Cancelled tasks for diagnosis.

Cross-task reuse requires Lemmings ownership, compatible backend/common dir/package, prior Integrated evidence, exact Git registration, clean tracked/untracked/submodule state, no unfinished Git operation, process, editor, invocation, or lease, only allowlisted ignored caches, and a new base equal to integration head. Claim first, repeat checks, create a fresh branch at the exact head without force/reset/clean, update registry/Task, and create a new agent invocation. Quarantine without modification on any failure.

Persistent validation clones are project lifetime, never assigned to writers, retain Unity Library, and are never automatically cleaned or removed. Dirty state blocks only validation and needs manual resolution.

Safe automatic removal applies only to an exact registered non-primary Lemmings worktree, or a standalone task-specific Unity clone whose Git top-level exactly equals its registry path, after its lifetime ends or it is evicted, with Integrated evidence (or if never Active), clean status/submodules, no Git operation, invocation/process/lease, and no other owner. Use ordinary `git worktree remove` for linked worktrees; delete only the verified standalone clone root for a task clone. Never auto-remove dirty/untracked, unmerged, Blocked/Replan/started-Cancelled, user, unknown, locked, primary, or validation workspaces. Failure quarantines the entry and never reopens Integrated.
