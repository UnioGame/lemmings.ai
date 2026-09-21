import shutil
import sys
from unittest import mock

from support import ROOT, HermeticTest

sys.path.insert(0, str(ROOT / "skills" / "lemmings" / "scripts"))
import install  # noqa: E402


class InstallTests(HermeticTest):
    def test_install_replaces_skill_and_agents(self):
        repo = self.make_repo()
        (repo / ".codex" / "agents").mkdir(parents=True)
        (repo / ".codex" / "agents" / "lemmings-validator.toml").write_text("old", encoding="utf-8")
        self.assertEqual(0, install.main(["--repo", str(repo)]))
        skill = repo / ".agents" / "skills" / "lemmings"
        self.assertTrue((skill / "SKILL.md").is_file())
        self.assertTrue((skill / "scripts" / "run.py").is_file())
        self.assertFalse(list(skill.rglob("__pycache__")))
        self.assertTrue((repo / ".codex" / "agents" / "lemmings-reviewer.toml").is_file())
        self.assertFalse((repo / ".codex" / "agents" / "lemmings-validator.toml").exists())

    def test_failed_install_restores_existing_files(self):
        repo = self.make_repo()
        skill = repo / ".agents" / "skills" / "lemmings"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("previous", encoding="utf-8")
        agent = repo / ".codex" / "agents" / "lemmings-worker.toml"
        agent.parent.mkdir(parents=True)
        agent.write_text("user override", encoding="utf-8")
        original_move = shutil.move
        calls = {"count": 0}

        def failing_move(source, destination):
            calls["count"] += 1
            if calls["count"] == 4:
                raise OSError("disk full")
            return original_move(source, destination)

        with mock.patch.object(install.shutil, "move", failing_move):
            self.assertEqual(1, install.main(["--repo", str(repo)]))
        self.assertEqual("previous", (skill / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual("user override", agent.read_text(encoding="utf-8"))
        self.assertFalse((repo / ".codex" / "agents" / "lemmings-reviewer.toml").exists())
        self.assertFalse(list(repo.glob(".lemmings-install-*")))

    def test_failure_before_any_move_leaves_everything(self):
        repo = self.make_repo()
        skill = repo / ".agents" / "skills" / "lemmings"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("previous", encoding="utf-8")
        with mock.patch.object(install.shutil, "copytree", side_effect=OSError("boom")):
            self.assertEqual(1, install.main(["--repo", str(repo)]))
        self.assertEqual("previous", (skill / "SKILL.md").read_text(encoding="utf-8"))

    def test_not_a_repository(self):
        self.assertEqual(1, install.main(["--repo", str(self.tmp)]))
