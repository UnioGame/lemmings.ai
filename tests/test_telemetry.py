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


class UsageTests(HermeticTest):
    def transcript(self, path, *entries):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")

    def reply(self, ident, model, **usage):
        return {"type": "assistant", "timestamp": "2026-10-10T10:00:00Z", "requestId": ident,
                "message": {"id": ident, "model": model, "usage": usage}}

    def test_cost_is_grouped_by_task_and_agent_type(self):
        from lemmings_telemetry.usage import collect, project_slug

        repo = self.tmp / "game"
        repo.mkdir()
        projects = self.tmp / "projects"
        base = projects / project_slug(repo) / "session1"
        self.transcript(projects / project_slug(repo) / "session1.jsonl",
                        {"message": {"content": [{"type": "tool_use", "id": "t1", "name": "Agent",
                                                  "input": {"subagent_type": "lemmings-claude-worker", "prompt": "x"}},
                                                 {"type": "tool_use", "id": "t2", "name": "Agent",
                                                  "input": {"subagent_type": "general-purpose", "prompt": "y"}}]}},
                        {"message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "agentId: aaa1"},
                                                 {"type": "tool_result", "tool_use_id": "t2", "content": "agentId: bbb2"}]}})
        user = lambda text: {"type": "user", "message": {"content": text}}  # noqa: E731
        self.transcript(base / "subagents" / "agent-aaa1.jsonl", user("Task: FK-7\nGoal: x"),
                        self.reply("m1", "claude-sonnet-5-5", input_tokens=10, output_tokens=1000, cache_read_input_tokens=200000),
                        self.reply("m1", "claude-sonnet-5-5", input_tokens=10, output_tokens=1000, cache_read_input_tokens=200000))
        self.transcript(base / "subagents" / "agent-bbb2.jsonl", user("no header here"),
                        self.reply("m2", "claude-haiku-5-5", input_tokens=1000, output_tokens=100))
        report = collect(projects, repo, None)
        self.assertEqual(2, report["subagents"])
        self.assertEqual(1, report["byTask"]["FK-7"]["requests"])  # the duplicated message counts once
        self.assertEqual(200010, report["byTask"]["FK-7"]["peakContext"])
        self.assertEqual(1, report["outsideLemmings"]["agents"])
        self.assertIn("(no Task line)", report["byTask"])
        self.assertEqual(["lemmings-claude-worker", "general-purpose"], list(report["byAgentType"]))
