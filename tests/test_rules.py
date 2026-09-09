import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))
from lemmings.rules import resolve_rules

class RuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.repo = Path(self.temp.name)
        target = self.repo / ".agents/skills/lemmings"; target.mkdir(parents=True)
        for name in ("defaults.json", "SKILL.md"):
            shutil.copyfile(ROOT / "skills/lemmings" / name, target / name)
        shutil.copytree(ROOT / "skills/lemmings/rules", target / "rules")
    def tearDown(self):
        self.temp.cleanup()
    def put(self, name, text=""):
        path = self.repo / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text, encoding="utf-8"); return path
    def ids(self, result):
        return {Path(r["ref"]).stem for r in result["ruleRefs"]} - {"manifest"}
    def test_seven_engines_are_scoped_to_the_nearest_project(self):
        self.put("unity/ProjectSettings/ProjectVersion.txt", "m_EditorVersion: 6000.0.1f1"); self.put("unity/Assets/a.cs")
        self.put("unreal/Game.uproject", '{"EngineAssociation":"5.4"}'); self.put("unreal/Source/a.cpp")
        self.put("godot/project.godot", "config_version=5"); self.put("godot/src/a.gd")
        self.put("defold/game.project", "[project]"); self.put("defold/main/a.script")
        self.put("flutter/pubspec.yaml", "name: app\ndependencies:\n  flutter:\n    sdk: flutter\n"); self.put("flutter/lib/a.dart")
        for tech, dep in (("phaser", "phaser"), ("pixijs", "pixi.js")):
            self.put(tech + "/package.json", json.dumps({"dependencies": {dep: "^8.0.0"}})); self.put(tech + "/src/a.ts")
        for tech, file in {"unity":"Assets/a.cs", "unreal":"Source/a.cpp", "godot":"src/a.gd", "defold":"main/a.script", "flutter":"lib/a.dart", "phaser":"src/a.ts", "pixijs":"src/a.ts"}.items():
            with self.subTest(tech=tech):
                result = resolve_rules(self.repo, paths=[tech + "/" + file])
                self.assertEqual({tech}, self.ids(result)); self.assertEqual(tech, result["projects"][0]["root"])
                self.assertTrue(all((self.repo/r["ref"]).is_file() for r in result["ruleRefs"]))
    def test_direct_import_and_cdn_but_not_transitive_lock(self):
        self.put("package.json", '{"dependencies":{"unrelated":"1"}}'); self.put("package-lock.json", '{"pixi.js":"8"}')
        self.put("src/plain.ts", "const text = 'pixi.js';")
        self.assertFalse(self.ids(resolve_rules(self.repo, paths=["src/plain.ts"])))
        self.put("src/view.ts", "import { Application } from 'pixi.js';")
        result = resolve_rules(self.repo, paths=["src/view.ts"])
        self.assertEqual({"pixijs"}, self.ids(result))
        self.assertEqual(".", result["projects"][0]["root"])
        self.put("web/index.html", '<script src="https://cdn.jsdelivr.net/npm/phaser@3/dist/phaser.js"></script>')
        self.assertEqual({"phaser"}, self.ids(resolve_rules(self.repo, paths=["web/index.html"])))
    def test_generated_paths_and_sibling_projects_are_not_read(self):
        self.put("node_modules/game/package.json", '{"dependencies":{"phaser":"3"}}')
        self.put("neighbor/project.godot", "config_version=5"); self.put("app/package.json", '{}'); self.put("app/main.ts")
        self.assertFalse(resolve_rules(self.repo, paths=["app/main.ts", "node_modules/game/package.json"])["ruleRefs"])
    def test_explicit_overrides_platforms_hashes_and_empty_override(self):
        self.put("project.godot", "config_version=5")
        result = resolve_rules(self.repo, technologies=["pixi.js"], platforms=["web"])
        self.assertEqual({"pixijs", "platforms"}, self.ids(result))
        self.assertEqual(result, resolve_rules(self.repo, technologies=["pixi.js"], platforms=["web"]))
        pack = self.repo / next(r["ref"] for r in result["ruleRefs"] if r["ref"].endswith("pixijs.md"))
        pack.write_text(pack.read_text() + "\nChanged rule.\n")
        self.assertNotEqual(result["digest"], resolve_rules(self.repo, technologies=["pixi.js"], platforms=["web"])["digest"])
        self.assertFalse(resolve_rules(self.repo, technologies=[])["ruleRefs"])
        with self.assertRaises(ValueError): resolve_rules(self.repo, platforms=["invented"])
        with self.assertRaises(ValueError): resolve_rules(self.repo, paths=["../outside"])
    def test_generic_and_upm_compatibility_are_not_unity_projects(self):
        self.put("package.json", '{"name":"a-package", "unity":"2022.3"}')
        self.assertEqual([], resolve_rules(self.repo)["ruleRefs"])
        self.put("pubspec.yaml", "description: Flutter-like example\ndependencies:\n  dart_package: any\n")
        self.assertEqual([], resolve_rules(self.repo)["ruleRefs"])
    def test_default_root_search_finds_nested_project_but_stays_bounded(self):
        self.put("GameClient/ProjectSettings/ProjectVersion.txt", "m_EditorVersion: 2022.3.0f1"); self.put("GameClient/Assets/a.cs")
        self.assertEqual({"unity"}, self.ids(resolve_rules(self.repo)))
    def test_pack_content_and_manifest_are_complete(self):
        packs = ROOT / "skills/lemmings/rules"
        manifest = json.loads((packs/"manifest.json").read_text())
        self.assertEqual(8, len(manifest["packs"]))
        for name, record in manifest["packs"].items():
            text = (packs/record["file"]).read_text()
            self.assertGreater(len(text), 800)
            if name != "platforms":
                for role in ("Manager:", "Worker:", "Reviewer:"):
                    self.assertIn(role, text)
                self.assertIn("Exclude", text)

if __name__ == "__main__": unittest.main()
