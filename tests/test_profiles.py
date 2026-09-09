from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/lemmings/scripts"))

from lemmings.profiles import (
    apply_profile_proposal,
    build_profile_proposal,
    inspect_profiles,
    resolve_profile,
    use_profile,
)


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.repo = self.root / "repo"
        self.home.mkdir()
        (self.repo / ".agents").mkdir(parents=True)
        (self.repo / ".agents" / "lemmings.json").write_text(json.dumps({
            "schemaVersion": 4,
            "modelRoutes": {
                "codex": {
                    "worker": [{"providerId": "manual", "modelId": "worker"}],
                    "reviewer": [],
                    "explorer": [],
                }
            },
        }), encoding="utf-8")
        (self.home / ".lemmings").mkdir()
        (self.home / ".lemmings" / "profiles.json").write_text(json.dumps({
            "activeProfile": "personal",
            "profiles": {
                "personal": {"roleRoutes": {
                    "worker": [{"hostId": "opencode", "providerId": "personal", "modelId": "worker"}],
                    "reviewer": [{"hostId": "opencode", "providerId": "personal", "modelId": "reviewer"}],
                    "explorer": [],
                }}
            },
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_precedence_is_per_role_and_empty_manual_routes_are_unpinned(self) -> None:
        proposal = build_profile_proposal(self.repo, "generated", {
            "worker": [{"hostId": "codex", "providerId": "generated", "modelId": "worker"}],
            "reviewer": [{"hostId": "codex", "providerId": "generated", "modelId": "reviewer"}],
            "explorer": [{"hostId": "codex", "providerId": "generated", "modelId": "explorer"}],
        }, home=self.home)
        apply_profile_proposal(self.repo, proposal, proposal["proposalDigest"], home=self.home)
        resolved = resolve_profile(self.repo, "generated", home=self.home)
        self.assertEqual("manual", resolved["roleRoutes"]["worker"][0]["providerId"])
        self.assertEqual("personal", resolved["roleRoutes"]["reviewer"][0]["providerId"])
        self.assertEqual("generated", resolved["roleRoutes"]["explorer"][0]["providerId"])
        self.assertEqual("project-manual", resolved["sources"]["worker"])
        self.assertEqual("personal-manual", resolved["sources"]["reviewer"])
        self.assertEqual("generated:generated", resolved["sources"]["explorer"])

    def test_apply_rejects_changed_manual_source_and_use_only_selects(self) -> None:
        proposal = build_profile_proposal(self.repo, "generated", {
            "worker": [], "reviewer": [], "explorer": [],
        }, home=self.home)
        project_file = self.repo / ".agents" / "lemmings.json"
        project_file.write_text(project_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manual profile inputs changed"):
            apply_profile_proposal(self.repo, proposal, proposal["proposalDigest"], home=self.home)

        proposal = build_profile_proposal(self.repo, "generated", {
            "worker": [], "reviewer": [], "explorer": [],
        }, home=self.home)
        apply_profile_proposal(self.repo, proposal, proposal["proposalDigest"], home=self.home)
        before = json.loads((self.home / ".lemmings" / "state.json").read_text(encoding="utf-8"))
        result = use_profile(self.repo, "generated", home=self.home)
        self.assertEqual("generated", result["selection"])
        after = json.loads((self.home / ".lemmings" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(before["profiles"], after["profiles"])
        self.assertEqual("generated", after["selections"][str(self.repo.resolve()).replace("\\", "/").casefold()])

    def test_inspect_is_read_only_and_reports_origins(self) -> None:
        before = (self.home / ".lemmings" / "profiles.json").read_bytes()
        result = inspect_profiles(self.repo, home=self.home)
        self.assertIn("personal", result["available"])
        self.assertIn("personal-manual", result["origins"]["personal"])
        self.assertEqual("personal", result["selection"])
        self.assertEqual(before, (self.home / ".lemmings" / "profiles.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
