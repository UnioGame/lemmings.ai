import json
import os
import sys
from pathlib import Path

from support import HermeticTest

from lemmings import dispatch
from lemmings.gitutil import HelperError


class DispatchTests(HermeticTest):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        script = Path(__file__).with_name("fakehost.py")
        for host in ("codex", "claude", "opencode"):
            if os.name == "nt":
                line = '@"' + sys.executable + '" "' + str(script) + '" ' + host + " %*\r\n"
                (self.bin / f"{host}.cmd").write_text(line, encoding="utf-8")
            else:
                path = self.bin / host
                path.write_text('#!/bin/sh\nexec "' + sys.executable + '" "' + str(script) + '" ' + host + ' "$@"\n',
                                encoding="utf-8")
                path.chmod(0o755)
        os.environ["PATH"] = str(self.bin) + os.pathsep + os.environ["PATH"]
        os.environ["FAKE_LOG"] = str(self.tmp / "calls.jsonl")

    def configure(self, roles):
        (self.repo / ".agents").mkdir(exist_ok=True)
        (self.repo / ".agents" / "lemmings.json").write_text(json.dumps({"roles": roles}), encoding="utf-8")

    def calls(self):
        path = self.tmp / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_native_role_is_not_dispatched(self):
        result = dispatch.dispatch(self.repo, "worker", "Goal: x")
        self.assertEqual("native", result["status"])
        self.assertEqual([], self.calls())

    def test_claude_reviewer_is_read_only_and_reports_verdict(self):
        self.configure({"reviewer": {"host": "claude", "model": "opus", "effort": "high"}})
        result = dispatch.dispatch(self.repo, "reviewer", "Goal: review the diff")
        self.assertTrue(result["ok"], result)
        self.assertEqual("Accepted", result["verdict"])
        self.assertTrue(result["modelConfirmed"])
        self.assertEqual({"input_tokens": 10, "output_tokens": 5}, result["usage"])
        args = self.calls()[0]["args"]
        self.assertIn("Read,Glob,Grep,Bash", args)
        self.assertIn("Edit", args[args.index("--disallowedTools") + 1])
        self.assertIn("Goal: review the diff", self.calls()[0]["prompt"])
        run_dir = Path(result["runDir"])
        self.assertTrue((run_dir / "result.json").is_file() and (run_dir / "brief.md").is_file())

    def test_codex_worker_uses_workspace_write_and_last_message(self):
        self.configure({"worker": {"host": "codex", "model": "gpt-5.6-terra", "profile": "work"}})
        result = dispatch.dispatch(self.repo, "worker", "Goal: build")
        self.assertTrue(result["ok"], result)
        self.assertEqual("status: done", result["report"])
        args = self.calls()[0]["args"]
        self.assertEqual("workspace-write", args[args.index("--sandbox") + 1])
        self.assertEqual("work", args[args.index("--profile") + 1])
        self.assertEqual(7, result["usage"]["input_tokens"])

    def test_opencode_explorer_concatenates_text(self):
        self.configure({"explorer": {"host": "opencode", "model": "openrouter/deepseek-v4"}})
        result = dispatch.dispatch(self.repo, "explorer", "Question: where?")
        self.assertTrue(result["ok"], result)
        self.assertEqual("status: done", result["report"])
        self.assertTrue(result["modelConfirmed"])

    def test_model_mismatch_is_an_error_without_fallback(self):
        self.configure({"reviewer": {"host": "claude", "model": "opus", "fallback": [{"host": "codex", "model": "sol"}]}})
        os.environ["FAKE_CLAUDE"] = "mismatch"
        result = dispatch.dispatch(self.repo, "reviewer", "Goal: x")
        self.assertFalse(result["ok"])
        self.assertEqual("model-mismatch", result["status"])
        self.assertEqual(["claude"], [call["host"] for call in self.calls()])

    def test_fallback_after_host_failure_is_reported(self):
        self.configure({"reviewer": {"host": "claude", "model": "opus", "fallback": [{"host": "codex", "model": "sol"}]}})
        os.environ["FAKE_CLAUDE"] = "fail"
        result = dispatch.dispatch(self.repo, "reviewer", "Goal: x")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["fallbackUsed"])
        self.assertEqual("codex", result["host"])
        self.assertEqual("exit code 3", result["attempts"][0]["error"])

    def test_host_error_message_is_reported(self):
        self.configure({"explorer": {"host": "opencode"}})
        os.environ["FAKE_OPENCODE"] = "autherror"
        result = dispatch.dispatch(self.repo, "explorer", "Question: x")
        self.assertFalse(result["ok"])
        self.assertEqual("exit code 1: Token refresh failed: 401", result["message"])

    def test_timeout_kills_the_process(self):
        self.configure({"worker": {"host": "claude"}})
        os.environ["FAKE_CLAUDE"] = "sleep"
        result = dispatch.dispatch(self.repo, "worker", "Goal: x", timeout=2)
        self.assertFalse(result["ok"])
        self.assertTrue(result["attempts"][0]["timedOut"])
        self.assertLess(result["attempts"][0]["elapsedSeconds"], 20)

    def test_command_line_override_replaces_the_configured_route(self):
        self.configure({"reviewer": {"host": "claude", "model": "opus", "fallback": [{"host": "opencode"}]}})
        chain = dispatch.route_chain(dispatch.load_config(self.repo), "reviewer", {"host": "codex", "model": None, "effort": None})
        self.assertEqual([{"host": "codex"}], chain)

    def test_dry_run_does_not_execute_or_log(self):
        self.configure({"reviewer": {"host": "codex"}})
        result = dispatch.dispatch(self.repo, "reviewer", "Goal: x", dry_run=True)
        self.assertEqual("dry-run", result["status"])
        self.assertEqual([], self.calls())
        self.assertFalse((self.repo / ".git" / "lemmings" / "runs").exists())

    def test_invalid_routes(self):
        with self.assertRaises(HelperError):
            dispatch.route_chain({"roles": {"worker": {"host": "gemini"}}}, "worker", {})
        with self.assertRaises(HelperError):
            dispatch.route_chain({"roles": {"worker": {"host": "codex", "model": "--yolo"}}}, "worker", {})
        with self.assertRaises(HelperError):
            dispatch.route_chain({"roles": {"worker": {"host": "opencode", "model": "no-provider"}}}, "worker", {})

    def test_model_confirmation(self):
        self.assertTrue(dispatch.model_confirmed("opus", ["claude-opus-5"]))
        self.assertTrue(dispatch.model_confirmed("openai/gpt-5.6-sol", ["gpt-5.6-sol"]))
        self.assertFalse(dispatch.model_confirmed("gpt-5.6-sol", ["gpt-5.6-luna"]))
        self.assertIsNone(dispatch.model_confirmed("gpt-5.6-sol", []))
