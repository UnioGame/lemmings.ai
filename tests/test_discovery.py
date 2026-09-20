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
        self.assertEqual(5, snapshot["schemaVersion"])
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
            "schemaVersion": 5,
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
            "provider": {"opencode_go": {
                "protocol": "chat-completions",
                "baseUrl": "https://opencode.ai/zen/go/v1",
                "apiKey": "SECRET_GO",
            }},
        }), encoding="utf-8")
        cache = self.home / ".cache" / "opencode"
        cache.mkdir(parents=True)
        (cache / "models.json").write_text(json.dumps({"opencode-go": {"api": "https://opencode.ai/zen/go/v1", "models": {
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
        headers = {key.casefold(): value for key, value in request.header_items()}
        self.assertEqual("lemmings-discovery/4", headers["user-agent"])
        self.assertEqual("Bearer SECRET_GO", headers["authorization"])
        self.assertTrue(headers["x-opencode-session"])
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
            "provider": {"opencode_go": {
                "protocol": "chat-completions",
                "baseUrl": "https://opencode.ai/zen/go/v1",
            }},
        }), encoding="utf-8")
        state = self.home / ".lemmings"
        state.mkdir()
        (state / "state.json").write_text(json.dumps({"inventory": {
            "providers": [{"providerId": "old", "source": "host-catalog", "authConfigured": True, "catalogStatus": "current"}],
            "routes": [{"hostId": "opencode-go", "providerId": "old", "modelId": "old-model", "executor": "opencode", "protocol": "chat-completions", "configured": True, "catalogued": True, "compatible": True, "authConfigured": True, "probed": True, "source": "host-catalog"}],
        }}), encoding="utf-8")
        with patch("lemmings.discovery.urllib.request.urlopen", side_effect=OSError("network body must stay private")) as opened:
            snapshot = scan_providers(self.repo, offline=False, home=self.home)
        headers = {key.casefold(): value for key, value in opened.call_args.args[0].header_items()}
        self.assertEqual("lemmings-discovery/4", headers["user-agent"])
        self.assertTrue(headers["x-opencode-session"])
        self.assertNotIn("authorization", headers)
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
                payload = json.loads(request.data)
                self.assertTrue(payload.get("input") or payload.get("messages"))
                headers = {key.casefold(): value for key, value in request.header_items()}
                self.assertIn("x-api-key" if route["protocol"] == "messages" else "authorization", headers)
                suffix = {"responses": "/responses", "chat-completions": "/chat/completions", "messages": "/messages"}[route["protocol"]]
                self.assertTrue(request.full_url.endswith(suffix))
            self.assertTrue(probe_route(other, repo=other_repo, home=self.home)["probed"])
            self.assertIn(":9012/", opened.call_args.args[0].full_url)
        foreign = {**routes["responses"], "protocol": "chat-completions"}
        self.assertEqual("unsupported", probe_route(foreign, repo=self.repo, home=self.home)["status"])

    def test_custom_go_name_keeps_custom_endpoint_protocol_and_credentials(self) -> None:
        (self.repo / "opencode.json").write_text(json.dumps({"provider": {"opencode-go": {
            "models": {"gpt-5.6-luna": {
                "api": "messages",
                "baseUrl": "https://custom.example/v1",
                "apiKey": "CUSTOM_SECRET",
            }},
        }}}), encoding="utf-8")
        cache = self.home / ".cache" / "opencode"
        cache.mkdir(parents=True)
        (cache / "models.json").write_text(json.dumps({"opencode-go": {"api": "https://opencode.ai/zen/go/v1", "models": {"gpt-5.6-luna": {}}}}), encoding="utf-8")
        snapshot = scan_providers(self.repo, offline=True, home=self.home)
        route = next(item for item in snapshot["routes"] if item["hostId"] == "opencode")
        self.assertEqual("messages", route["protocol"])
        self.assertTrue(any(item["hostId"] == "opencode-go" and item["protocol"] == "unknown" and not item["compatible"] for item in snapshot["routes"]))

        class Response:
            status = 200
            def read(self, size=-1):
                return b'{"model":"gpt-5.6-luna"}'
            def close(self):
                pass

        with patch("lemmings.discovery.urllib.request.urlopen", return_value=Response()) as opened:
            self.assertTrue(probe_route(route, repo=self.repo, home=self.home)["probed"])
        request = opened.call_args.args[0]
        self.assertEqual("https://custom.example/v1/messages", request.full_url)
        self.assertNotIn("x-opencode-session", {key.casefold() for key, _ in request.header_items()})

    def test_repo_custom_endpoint_does_not_borrow_personal_provider_secret(self) -> None:
        personal = self.home / ".config" / "opencode"
        personal.mkdir(parents=True)
        (personal / "opencode.json").write_text(json.dumps({"provider": {"openai": {"apiKey": "HOME_SECRET"}}}), encoding="utf-8")
        (self.repo / "opencode.json").write_text(json.dumps({"model": "openai/gpt", "provider": {
            "openai": {"api": "responses", "baseUrl": "http://127.0.0.1:9013/v1"},
        }}), encoding="utf-8")
        route = next(item for item in scan_providers(self.repo, offline=True, home=self.home)["routes"] if item["modelId"] == "gpt")
        with patch("lemmings.discovery.urllib.request.urlopen", side_effect=AssertionError("must not send home secret")):
            result = probe_route(route, repo=self.repo, home=self.home)
        self.assertEqual("unsupported", result["status"])
        self.assertTrue(any(item["code"] == "probe-auth-required" for item in result["diagnostics"]))
        self.assertNotIn("HOME_SECRET", json.dumps(result))


    def test_codex_cache_and_standalone_profiles_preserve_identity(self) -> None:
        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "config.toml").write_text('model="m"\nmodel_provider="custom"\n[model_providers.custom]\nbase_url="https://base.example/v1"\nwire_api="responses"\n', encoding="utf-8")
        for name in ("one", "two"):
            (codex / f"{name}.config.toml").write_text(f'model="m"\nmodel_provider="custom"\n[model_providers.custom]\nbase_url="https://{name}.example/v1"\nwire_api="responses"\nexperimental_bearer_token="PROFILE_SECRET"\n', encoding="utf-8")
        (codex / "auth.json").write_text(json.dumps({"tokens": {"access_token": "PRIVATE_TOKEN"}}), encoding="utf-8")
        (codex / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "visible", "visibility": "list", "supported_in_api": False, "supported_reasoning_levels": [{"effort": "high"}, {"effort": "max"}], "model_messages": "PRIVATE_INSTRUCTIONS"},
            {"slug": "hidden", "visibility": "hide"},
        ]}), encoding="utf-8")
        snapshot = scan_providers(self.repo, offline=True, home=self.home)
        self.assertEqual({"one", "two"}, {r.get("profileName") for r in snapshot["routes"] if r.get("profileName")})
        visible = [r for r in snapshot["routes"] if r["modelId"] == "visible"]
        self.assertEqual({"high", "max"}, {r["variantId"] for r in visible if r.get("variantId")})
        self.assertTrue(all(r["authConfigured"] for r in visible))
        self.assertNotIn("hidden", {r["modelId"] for r in snapshot["routes"]})
        self.assertNotIn("tokens", {p["providerId"] for p in snapshot["providers"]})
        self.assertNotIn("PRIVATE", json.dumps(snapshot))
        from lemmings.discovery import _trusted_bundle
        for route in snapshot["routes"]:
            if route.get("profileName"):
                self.assertTrue(route["authConfigured"])
                endpoint, secret = _trusted_bundle(self.repo, self.home, route)
                self.assertEqual(f'https://{route["profileName"]}.example/v1', endpoint)
                self.assertEqual("PROFILE_SECRET", secret)

    def test_custom_catalog_endpoint_does_not_prove_go_protocol(self) -> None:
        cache = self.home / ".cache" / "opencode"
        cache.mkdir(parents=True)
        (cache / "models.json").write_text(json.dumps({"opencode-go": {"api": "https://custom.example/v1", "models": {"gpt-5.6-luna": {}}}}), encoding="utf-8")
        route = scan_providers(self.repo, offline=True, home=self.home)["routes"][0]
        self.assertEqual("unknown", route["protocol"])
        self.assertFalse(route["compatible"])

    def test_codex_go_metadata_and_repeat_proposal_are_stable(self) -> None:
        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "config.toml").write_text('model="gpt-5.6-luna"\nmodel_provider="opencode_go"\n[model_providers.opencode_go]\nbase_url="https://opencode.ai/zen/go/v1"\nwire_api="responses"\napi_key="CATALOG_SECRET"\n', encoding="utf-8")
        class Response:
            def read(self, size=-1):
                return b'{"data":[{"id":"gpt-5.6-luna","object":"model","owned_by":"opencode"}]}'
            def close(self):
                pass
        from lemmings.profiles import build_profile_proposal
        routes = {"worker": [{"hostId": "opencode-go", "providerId": "opencode-go", "modelId": "gpt-5.6-luna"}]}
        with patch("lemmings.discovery.urllib.request.urlopen", return_value=Response()) as opened:
            first = build_profile_proposal(self.repo, "go", routes, home=self.home)
            second = build_profile_proposal(self.repo, "go", routes, home=self.home)
        self.assertEqual(first["inventoryDigest"], second["inventoryDigest"])
        headers = {key.casefold(): value for key, value in opened.call_args.args[0].header_items()}
        self.assertEqual("Bearer CATALOG_SECRET", headers["authorization"])
        self.assertNotIn("CATALOG_SECRET", json.dumps(first))

    def test_large_json_cache_uses_fast_parser_and_keeps_provider_identity(self) -> None:
        cache = self.home / ".cache" / "opencode"
        cache.mkdir(parents=True)
        models = {f"vendor/model-{i}": {"description": "x" * 1000} for i in range(4500)}
        (cache / "models.json").write_text(json.dumps({"router": {"api": "https://router.example/v1", "models": models}}), encoding="utf-8")
        with patch("lemmings.discovery._jsonc", side_effect=AssertionError("ordinary JSON must use native parser")):
            snapshot = scan_providers(self.repo, offline=True, home=self.home)
        self.assertEqual(4500, len(snapshot["routes"]))
        self.assertEqual({"router"}, {r["providerId"] for r in snapshot["routes"]})
        self.assertTrue(all(r["modelId"].startswith("vendor/") for r in snapshot["routes"]))
        self.assertEqual({"opencode"}, {r["hostId"] for r in snapshot["routes"]})

    def test_shared_cache_does_not_add_unconnected_subscriptions(self) -> None:
        (self.repo / "opencode.json").write_text(json.dumps({"model": "connected/vendor/local"}), encoding="utf-8")
        cache = self.home / ".cache" / "opencode"
        cache.mkdir(parents=True)
        (cache / "models.json").write_text(json.dumps({provider: {"models": {"vendor/model": {}}} for provider in ("connected", "unconnected")}), encoding="utf-8")
        snapshot = scan_providers(self.repo, offline=True, home=self.home)
        self.assertEqual({"connected"}, {r["providerId"] for r in snapshot["routes"]})

    def test_jsonc_trailing_commas_preserve_strings_and_comments(self) -> None:
        from lemmings.discovery import _jsonc
        self.assertEqual({"a": [1, 2], "literal": ", } // untouched"}, _jsonc('{ /* comment */ "a": [1,2,], "literal": ", } // untouched", }'))

if __name__ == "__main__":
    unittest.main()
