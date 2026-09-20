from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/lemmings/scripts"))

from lemmings.budget import consume_context_expansions, extend_budget, new_task_budget
from lemmings.contracts import validate_invocation, validate_task_budget


class ContextBudgetTests(unittest.TestCase):
    def test_context_expansions_require_progress_and_stop_at_three(self) -> None:
        profile = json.loads((ROOT / "skills/lemmings/defaults.json").read_text(encoding="utf-8"))
        budget = new_task_budget(profile)
        self.assertEqual(1, consume_context_expansions(budget))
        with self.assertRaisesRegex(ValueError, "requires an unresolved question"):
            extend_budget(
                budget,
                kind="maxExpansions",
                amount=1,
                unresolved_question="",
                progress="The first search narrowed the symbol.",
            )
        extend_budget(
            budget,
            kind="maxExpansions",
            amount=2,
            unresolved_question="Which implementation owns the remaining call?",
            progress="The first search narrowed the symbol.",
        )
        self.assertEqual(3, consume_context_expansions(budget, 2))
        with self.assertRaisesRegex(ValueError, "exhausted"):
            consume_context_expansions(budget)
        value = {"budget": budget}
        self.assertTrue(validate_task_budget(value).ok, validate_task_budget(value).as_dict())

    def test_invocation_rejects_reference_and_packet_hard_ceilings(self) -> None:
        base = {
            "schemaVersion": 5,
            "runId": "run",
            "taskId": "task",
            "taskRevision": 1,
            "invocationId": "inv",
            "attempt": 1,
            "role": "explorer",
            "baseSha": "base",
            "profileDigest": "profile",
            "taskDigest": "task",
            "contextDigest": "context",
            "objective": "inspect",
            "acceptanceCriteria": [],
            "ownedPaths": [],
            "forbiddenPaths": [],
            "contextRefs": [],
            "validationCommands": [],
            "limits": {"maxTurns": 6, "maxToolCalls": 24, "deadlineSeconds": 600},
            "outputSchemaVersion": 4,
        }
        refs = [{"ref": f"src/{index}", "purpose": "needed", "contentHash": "hash"} for index in range(25)]
        too_many = {**base, "contextRefs": refs}
        self.assertIn("context.entries", {item.code for item in validate_invocation(too_many).findings})
        too_large = {**base, "objective": "x" * 33000}
        self.assertIn("context.bytes", {item.code for item in validate_invocation(too_large).findings})


if __name__ == "__main__":
    unittest.main()
