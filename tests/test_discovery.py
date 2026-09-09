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

    def test_online_go_catalog_is_bounded_and_offline_scan_does_not_network(self) -> None:
        config = self.home / ".config" / "opencode"
        config.mkdir(parents=True)
        (config / "opencode.json").write_text(json.dumps({
            "model": "opencode_go/local",
            "provider": {"opencode_go": {"protocol": "chat-completions"}},
        }), encoding="utf-8")
        cache = self.home / ".cache" / "opencode"
        cache.mkdir(parents=True)
        (cache / "models.json").write_text(json.dumps({"opencode-go": {"models": {
            "glm-5.3": {}, "new-go-model": {},
        }}}), encoding="utf-8")

        class Response:
            status = 200
            def read(self, size=-1):
                return json.dumps({"data": [{"id": "gpt-5.6-luna", "object": "model", "owned_by": "opencode"}]}).encode()
            def close(self):
                pass

        with patch("lemmings.discovery.urllib.request.urlopen", return_value=Response()) as opened:
            snapshot = scan_providers(self.repo, offline=False, home=self.home)
        request = opened.call_args.args[0]
        self.assertEqual("https://opencode.ai/zen/go/v1/models", request.full_url)
        self.assertEqual("GET", request.method)
        routes = {item["modelId"]: item for item in snapshot["routes"] if item["hostId"] == "opencode-go"}
        self.assertEqual("responses", routes["gpt-5.6-luna"]["protocol"])
        self.assertTrue(routes["gpt-5.6-luna"]["compatible"])
        self.assertEqual("chat-completions", routes["glm-5.3"]["protocol"])
        self.assertEqual("unknown", routes["new-go-model"]["protocol"])

        class UnexpectedNetwork:
            def __call__(self, *args, **kwargs):
                raise AssertionError("offline scan must not perform network calls")
        with patch("lemmings.discovery.urllib.request.urlopen", new=UnexpectedNetwork()):
            offline = scan_providers(self.repo, offline=True, home=self.home)
        self.assertIsInstance(offline["routes"], list)

    def test_scan_honors_state_lock_before_provider_reads(self) -> None:
        lock = self.home / ".lemmings" / "state.json.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("busy", encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "locked"):
                scan_providers(self.repo, offline=True, home=self.home)
        finally:
            lock.unlink(missing_ok=True)

    def test_catalog_failure_preserves_stale_inventory(self) -> None:
        config = self.home / ".config" / "opencode"
        config.mkdir(parents=True)
        (config / "opencode.json").write_text(json.dumps({
            "model": "opencode_go/local",
            "provider": {"opencode_go": {"protocol": "chat-completions"}},
        }), encoding="utf-8")
        state = self.home / ".lemmings"
        state.mkdir()
        (state / "state.json").write_text(json.dumps({"inventory": {
            "providers": [{"providerId": "old", "source": "host-catalog", "authConfigured": True, "catalogStatus": "current"}],
            "routes": [{"hostId": "opencode-go", "providerId": "old", "modelId": "old-model", "executor": "opencode", "protocol": "chat-completions", "configured": True, "catalogued": True, "compatible": True, "authConfigured": True, "probed": True, "source": "host-catalog"}],
        }}), encoding="utf-8")
        with patch("lemmings.discovery.urllib.request.urlopen", side_effect=OSError("network body must stay private")):
            snapshot = scan_providers(self.repo, offline=False, home=self.home)
        self.assertTrue(any(item["providerId"] == "old" for item in snapshot["routes"]))
        self.assertTrue(any(item["code"] == "go-catalog-unavailable" for item in snapshot["diagnostics"]))
        self.assertNotIn("network body", json.dumps(snapshot))

    def test_targeted_probe_requires_auth_and_confirms_exact_model(self) -> None:
        config = self.home / ".config" / "opencode"
        config.mkdir(parents=True)
        (config / "opencode.json").write_text(json.dumps({"model": "openai/gpt", "provider": {
            "openai": {"api": "responses", "apiKey": "SECRET_PROBE", "baseUrl": "http://127.0.0.1:9/v1"},
        }}), encoding="utf-8")
        route = next(item for item in scan_providers(self.repo, offline=True, home=self.home)["routes"] if item["modelId"] == "gpt")

        class Response:
            status = 200
            def read(self, size=-1):
                return b'{"model":"gpt"}'
            def close(self):
                pass

        with patch("lemmings.discovery.urllib.request.urlopen", return_value=Response()) as opened:
            result = probe_route(route, repo=self.repo, home=self.home)
        request = opened.call_args.args[0]
        self.assertEqual("POST", request.method)
        self.assertEqual("http://127.0.0.1:9/v1/responses", request.full_url)
        self.assertIn(b'"model": "gpt"', request.data)
        self.assertNotIn("SECRET_PROBE", json.dumps(result))
        self.assertTrue(result["probed"])
        tampered = {**route, "endpoint": "http://foreign.invalid/v1"}
        self.assertEqual("unsupported", probe_route(tampered, repo=self.repo, home=self.home)["status"])

    def test_probe_uses_selected_repo_and_each_protocol(self) -> None:
        other_repo = self.root / "other-repo"
        (other_repo / ".agents").mkdir(parents=True)

        def write_config(repo, port, models):
            (repo / "opencode.json").write_text(json.dumps({"provider": {"openai": {
                "models": {model: {"api": protocol, "apiKey": "SECRET", "baseUrl": f"http://127.0.0.1:{port}/v1"}
                           for model, protocol in models.items()},
            }}}), encoding="utf-8")

        write_config(self.repo, 9011, {"responses": "responses", "chat": "chat-completions", "messages": "messages"})
        write_config(other_repo, 9012, {"responses": "responses"})

        class Response:
            status = 200
            def __init__(self, model):
                self.model = model
            def read(self, size=-1):
                return json.dumps({"model": self.model}).encode()
            def close(self):
                pass

        def answer(request, timeout):
            return Response(json.loads(request.data.decode())["model"])

        routes = {item["modelId"]: item for item in scan_providers(self.repo, offline=True, home=self.home)["routes"]}
        other = next(item for item in scan_providers(other_repo, offline=True, home=self.home)["routes"] if item["modelId"] == "responses")
        with patch("lemmings.discovery.urllib.request.urlopen", side_effect=answer) as opened:
            for model, route in routes.items():
                self.assertTrue(probe_route(route, repo=self.repo, home=self.home)["probed"])
                request = opened.call_args.args[0]
                self.assertIn(model.encode(), request.data)
                suffix = {"responses": "/responses", "chat-completions": "/chat/completions", "messages": "/messages"}[route["protocol"]]
                self.assertTrue(request.full_url.endswith(suffix))
            self.assertTrue(probe_route(other, repo=other_repo, home=self.home)["probed"])
            self.assertIn(":9012/", opened.call_args.args[0].full_url)
        foreign = {**routes["responses"], "protocol": "chat-completions"}
        self.assertEqual("unsupported", probe_route(foreign, repo=self.repo, home=self.home)["status"])


if __name__ == "__main__":
    unittest.main()
