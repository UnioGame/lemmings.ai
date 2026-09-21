import json
import re
import subprocess
import sys
import tomllib

from support import ROOT, HermeticTest

SKILL = ROOT / "skills" / "lemmings"


class PackageTests(HermeticTest):
    def test_versions_agree(self):
        versions = {
            "package.json": json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"],
            "claude plugin": json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"],
            "codex plugin": json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"],
            "pyproject": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        }
        sys.path.insert(0, str(SKILL / "scripts"))
        import install
        import lemmings
        versions.update(helper=lemmings.__version__, installer=install.VERSION)
        self.assertEqual({"6.5.0"}, set(versions.values()), versions)

    def test_agent_toml_is_generated_from_markdown(self):
        process = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_agents.py"), "--check"],
                                 capture_output=True, text=True)
        self.assertEqual(0, process.returncode, process.stderr)

    def test_markdown_links_resolve(self):
        files = [ROOT / "README.md", ROOT / "AGENTS.md", *SKILL.rglob("*.md")]
        for path in files:
            for target in re.findall(r"\]\(([^)#\s]+)\)", path.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://")):
                    continue
                self.assertTrue((path.parent / target).exists(), f"{path.name} links to missing {target}")

    def test_skill_frontmatter_and_size(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"^---\nname: lemmings\ndescription: .+\n---\n")
        self.assertLess(len(text.splitlines()), 150)

    def test_five_stages_are_documented(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for number, stage in enumerate(("Discover", "Plan", "Refine", "Implement", "Verify"), 1):
            self.assertRegex(skill, rf"## {number}\. {stage}")
            self.assertIn(f"**{number}. {stage}**", readme)
        self.assertIn("```mermaid", readme)

    def test_auto_is_the_default_mode(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for mode in ("**Auto** (default)", "**Simple**", "**Standard**", "**Parallel**"):
            self.assertIn(mode, text)
        self.assertIn("How Auto decides", text)

    def test_skill_has_a_python_free_path_for_every_required_step(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("The skill works without Python", text)
        for fallback in ("git worktree add -b task/<slug>", "git worktree remove <path>", "git branch -d task/<slug>",
                         "git diff --name-only <base>..<head>", "read `defaults.json` in this skill",
                         "**Without Python:**", "Without the helper, estimate"):
            self.assertIn(fallback, text)

    def test_python_free_install_ships_ready_agents(self):
        """The Claude plugin and a copied Codex install need no generation step."""
        sys.path.insert(0, str(SKILL / "scripts"))
        from lemmings.agents import load_agents
        shipped = load_agents({})
        for name, agent in shipped.items():
            suffix = ".md" if agent["host"] == "claude" else ".toml"
            path = ROOT / "agents" / f"lemmings-{name}{suffix}"
            self.assertTrue(path.is_file(), path)
            self.assertIn(agent["model"], path.read_text(encoding="utf-8"))
        for manifest in (ROOT / ".claude-plugin" / "plugin.json", ROOT / ".codex-plugin" / "plugin.json"):
            text = manifest.read_text(encoding="utf-8")
            self.assertNotIn("hooks", text)
            self.assertNotIn("python", text.lower())
        for path in (ROOT / "agents").iterdir():
            self.assertNotIn("run.py", path.read_text(encoding="utf-8"))

    def test_no_hooks_are_shipped(self):
        self.assertFalse((ROOT / "hooks").exists())

    def test_helper_cli_runs_from_bundle(self):
        repo = self.make_repo()
        process = subprocess.run([sys.executable, str(SKILL / "scripts" / "run.py"), "doctor", "--repo", str(repo)],
                                 capture_output=True, text=True, encoding="utf-8")
        result = json.loads(process.stdout)
        self.assertEqual("6.5.0", result["version"])
        self.assertEqual({"worker", "reviewer", "explorer"}, {row["role"] for row in result["agents"]})
