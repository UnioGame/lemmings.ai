import json

from support import HermeticTest

from lemmings import agents, brief
from lemmings.gitutil import HelperError

GOOD = """Task: FK-1
Agent: claude-worker
Goal: add the zoom table
Acceptance:
- farthest level is 1
Owned paths: GameClient/Assets/Game.Runtime/FrontierKeep/**
Checks: pwsh scripts/frontier-tests.ps1 filter FrontierZoomPolicyTests, exit 0
Context:
- FrontierZoomPolicy.cs#Levels: the table
Stop: done when the check passes; blocked when Unity is locked
"""


class BriefTests(HermeticTest):
    def test_template_passes_its_own_check(self):
        outcome = brief.check(brief.template("FK-2"))
        self.assertEqual([], outcome["warnings"])

    def test_good_brief_has_no_warnings(self):
        outcome = brief.check(GOOD)
        self.assertEqual([], outcome["warnings"])
        self.assertEqual(4096, outcome["targetBytes"])

    def test_missing_sections_size_and_polling_are_reported(self):
        outcome = brief.check("Goal: x\nChecks: poll in the foreground until it is green\n" + "filler " * 800)
        text = " | ".join(outcome["warnings"])
        self.assertIn("missing sections: Task, Acceptance, Owned paths, Context", text)
        self.assertIn("target", text)
        self.assertIn("poll or wait", text)

    def test_ceiling_comes_from_context_policy_and_never_blocks(self):
        config = {"contextPolicy": {"targetBytes": 100, "ceilingBytes": 200}}
        outcome = brief.check(GOOD + "x" * 400, config)
        self.assertTrue(outcome["ok"])
        self.assertTrue(any("ceiling" in item for item in outcome["warnings"]))

    def test_too_many_criteria_context_items_and_pasted_code(self):
        many = "Acceptance:\n" + "\n".join(f"- c{i}" for i in range(7)) + "\nContext:\n" + "\n".join(f"- f{i}.cs: why" for i in range(12))
        code = "```\n" + "\n".join("line" for _ in range(30)) + "\n```\n"
        outcome = brief.check("Task: T-1\nGoal: g\n" + many + "\nOwned paths: p\nChecks: c\n" + code)
        text = " | ".join(outcome["warnings"])
        self.assertIn("7 criteria", text)
        self.assertIn("12 items", text)
        self.assertIn("30 lines of code", text)


class AgentBudgetTests(HermeticTest):
    def configured(self, extra=None):
        return agents.load_agents({"defaults": False, "agents": {
            "w": {"role": "worker", "host": "claude", "model": "sonnet", **(extra or {})},
            "e": {"role": "explorer", "host": "claude", "model": "haiku"},
            "r": {"role": "reviewer", "host": "claude", "model": "sonnet"}}})

    def test_native_claude_agents_get_role_default_turns(self):
        files = {path.name: text for path, text in agents.planned_files(self.tmp, self.configured()).items()}
        self.assertIn("maxTurns: 80", files["lemmings-w.md"])
        self.assertIn("maxTurns: 25", files["lemmings-e.md"])
        self.assertIn("maxTurns: 40", files["lemmings-r.md"])

    def test_agent_override_and_instructions_are_written(self):
        files = {path.name: text for path, text in agents.planned_files(
            self.tmp, self.configured({"maxTurns": 120, "instructions": "Return a digest, never raw output."})).items()}
        self.assertIn("maxTurns: 120", files["lemmings-w.md"])
        self.assertTrue(files["lemmings-w.md"].rstrip().endswith("Return a digest, never raw output."))

    def test_invalid_budget_fields_are_rejected(self):
        for extra in ({"maxTurns": 0}, {"maxTurns": "many"}, {"maxTurns": True}, {"instructions": ""},
                      {"instructions": "x" * 601}):
            with self.assertRaises(HelperError, msg=json.dumps(extra)):
                self.configured(extra)


class DoctorConfigTests(HermeticTest):
    def test_unread_config_keys_are_reported_without_failing(self):
        from lemmings import cli

        repo = self.make_repo()
        (repo / ".agents").mkdir()
        (repo / ".agents" / "lemmings.json").write_text(
            json.dumps({"agents": {}, "contextPolicy": {"targetBytes": 4096}, "invocationBudgets": {"worker": 48}}), encoding="utf-8")
        report = cli.doctor(repo)
        self.assertEqual(1, len(report["warnings"]))
        self.assertIn("invocationBudgets", report["warnings"][0])
