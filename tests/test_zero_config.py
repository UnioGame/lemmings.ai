import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))
from lemmings.cli import load_profile, main
from lemmings.contracts import validate_profile

class ZeroConfigTests(unittest.TestCase):
    def test_defaults_and_doctor_without_engine(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(validate_profile(load_profile(Path(d))).ok)
            self.assertEqual({}, load_profile(Path(d))["modelRoutes"])
            self.assertEqual(0, main(["doctor", "--repo", d]))

    def test_generic_install_preserves_pins_and_rejects_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            subprocess.run(["git", "init", "-q", d], check=True)
            installer = ROOT / "skills/lemmings/scripts/install.py"
            def run():
                return subprocess.run([sys.executable, "-B", str(installer), "--repo", d], capture_output=True, text=True)
            first = run()
            self.assertEqual(0, first.returncode, first.stderr + first.stdout)
            p = repo / ".agents/lemmings.json"
            settings = json.loads(p.read_text())
            settings["custom"] = {"keep": True}
            settings["modelRoutes"] = {"codex":{role:[{"providerId":"test","modelId":"manual"}] for role in ("worker","reviewer","explorer")}}
            p.write_text(json.dumps(settings))
            a = repo / ".codex/agents/lemmings-worker.toml"
            a.write_text(a.read_text() + '\nmodel = "manual"\nmodel_reasoning_effort = "high"\n')
            second = run()
            self.assertEqual(0, second.returncode, second.stderr + second.stdout)
            self.assertEqual(settings, json.loads(p.read_text()))
            self.assertIn('model = "manual"', a.read_text())
            p.write_text('{"schemaVersion":2}')
            self.assertNotEqual(0, run().returncode)
            self.assertEqual('{"schemaVersion":2}', p.read_text())

    def test_busy_invocation(self):
        spec = importlib.util.spec_from_file_location("installer", ROOT / "skills/lemmings/scripts/install.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d:
            common=Path(d); (common / "lemmings").mkdir()
            (common / "lemmings/workspaces-v4.json").write_text(json.dumps({"entries":[{"state":"idle","activeInvocationId":"running"}]}))
            self.assertIsNotNone(module.installation_busy(common))
