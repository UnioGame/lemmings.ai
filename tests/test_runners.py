import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))
from lemmings import runners
from lemmings.workspace import load_registry, register_workspace, release_workspace

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name); self.repo = self.root/"repo"; self.repo.mkdir()
        self.home = self.root/"home"; self.home.mkdir()
        for args in (["init"], ["config", "user.email", "test@example.invalid"], ["config", "user.name", "test"], ["commit", "--allow-empty", "-m", "base"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        self.head = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        self.inv = {"schemaVersion":5, "invocationId":"inv-1", "attempt":1, "role":"worker", "taskId":"T", "taskRevision":1, "baseSha":self.head, "ownedPaths":["src/**"], "contextRefs":[], "validationCommands":["python -m unittest"], "limits":{"deadlineSeconds":5}}
        self.route = {"hostId":"opencode", "executor":"opencode", "providerId":"demo", "modelId":"model", "protocol":"responses"}
    def tearDown(self): self.temp.cleanup()
    def result(self, **kwargs):
        return {"schemaVersion":5, "invocationId":"inv-1", "attempt":1, "status":"succeeded", "candidateHead":self.head, **{k:[] for k in runners.ARRAYS}, **kwargs}
    def fake(self, code):
        path=self.root/"fake.py"; path.write_text(code, encoding="utf-8")
        return {"executor":"opencode", "argv":[sys.executable,str(path)], "env":{}, "stdin":"{}", "readOnly":False, "capabilities":{}}
    def test_native_default_requires_bridge_and_preserves_identity(self):
        route={"hostId":"native","executor":"native"}
        result=runners.run_invocation(self.repo,self.inv,route)
        self.assertEqual("blocked",result["status"]); self.assertEqual(["dispatch-required"],result["blockers"])
        def bridge(*args,**kwargs): return self.result()
        bridge.capabilities={k:True for k in ("freshSession","noDelegation","roleRestrictions","cancellation","readOnly")}
        self.assertEqual("succeeded",runners.run_invocation(self.repo,self.inv,route,native_bridge=bridge)["status"])
        def invalid(*args,**kwargs): return self.result(invocationId="other")
        invalid.capabilities=bridge.capabilities
        self.assertEqual("failed",runners.run_invocation(self.repo,self.inv,route,native_bridge=invalid)["status"])
    def test_launch_restrictions_protocols_profiles_and_go_headers(self):
        with patch("lemmings.runners._home",return_value=self.home), patch("lemmings.runners._trusted_bundle",return_value=("https://opencode.ai/zen/go/v1","PRIVATE_KEY")):
            for protocol,driver in (("responses","@ai-sdk/openai"),("chat-completions","@ai-sdk/openai-compatible"),("messages","@ai-sdk/anthropic")):
                route={**self.route,"protocol":protocol}
                reader={**self.inv,"role":"reviewer"}
                launch=runners.build_launch(self.repo,reader,route); config=json.loads(launch["env"]["OPENCODE_CONFIG_CONTENT"])
                agent=next(iter(config["agent"].values())); self.assertEqual("deny",agent["permission"]["*"])
                self.assertNotIn("bash",agent["permission"]); self.assertNotIn("edit",agent["permission"])
                self.assertEqual(driver,config["provider"]["demo"]["npm"])
                self.assertEqual("inv-1",config["provider"]["demo"]["options"]["headers"]["x-opencode-session"])
                self.assertIn("--pure",launch["argv"])
                self.assertFalse(any(x in launch["argv"] for x in ("--continue","--session","--attach","--auto")))
            launch=runners.build_launch(self.repo,self.inv,{**self.route,"hostId":"codex","executor":"codex","profileName":"selected","variantId":"high"})
            self.assertIn("--ignore-user-config",launch["argv"]); self.assertIn("--ephemeral",launch["argv"])
            self.assertNotIn("PRIVATE_KEY"," ".join(launch["argv"])); self.assertEqual("PRIVATE_KEY",launch["env"]["LEMMINGS_PROVIDER_KEY"])
            self.assertIn('model_reasoning_effort="high"',launch["argv"])
            with self.assertRaisesRegex(ValueError,"Responses"):
                runners.build_launch(self.repo,self.inv,{**self.route,"executor":"codex","protocol":"messages"})

    def test_host_native_codex_and_claude_launches_do_not_copy_credentials(self):
        codex_route = {"hostId": "codex", "executor": "codex", "providerId": "byteplus",
                       "modelId": "deepseek", "protocol": "responses", "configMode": "host",
                       "profileName": "byteplus", "variantId": "high"}
        launch = runners.build_launch(self.repo, self.inv, codex_route)
        self.assertIn("--profile", launch["argv"])
        self.assertIn("byteplus", launch["argv"])
        self.assertNotIn("--ignore-user-config", launch["argv"])
        self.assertEqual({}, launch["env"])
        self.assertNotIn("model_providers.lemmings-selected", " ".join(launch["argv"]))

        claude_route = {"hostId": "claude", "executor": "claude", "providerId": "bedrock",
                        "modelId": "claude-sonnet", "protocol": "messages",
                        "configMode": "host", "variantId": "high"}
        launch = runners.build_launch(self.repo, self.inv, claude_route)
        argv = launch["argv"]
        for flag in ("--safe-mode", "--no-session-persistence", "--json-schema",
                     "--permission-prompts", "--tools", "--effort"):
            self.assertIn(flag, argv)
        self.assertEqual({}, launch["env"])
        self.assertNotIn("ANTHROPIC_API_KEY", " ".join(argv))
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual([], settings["fallbackModel"])
        self.assertFalse(settings["switchModelsOnFlag"])

        reader = runners.build_launch(self.repo, {**self.inv, "role": "reviewer"}, claude_route)
        tools = reader["argv"][reader["argv"].index("--tools") + 1]
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Write", tools)
        reader_settings = json.loads(reader["argv"][reader["argv"].index("--settings") + 1])
        self.assertIn("Edit", reader_settings["permissions"]["deny"])

    def test_codex_host_disables_user_profile_and_project_mcp_servers(self):
        import json
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            repo = Path(temporary) / "repo"
            (home / ".codex").mkdir(parents=True)
            (repo / ".codex").mkdir(parents=True)
            (home / ".codex/config.toml").write_text("[mcp_servers.user_server]\nurl='http://invalid'\n", encoding="utf-8")
            (home / ".codex/byteplus.config.toml").write_text("[mcp_servers.profile_server]\ncommand='bad'\n", encoding="utf-8")
            (repo / ".codex/config.toml").write_text("[mcp_servers.project_server]\ncommand='bad'\n", encoding="utf-8")
            route = {"hostId": "codex", "providerId": "byteplus", "modelId": "deepseek",
                     "executor": "codex", "protocol": "responses", "configMode": "host",
                     "profileName": "byteplus"}
            with patch.object(runners, "_home", return_value=home):
                launch = runners.build_launch(repo, self.inv, route)
            command = " ".join(launch["argv"])
            for name in ("user_server", "profile_server", "project_server"):
                self.assertIn(f'mcp_servers.{name}.enabled=false', command)

    def test_claude_envelope_parsing_and_model_observation(self):
        path = self.root / "claude.json"
        payload = self.result()
        path.write_text(json.dumps({
            "type": "result", "result": json.dumps(payload),
            "modelUsage": {"claude-sonnet": {"inputTokens": 10, "outputTokens": 2}}
        }), encoding="utf-8")
        self.assertEqual("inv-1", runners._parse_output(path, "claude")["invocationId"])
        observed = runners._host_observation(path, "claude", "claude-sonnet")
        self.assertTrue(observed["modelConfirmed"])
        self.assertTrue(observed["usageComplete"])

    def test_fake_process_result_and_bounded_failure_evidence(self):
        launch=self.fake("print("+repr(json.dumps(self.result()))+")")
        with patch.object(runners,"build_launch",return_value=launch): result=runners.run_invocation(self.repo,self.inv,self.route)
        self.assertEqual("succeeded",result["status"],result)
        launch=self.fake("import sys\nprint('PRIVATE_PROVIDER_ERROR' * 10000)\nsys.exit(7)")
        with patch.object(runners,"build_launch",return_value=launch): result=runners.run_invocation(self.repo,self.inv,self.route)
        self.assertEqual("failed",result["status"]); self.assertIn("executor-exit:7",result["blockers"])
        self.assertNotIn("PRIVATE",json.dumps(result)); self.assertLess(len(json.dumps(result)),1500)
        self.assertEqual(7,result["validationEvidence"][0]["exitCode"])
    def test_jsonl_text_parts_and_reader_result_contract(self):
        path=self.root/"events.jsonl"
        payload=json.dumps(self.result())
        for parts in ([payload], [payload[:50],payload[50:]]):
            path.write_text("\n".join(json.dumps({"type":"text","part":{"text":part}}) for part in parts))
            self.assertEqual("inv-1",runners._parse_output(path,"opencode")["invocationId"])
        with self.assertRaises(ValueError):
            runners._checked_result(self.result(changedPaths=["edited"]),{**self.inv,"role":"reviewer"},True)

    def test_timeout_and_cancellation_terminate_spawned_children(self):
        marker=self.root/"child.pid"
        code="import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nPath("+repr(str(marker))+").write_text(str(p.pid))\ntime.sleep(60)"
        launch=self.fake(code)
        with patch.object(runners,"build_launch",return_value=launch):
            result=runners.run_invocation(self.repo,self.inv,self.route,cancelled=marker.exists)
        self.assertEqual("cancelled",result["status"],result)
        pid=int(marker.read_text())
        if os.name=="nt":
            import ctypes
            from ctypes import wintypes
            api=ctypes.WinDLL("kernel32",use_last_error=True);api.OpenProcess.restype=wintypes.HANDLE;api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];api.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=api.OpenProcess(0x100000,False,pid)
            if handle:
                try:self.assertEqual(0,api.WaitForSingleObject(handle,1000))
                finally:api.CloseHandle(handle)
        marker.unlink()
        inv=copy.deepcopy(self.inv);inv["limits"]["deadlineSeconds"]=0.2
        with patch.object(runners,"build_launch",return_value=launch): result=runners.run_invocation(self.repo,inv,self.route)
        self.assertEqual("failed",result["status"],result)
        self.assertFalse(list((runners.git_common_dir(self.repo)/"lemmings/runs").glob("*.lock")))
    def test_managed_live_run_blocks_other_writer_and_cleanup(self):
        workspace=self.root/"writer";subprocess.run(["git","-C",str(self.repo),"worktree","add","-b","writer",str(workspace)],check=True,capture_output=True)
        register_workspace(self.repo,workspace_id="writer",path=workspace,backend="code-worktree",managed_by="lemmings",lifetime="task",expected_revision=0,task_id="T",base_sha=self.head,branch="writer")
        launch=self.fake("import time\ntime.sleep(60)");cancel=threading.Event();results=[]
        with patch.object(runners,"build_launch",return_value=launch):
            thread=threading.Thread(target=lambda:results.append(runners.run_invocation(workspace,self.inv,self.route,cancelled=cancel.is_set)));thread.start()
            try:
                limit=time.monotonic()+5
                while time.monotonic()<limit:
                    registry=load_registry(self.repo)
                    if registry["entries"][0].get("processes"):break
                    time.sleep(0.05)
                self.assertTrue(registry["entries"][0].get("processes"))
                self.assertEqual("blocked",runners.run_invocation(workspace,{**self.inv,"invocationId":"other"},self.route)["status"])
                released=release_workspace(self.repo,workspace_id="writer",expected_revision=registry["revision"],task_state="Integrated",integration_evidence=True,action="remove")
                self.assertEqual("retained",released["action"]);self.assertTrue(workspace.exists())
            finally:cancel.set();thread.join(10)
        self.assertFalse(thread.is_alive());self.assertEqual("cancelled",results[0]["status"],results)
        self.assertFalse(load_registry(self.repo)["entries"][0].get("processes"))
    def test_unconfirmed_native_failure_keeps_lock(self):
        def bridge(*args,**kwargs): raise OSError("PRIVATE")
        bridge.capabilities={k:True for k in ("freshSession","noDelegation","roleRestrictions","cancellation")}
        result=runners.run_invocation(self.repo,self.inv,{"executor":"native"},native_bridge=bridge)
        self.assertEqual("blocked",result["status"]);self.assertTrue(list((runners.git_common_dir(self.repo)/"lemmings/runs").glob("*.lock")))

if __name__ == "__main__": unittest.main()
