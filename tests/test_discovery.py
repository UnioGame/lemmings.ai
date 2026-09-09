from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/lemmings/scripts"))

from lemmings.discovery import probe_route, scan_providers


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.repo = self.root / "repo"
        self.home.mkdir()
        (self.repo / ".agents").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_codex_and_opencode_metadata_are_sanitized(self) -> None:
        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "config.toml").write_text(
            '''model = "gpt-6"
model_provider = "openai"
wire_api = "responses"

[profiles.fast]
model = "gpt-6-mini"
model_provider = "openai"
wire_api = "responses"
''',
            encoding="utf-8",
        )
        (codex / "auth.json").write_text('{"openai":{"accessToken":"SECRET_CODEX"}}', encoding="utf-8")
        opencode = self.home / ".config" / "opencode"
        opencode.mkdir(parents=True)
        (opencode / "opencode.jsonc").write_text(
            '{\n // comments are accepted\n "provider": {\n'
            '   "anthropic": {"models": {"claude": {"api": "messages", "apiKey": "SECRET_OPEN"}}}\n'
            ' },\n "model": "anthropic/claude",\n}',
            encoding="utf-8",
        )
        (opencode / "auth.json").write_text('{"anthropic":{"key":"SECRET_AUTH"}}', encoding="utf-8")

        snapshot = scan_providers(self.repo, offline=True, home=self.home)
        self.assertEqual(4, snapshot["schemaVersion"])
        self.assertEqual({"openai", "anthropic"}, {item["providerId"] for item in snapshot["providers"]})
        self.assertTrue(any(item.get("profileName") == "fast" for item in snapshot["routes"]))
        self.assertTrue(any(item["protocol"] == "messages" and item["executor"] == "opencode" for item in snapshot["routes"]))
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn("SECRET", encoded)
        for route in snapshot["routes"]:
            allowed = {
                "hostId", "providerId", "modelId", "executor", "protocol", "configured",
                "catalogued", "compatible", "authConfigured", "probed", "source",
                "profileName", "variantId", "quotaGroup",
            }
            self.assertTrue(set(route) <= allowed)

    def test_catalog_protocol_and_alias_diagnostics_do_not_rewrite_routes(self) -> None:
        snapshot = scan_providers(
            self.repo,
            offline=True,
            home=self.home,
            host_catalog={
                "opencode-go": {
                    "models": [
                        {"id": "opencode_go/gpt-go", "protocol": "chat-completions"},
                        {"providerId": "openai", "modelId": "mystery", "protocol": "future-api"},
                    ]
                }
            },
        )
        routes = {(item["providerId"], item["modelId"]): item for item in snapshot["routes"]}
        self.assertEqual("opencode", routes[("opencode_go", "gpt-go")]["executor"])
        self.assertEqual("unknown", routes[("openai", "mystery")]["protocol"])
        self.assertFalse(routes[("openai", "mystery")]["compatible"])
        self.assertTrue(any(item["code"] == "provider-alias-mismatch" for item in snapshot["diagnostics"]))
        self.assertNotIn("opencode-go", {item["providerId"] for item in snapshot["providers"]})

    def test_offline_scan_preserves_sanitized_stale_inventory(self) -> None:
        state_dir = self.home / ".lemmings"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(json.dumps({
            "schemaVersion": 4,
            "inventory": {
                "providers": [{"providerId": "old", "source": "host-catalog", "authConfigured": True, "catalogStatus": "current"}],
                "routes": [{
                    "hostId": "opencode-go", "providerId": "old", "modelId": "model",
                    "executor": "opencode", "protocol": "messages", "configured": True,
                    "catalogued": True, "compatible": True, "authConfigured": True,
                    "probed": True, "source": "host-catalog",
                }],
            },
        }), encoding="utf-8")
        snapshot = scan_providers(self.repo, offline=True, home=self.home)
        self.assertTrue(any(item["providerId"] == "old" for item in snapshot["providers"]))
        route = next(item for item in snapshot["routes"] if item["providerId"] == "old")
        self.assertEqual("state-inventory", route["source"])
        self.assertFalse(route["probed"])
        self.assertTrue(any(item["code"] == "catalog-stale" for item in snapshot["diagnostics"]))

    def test_probe_is_explicit_and_does_not_send_a_prompt(self) -> None:
        route = {
            "hostId": "opencode",
            "providerId": "openai",
            "modelId": "gpt",
            "protocol": "responses",
        }
        result = probe_route(route, home=self.home)
        self.assertTrue(result["probed"])
        self.assertEqual("metadata-only", result["status"])
        self.assertIsNone(result["reachable"])

        class UnexpectedNetwork:
            def __call__(self, *args, **kwargs):
                raise AssertionError("scan must not perform implicit network probes")

        with patch("lemmings.discovery.urllib.request.urlopen", new=UnexpectedNetwork()):
            snapshot = scan_providers(self.repo, offline=False, home=self.home)
        self.assertIsInstance(snapshot["routes"], list)


if __name__ == "__main__":
    unittest.main()
