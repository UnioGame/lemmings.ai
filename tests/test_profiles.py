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
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text(
            """model = "generated/worker"
wire_api = "responses"

[model_providers.generated.models]
worker = {}
reviewer = {}
explorer = {}

[model_providers.bad]
wire_api = "chat-completions"

[model_providers.bad.models]
bad = {}
""",
            encoding="utf-8",
        )
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

    def test_bound_inventory_rejects_invented_and_incompatible_routes(self) -> None:
        with self.assertRaisesRegex(ValueError, "absent from bound inventory"):
            build_profile_proposal(self.repo, "invented", {
                "worker": [{"hostId": "codex", "providerId": "missing", "modelId": "missing"}],
                "reviewer": [], "explorer": [],
            }, home=self.home)
        with self.assertRaisesRegex(ValueError, "incompatible"):
            build_profile_proposal(self.repo, "bad", {
                "worker": [{"hostId": "codex", "providerId": "bad", "modelId": "bad"}],
                "reviewer": [], "explorer": [],
            }, home=self.home)

    def test_project_active_profile_has_manual_priority(self) -> None:
        project_path = self.repo / ".agents" / "lemmings.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["modelRoutes"]["codex"]["worker"] = []
        project["activeProfile"] = "project"
        project["profiles"] = {"project": {"roleRoutes": {
            "worker": [{"hostId": "codex", "providerId": "project", "modelId": "worker"}],
            "reviewer": [], "explorer": [],
        }}}
        project_path.write_text(json.dumps(project), encoding="utf-8")
        resolved = resolve_profile(self.repo, "generated", home=self.home)
        self.assertEqual("project", resolved["roleRoutes"]["worker"][0]["providerId"])
        self.assertEqual("project-manual", resolved["sources"]["worker"])

    def test_state_lock_precedes_digest_validation(self) -> None:
        state_path = self.home / ".lemmings" / "state.json"
        lock = state_path.with_suffix(state_path.suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("busy", encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "locked"):
                use_profile(self.repo, "unknown", home=self.home)
        finally:
            lock.unlink(missing_ok=True)

    def test_inspect_is_read_only_and_reports_origins(self) -> None:
        before = (self.home / ".lemmings" / "profiles.json").read_bytes()
        result = inspect_profiles(self.repo, home=self.home)
        self.assertIn("personal", result["available"])
        self.assertIn("personal-manual", result["origins"]["personal"])
        self.assertEqual("personal", result["selection"])
        self.assertEqual(before, (self.home / ".lemmings" / "profiles.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
