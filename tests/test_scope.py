from support import HermeticTest, git

from lemmings.gitutil import HelperError
from lemmings.scope import changed_paths, check_scope, matches, normalize


class ScopeTests(HermeticTest):
    def test_normalize_rejects_escape_and_absolute(self):
        self.assertEqual("src/a.py", normalize("src/./b/../a.py"))
        self.assertEqual("src/a.py", normalize("src" + "\\" + "a.py"))
        for bad in ("../x", "src/../../x", "/etc/passwd", "C:/x"):
            with self.assertRaises(HelperError):
                normalize(bad)

    def test_traversal_cannot_reach_forbidden_path(self):
        path = normalize("src/owned/../../secret/key.txt")
        self.assertEqual([{"path": "secret/key.txt", "reason": "forbidden"}],
                         check_scope([path], ["src/owned"], ["secret"]))

    def test_rules(self):
        self.assertTrue(matches("src/a/b.py", ["src"]))
        self.assertFalse(matches("srcx/b.py", ["src"]))
        self.assertTrue(matches("src/a/b.py", ["src/**/*.py"]))
        self.assertTrue(matches("src/b.py", ["src/**/*.py"]))
        self.assertFalse(matches("src/a/b.py", ["src/*.py"]))
        self.assertTrue(matches("tests/test_x.py", ["tests/test_?.py"]))

    def test_changed_paths_include_both_sides_of_rename_and_unicode(self):
        repo = self.make_repo(files={"src/old.py": "x = 1\n" * 20, "docs/readme.md": "a\n"})
        base = git(repo, "rev-parse", "HEAD")
        (repo / "lib").mkdir()
        git(repo, "mv", "src/old.py", "lib/new.py")
        (repo / "src" / "модуль.py").write_text("y = 2\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "rename")
        paths = changed_paths(repo, base)
        self.assertEqual(["lib/new.py", "src/old.py", "src/модуль.py"], paths)
        self.assertEqual([{"path": "lib/new.py", "reason": "outside owned paths"}],
                         check_scope(paths, ["src"], []))

    def test_worktree_mode_sees_uncommitted_and_untracked(self):
        repo = self.make_repo(files={"a.txt": "1\n"})
        base = git(repo, "rev-parse", "HEAD")
        (repo / "a.txt").write_text("2\n", encoding="utf-8")
        (repo / "new.txt").write_text("n\n", encoding="utf-8")
        self.assertEqual([], changed_paths(repo, base))
        self.assertEqual(["a.txt", "new.txt"], changed_paths(repo, base, worktree=True))
