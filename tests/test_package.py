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

    def test_no_hooks_are_shipped(self):
        self.assertFalse((ROOT / "hooks").exists())

    def test_helper_cli_runs_from_bundle(self):
        repo = self.make_repo()
        process = subprocess.run([sys.executable, str(SKILL / "scripts" / "run.py"), "doctor", "--repo", str(repo)],
                                 capture_output=True, text=True, encoding="utf-8")
        result = json.loads(process.stdout)
        self.assertEqual("6.5.0", result["version"])
        self.assertEqual({"worker", "reviewer", "explorer"}, {row["role"] for row in result["agents"]})
