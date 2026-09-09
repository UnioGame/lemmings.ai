import json
import subprocess
import types
from unittest.mock import patch
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"skills/lemmings/scripts"))
from lemmings.cli import build_parser, command_integration, command_run

class CliExtensionTests(unittest.TestCase):
    def test_documented_command_syntax(self):
        commands=[
            ["models","scan","--offline"],
            ["models","scan","--host-catalog","host.json","--output","inventory.json"],
            ["models","probe","--route","route.json"],
            ["models","propose","--name","balanced","--routes","routes.json","--output","proposal.json"],
            ["models","apply","--proposal","proposal.json","--confirm","digest"],
            ["profiles","list"], ["profiles","inspect","balanced"], ["profiles","use","balanced"],
            ["rules","explain","--path","game/src","--technology","pixijs","--platform","web"],
            ["invocation","create","--task","task.json","--role","worker","--attempt","1","--expected-revision","0","--preset","balanced","--profile","manual.json"],
            ["run","--task","task.json","--invocation-id","id","--route","route.json","--dry-run"],
        ]
        commands += [
            ["workspace","prepare","--task","task.json","--destination","../work","--branch","codex/task","--expected-revision","0"],
            ["workspace","release","--workspace-id","w","--task","task.json","--task-revision","2","--expected-revision","1"],
            ["workspace","remove","--workspace-id","w","--task","task.json","--task-revision","2","--expected-revision","1"],
        ]
        parser=build_parser()
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(callable(parser.parse_args(command).run))
        args=parser.parse_args(next(c for c in commands if c[0] == "invocation"))
        self.assertEqual("balanced",args.preset)
        self.assertEqual("manual.json",args.profile)

    def test_integration_checks_exact_tree_and_bounds_failure_output(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp)
            def git(*args):
                return subprocess.run(["git","-C",str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()
            git("init"); git("config","user.email","test@example.invalid"); git("config","user.name","Test")
            check=repo/"check.py"
            check.write_text("import sys\nprint('x'*6000)\nsys.exit(3)\n")
            git("add","check.py"); git("commit","-m","fixture"); head=git("rev-parse","HEAD")
            task=repo/"task.json"
            value={"revision":0,"taskId":"T","close":{"mergeCommit":head},"validation":{"commands":[f'"{sys.executable}" check.py']}}
            task.write_text(json.dumps(value))
            args=build_parser().parse_args(["integration","validate","--repo",str(repo),"--task","task.json","--expected-revision","0"])
            with patch("lemmings.cli.emit"):
                self.assertEqual(1,command_integration(args))
            saved=json.loads(task.read_text()); evidence=saved["close"]["integrationEvidence"][0]
            self.assertFalse(evidence["passed"]); self.assertEqual(3,evidence["exitCode"])
            self.assertGreater(evidence["diagnostics"]["omittedBytes"],0)
            self.assertLessEqual(len(evidence["diagnostics"]["tail"].encode()),4096)
            args.expected_revision=1; check.write_text("print('dirty')")
            with self.assertRaisesRegex(ValueError,"integration tree has changes"):
                command_integration(args)
            self.assertEqual(1,json.loads(task.read_text())["revision"])

    def test_worker_run_rejects_wrong_destination_and_head_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp); subprocess.run(["git","init",str(repo)],check=True,capture_output=True)
            for key,value in [("user.email","test@example.invalid"),("user.name","Test")]:
                subprocess.run(["git","-C",str(repo),"config",key,value],check=True,capture_output=True)
            subprocess.run(["git","-C",str(repo),"commit","--allow-empty","-m","fixture"],check=True,capture_output=True)
            head=subprocess.run(["git","-C",str(repo),"rev-parse","HEAD"],check=True,capture_output=True,text=True).stdout.strip()
            task={"revision":1,"state":"Active","baseSha":head,"role":"worker","workspace":{"destination":str(repo/"wrong")},
                  "models":{"assigned":"current-host/default","hostId":"native"},"execution":{"invocations":[{"invocationId":"i","taskRevision":1,"role":"worker"}]}}
            path=repo/"task.json"; path.write_text(json.dumps(task))
            (repo/"route.json").write_text(json.dumps({"executor":"native"}))
            args=build_parser().parse_args(["run","--repo",str(repo),"--task","task.json","--invocation-id","i","--route","route.json","--dry-run"])
            launches=[]
            module=types.SimpleNamespace(build_launch=lambda *a:launches.append(a) or {"executor":"native","env":{"secret":"hidden"},"stdin":"hidden"},run_invocation=lambda *a:None)
            with patch.dict(sys.modules,{"lemmings.runners":module}),patch("lemmings.invocations.validate_dispatch"),patch("lemmings.cli.emit") as emit:
                with self.assertRaisesRegex(ValueError,"worker destination"): command_run(args)
                task["workspace"]={};task["baseSha"]="0"*40;path.write_text(json.dumps(task))
                with self.assertRaisesRegex(ValueError,"worker HEAD"): command_run(args)
                self.assertFalse(launches)
                task["baseSha"]=head;path.write_text(json.dumps(task));self.assertEqual(0,command_run(args))
                self.assertEqual({"executor":"native"},emit.call_args.args[0])
