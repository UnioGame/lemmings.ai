import json

from support import HermeticTest

from lemmings_telemetry.report import load_runs, render_markdown, summarize


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
