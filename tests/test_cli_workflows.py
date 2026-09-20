"""User-facing offline flows across the real command/module seams."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"skills/lemmings/scripts"))
from lemmings.cli import build_parser

class CliWorkflowTests(unittest.TestCase):
    def test_scan_save_select_manual_priority_and_frozen_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/"repo";home=root/"home"
            (repo/".agents").mkdir(parents=True);(home/".codex").mkdir(parents=True)
            (home/".codex/config.toml").write_text('model = "demo/worker"\nwire_api = "responses"\n[model_providers.demo.models]\nworker = {}\nreviewer = {}\n')
            profile=json.loads((ROOT/"skills/lemmings/defaults.json").read_text())
            profile["modelRoutes"]={"native":{"worker":[{"providerId":"manual","modelId":"locked"}]}}
            config=repo/".agents/lemmings.json";config.write_text(json.dumps(profile))
            outputs=[]
            def run(*tokens):
                args=build_parser().parse_args([*tokens,"--repo",str(repo)])
                outputs.clear()
                with patch("lemmings.cli.emit",side_effect=outputs.append):
                    self.assertEqual(0,args.run(args))
                return outputs[-1]
            with patch("pathlib.Path.home",return_value=home):
                scan=run("models","scan","--offline","--output","inventory.json")
                inventory=json.loads((repo/"inventory.json").read_text())
                route=next(r for r in inventory["routes"] if r["providerId"]=="demo" and r["modelId"]=="worker")
                (repo/"routes.json").write_text(json.dumps({"worker":[route],"reviewer":[],"explorer":[]}))
                proposal=run("models","propose","--name","balanced","--routes","routes.json","--output","proposal.json")
                run("models","apply","--proposal","proposal.json","--confirm",proposal["proposalDigest"])
                state_path=home/".lemmings/state.json"
                self.assertFalse(json.loads(state_path.read_text()).get("selections"))
                run("profiles","use","balanced")
                selected=run("profiles","inspect","balanced")
                self.assertEqual("project-manual",selected["sources"]["worker"])
                self.assertEqual("manual",selected["roleRoutes"]["worker"][0]["providerId"])
                (repo/"owned.txt").write_text("owned")
                task=json.loads((ROOT/"skills/lemmings/templates/task.json").read_text())
                task.update(baseSha="base",workingSet=[{"ref":"owned.txt","purpose":"owned source"}])
                task["ownership"]["owned"]=["owned.txt"]
                task_path=repo/"task.json";task_path.write_text(json.dumps(task))
                invocation=run("invocation","create","--task","task.json","--role","reviewer","--attempt","1","--expected-revision","0","--preset","balanced")
                frozen=json.loads(task_path.read_text())["effectiveConfig"]
                self.assertEqual("balanced",frozen["profile"]["name"])
                self.assertEqual([],frozen["rules"]["ruleRefs"])
                changed=copy.deepcopy(profile);changed["modelRoutes"]={};config.write_text(json.dumps(changed))
                result={"schemaVersion":5,"invocationId":invocation["invocationId"],"attempt":1,"status":"succeeded",
                        **{key:[] for key in ("changedPaths","acceptanceEvidence","validationEvidence","findings","blockers","remainingRisks")}}
                (repo/"result.json").write_text(json.dumps(result))
                accepted=run("invocation","accept","--task","task.json","--result","result.json","--expected-revision","1")
                self.assertTrue(accepted["ok"])
                self.assertEqual(frozen,json.loads(task_path.read_text())["effectiveConfig"])
