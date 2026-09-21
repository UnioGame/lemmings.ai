import json

from support import HermeticTest

from lemmings_telemetry.report import load_runs, render_markdown, summarize, summarize_tasks


class TelemetryTests(HermeticTest):
    def write_run(self, name, value):
        directory = self.tmp / "runs" / name
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps(value), encoding="utf-8")

    def test_summary_groups_routes_and_normalizes_tokens(self):
        self.write_run("20260921T100000Z-reviewer-aaaaaa", {
            "role": "reviewer", "host": "claude", "requestedModel": "opus", "status": "completed",
            "verdict": "ChangesRequested", "elapsedSeconds": 10, "usage": {"input_tokens": 100, "output_tokens": 20}})
        self.write_run("20260921T110000Z-reviewer-bbbbbb", {
            "role": "reviewer", "host": "claude", "requestedModel": "opus", "status": "completed",
            "verdict": "Accepted", "fallbackUsed": True, "elapsedSeconds": 5, "usage": None})
        self.write_run("20260921T120000Z-worker-cccccc", {"role": "worker", "status": "dry-run"})
        (self.tmp / "runs" / "broken").mkdir()
        (self.tmp / "runs" / "broken" / "result.json").write_text("{", encoding="utf-8")
        summary = summarize(load_runs(self.tmp / "runs"))
        self.assertEqual(2, summary["totalRuns"])
        row = summary["routes"][0]
        self.assertEqual((1, 1, 1, 1), (row["accepted"], row["changesRequested"], row["fallbacks"], row["usageMissing"]))
        self.assertEqual({"inputTokens": 100, "outputTokens": 20}, row["tokens"])
        self.assertIn("| reviewer | claude | opus | 2 |", render_markdown(summary))

    def test_task_journal_summary(self):
        directory = self.tmp / "tasks"
        directory.mkdir()
        (directory / "TASKS.md").write_text(
            "# Tasks\n\n| ID | Task | Where | Who | Depends on | Status |\n| --- | --- | --- | --- | --- | --- |\n"
            "| P-01 | A | x | W | — | Done (`abc1234`) — [details](P-01.md) |\n"
            "| P-02 | B | x | W | 01 | Repair 1 — [details](P-02.md) |\n"
            "| P-03 | C | x | W | — | Not started |\n\nAfter the table.\n", encoding="utf-8")
        (directory / "P-01.md").write_text(
            "## Log\n\n- 2026-09-21 10:00 In progress — w\n- 2026-09-21 11:00 Repair 1 — f\n"
            "- 2026-09-21 12:00 Escalated — strong\n- 2026-09-21 14:00 Done — ok\n", encoding="utf-8")
        (directory / "P-02.md").write_text("## Log\n\n- 2026-09-21 10:00 Repair 1 — x\n", encoding="utf-8")
        journal = summarize_tasks(directory)
        self.assertEqual({"Done": 1, "Repair": 1, "Not started": 1}, journal["counts"])
        self.assertEqual((2, 1, 4.0), (journal["repairRounds"], journal["escalations"], journal["medianLeadHours"]))
        self.assertIsNone(summarize_tasks(self.tmp / "missing"))
        self.assertIn("Tasks: 3", render_markdown({"routes": [], "taskJournal": journal}))
