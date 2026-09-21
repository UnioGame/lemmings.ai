import json
from pathlib import Path

from support import HermeticTest, git

from lemmings import workspace
from lemmings.gitutil import HelperError


class WorkspaceTests(HermeticTest):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo(files={"src/a.py": "a = 1\n"})
        (self.repo / ".agents").mkdir()
        (self.repo / ".agents" / "lemmings.json").write_text(
            json.dumps({"workspace": {"root": str(self.tmp / "wt")}}), encoding="utf-8")

    def test_create_merge_remove_deletes_branch(self):
        entry = workspace.create(self.repo, "add-b")
        path = Path(entry["path"])
        self.assertEqual("task/add-b", git(path, "branch", "--show-current"))
        self.commit(path, {"src/b.py": "b = 2\n"})
        git(self.repo, "merge", "-q", "--ff-only", "task/add-b")
        result = workspace.remove(self.repo, "add-b")
        self.assertTrue(result["branchDeleted"])
        self.assertFalse(path.exists())
        self.assertEqual([], workspace.list_workspaces(self.repo))

    def test_remove_refuses_dirty_and_keeps_unmerged_branch(self):
        path = Path(workspace.create(self.repo, "wip")["path"])
        (path / "scratch.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(HelperError, "uncommitted or untracked"):
            workspace.remove(self.repo, "wip")
        self.assertTrue((path / "scratch.txt").exists())
        (path / "scratch.txt").unlink()
        self.commit(path, {"src/c.py": "c\n"})
        result = workspace.remove(self.repo, "wip")
        self.assertEqual("task/wip", result["branchRetained"])
        self.assertIn("task/wip", git(self.repo, "branch", "--list", "task/wip"))

    def test_clone_removal_requires_commits_in_primary(self):
        entry = workspace.create(self.repo, "cloned", clone=True)
        path = Path(entry["path"])
        self.assertEqual("task/cloned", git(path, "branch", "--show-current"))
        self.commit(path, {"src/d.py": "d\n"})
        with self.assertRaisesRegex(HelperError, "missing from the primary"):
            workspace.remove(self.repo, "cloned")
        git(self.repo, "fetch", "-q", str(path), "task/cloned:task/cloned")
        workspace.remove(self.repo, "cloned")
        self.assertFalse(path.exists())

    def test_large_workspace_requires_approval(self):
        config = self.repo / ".agents" / "lemmings.json"
        config.write_text(json.dumps({"workspace": {"root": str(self.tmp / "wt"), "largeThresholdGiB": 0}}), encoding="utf-8")
        with self.assertRaisesRegex(HelperError, "approve-large"):
            workspace.create(self.repo, "big")
        self.assertTrue(Path(workspace.create(self.repo, "big", approve_large=True)["path"]).is_dir())

    def test_estimate_counts_unity_library(self):
        (self.repo / "Game" / "Assets").mkdir(parents=True)
        (self.repo / "Game" / "ProjectSettings").mkdir()
        (self.repo / "Game" / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0\n", encoding="utf-8")
        library = self.repo / "Game" / "Library"
        library.mkdir()
        (library / "cache.bin").write_bytes(b"0" * 4096)
        result = workspace.estimate(self.repo, {})
        self.assertTrue(result["unityProject"].endswith("Game"))
        self.assertGreaterEqual(result["engineCacheBytes"], 4096)

    def test_invalid_slug(self):
        with self.assertRaises(HelperError):
            workspace.create(self.repo, "Bad Slug")
