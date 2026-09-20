import copy
import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))
from lemmings.invocations import record_invocation, result_findings
from lemmings.effective import capture_effective, digest

class EffectiveTests(unittest.TestCase):
    def test_active_settings_are_frozen_and_rule_drift_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d); (repo/"owned.txt").write_text("base")
            (repo/"rule.md").write_text("rule one")
            rule={"ref":"rule.md","purpose":"target rules","contentHash":hashlib.sha256((repo/"rule.md").read_bytes()).hexdigest()}
            rules={"projects":[],"ruleRefs":[rule],"digest":"rule-digest"}
            profile=json.loads((ROOT/"skills/lemmings/defaults.json").read_text())
            task=json.loads((ROOT/"skills/lemmings/templates/task.json").read_text())
            task.update(baseSha="base",workingSet=[{"ref":"owned.txt","purpose":"owned source"}])
            snapshot={"schemaVersion":5,"name":"balanced","roleRoutes":{},"sources":{},"digest":"preset"}
            modules={"lemmings.profiles":types.SimpleNamespace(resolve_profile=lambda *a,**k:copy.deepcopy(snapshot)),"lemmings.rules":types.SimpleNamespace(resolve_rules=lambda *a,**k:copy.deepcopy(rules))}
            path=repo/"task.json";path.write_text(json.dumps(task))
            with patch.dict(sys.modules,modules):
                invocation=record_invocation(repo,path,profile,"reviewer",1,0,preset="balanced",freeze=True)
                task=json.loads(path.read_text())
                changed=copy.deepcopy(profile);changed["modelRoutes"]={"future":{}}
                result={"schemaVersion":5,"invocationId":invocation["invocationId"],"attempt":1,"status":"succeeded",**{key:[] for key in ("changedPaths","acceptanceEvidence","validationEvidence","findings","blockers","remainingRisks")}}
                self.assertTrue(result_findings(repo,task,changed,result).ok)
                with self.assertRaisesRegex(ValueError,"cannot switch"):
                    capture_effective(repo,task,profile,preset="new")
                (repo/"rule.md").write_text("rule two")
                with self.assertRaisesRegex(ValueError,"rules changed"):
                    result_findings(repo,task,changed,result)
                task["effectiveConfig"]["profile"]["name"]="tampered"
                with self.assertRaisesRegex(ValueError,"digest"):
                    result_findings(repo,task,changed,result)

    def test_rule_refs_share_existing_budget(self):
        from lemmings.invocations import build_invocation
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d);(repo/"file").write_text("a")
            h=hashlib.sha256(b"a").hexdigest()
            task=json.loads((ROOT/"skills/lemmings/templates/task.json").read_text())
            task.update(baseSha="base",workingSet=[{"ref":"file#"+str(i),"purpose":"source"} for i in range(12)])
            frozen={"schemaVersion":5,"profile":{"roleRoutes":{}},"rules":{"ruleRefs":[{"ref":"file","purpose":"rules","contentHash":h}]}}
            frozen["digest"]=digest(frozen);task["effectiveConfig"]=frozen
            with self.assertRaisesRegex(ValueError,"12 context"):
                build_invocation(repo,task,{},"worker",attempt=1)

    def test_reviewer_uses_its_role_route(self):
        from lemmings.invocations import build_invocation
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)
            task=json.loads((ROOT/"skills/lemmings/templates/task.json").read_text())
            task.update(baseSha="base",workingSet=[])
            task["models"].update(hostId="native",assigned="current-host/default")
            frozen={"schemaVersion":5,"profile":{"roleRoutes":{"reviewer":[{"hostId":"opencode","providerId":"review-provider","modelId":"review-model"}]}},"rules":{"ruleRefs":[]}}
            frozen["digest"]=digest(frozen);task["effectiveConfig"]=frozen
            invocation=build_invocation(repo,task,{},"reviewer",attempt=1)
            self.assertEqual("review-provider/review-model",invocation["assignedModel"])
            self.assertEqual("opencode",invocation["assignedHost"])

    def test_hooks_allow_inherited_native_model_and_keep_frozen_routes(self):
        from lemmings.hooks import handle, derive_context_packet
        profile=json.loads((ROOT/"skills/lemmings/defaults.json").read_text())
        task=json.loads((ROOT/"skills/lemmings/templates/task.json").read_text())
        task.update(baseSha="base",workingSet=[])
        frozen={"schemaVersion":5,"profile":{"roleRoutes":{}},"rules":{"ruleRefs":[]}}
        frozen["digest"]=digest(frozen);task["effectiveConfig"]=frozen
        task["execution"]["invocations"]=[derive_context_packet(task,None,"worker",{"profile":profile})]
        changed=copy.deepcopy(profile)
        changed["modelRoutes"]={"codex":{role:[{"providerId":"future","modelId":"model"}] for role in ("worker","reviewer","explorer")}}
        result=handle({"event":"PreToolUse","tool_name":"spawn_agent","task":task,"profile":changed,"task_name":"lemmings-worker"})
        self.assertEqual("allow",result["decision"],result)

    def test_run_rejects_task_closed_or_context_changed(self):
        from lemmings.invocations import build_invocation, validate_dispatch
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d);(repo/"owned").write_text("old")
            task=json.loads((ROOT/"skills/lemmings/templates/task.json").read_text())
            task.update(baseSha="base",workingSet=[{"ref":"owned","purpose":"source"}])
            profile=json.loads((ROOT/"skills/lemmings/defaults.json").read_text())
            invocation=build_invocation(repo,task,profile,"worker",attempt=1)
            validate_dispatch(repo,task,profile,invocation)
            task["state"]="Integrated"
            with self.assertRaisesRegex(ValueError,"lifecycle"):
                validate_dispatch(repo,task,profile,invocation)
            task["state"]="Ready";(repo/"owned").write_text("changed")
            with self.assertRaisesRegex(ValueError,"context file"):
                validate_dispatch(repo,task,profile,invocation)

    def test_engine_scope_includes_owned_paths_and_explicit_override_wins(self):
        calls=[]
        modules={"lemmings.profiles":types.SimpleNamespace(resolve_profile=lambda *a,**k:{"schemaVersion":5,"roleRoutes":{},"sources":{}}),
                 "lemmings.rules":types.SimpleNamespace(resolve_rules=lambda *a,**k:calls.append(k) or {"projects":[],"ruleRefs":[],"digest":"rules"})}
        with tempfile.TemporaryDirectory() as d, patch.dict(sys.modules,modules):
            task={"workingSet":[{"ref":"docs/plan.md"}],"ownership":{"owned":["GameClient/Assets/**/*.cs"]}}
            capture_effective(Path(d),task,{})
            self.assertIn("GameClient/Assets",calls[-1]["paths"])
            task.pop("effectiveConfig");task["ruleSelection"]={"paths":["other/src"],"technologies":["phaser"]}
            capture_effective(Path(d),task,{})
            self.assertEqual(["other/src"],calls[-1]["paths"])
            self.assertEqual(["phaser"],calls[-1]["technologies"])
