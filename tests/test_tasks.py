import json
import subprocess
import sys

from support import ROOT, HermeticTest, git

from lemmings import tasks
from lemmings.gitutil import HelperError

RUN = [sys.executable, str(ROOT / "skills" / "lemmings" / "scripts" / "run.py")]


class TaskJournalTests(HermeticTest):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo()
        self.dir = self.repo / "docs" / "tasks"

    def journal(self):
        return tasks.Journal(self.dir)

    def test_add_update_and_next_wave(self):
        brief = self.tmp / "brief.md"
        brief.write_text("Goal: compact wire format\nAcceptance:\n- 16 B per entity", encoding="utf-8")
        self.journal().add("NCORE-00", "Fix P0", "superproject", "M", "", "")
        self.journal().add("NCORE-01", "Perf pipeline", "Unity", "W (codex-worker)", "00", "")
        self.journal().add("NCORE-02", "Histogram", "worktree", "W", "00", brief.read_text(encoding="utf-8"))
        self.journal().add("NCORE-05", "Baseline", "stand", "M", "01, 02", "")
        self.assertEqual(["NCORE-00"], [item["id"] for item in tasks.ready(self.journal())])
        sha = git(self.repo, "rev-parse", "--short", "HEAD")
        self.journal().update("NCORE-00", "Done", "P0 fixed", sha)
        self.assertEqual(["NCORE-01", "NCORE-02"], [item["id"] for item in tasks.ready(self.journal())])
        self.assertEqual(["NCORE-01", "NCORE-02"], self.journal().dependencies(self.journal().find("NCORE-05")))
        self.journal().update("NCORE-01", "Repair 1", "P1 overflow", who="W (codex-worker -> codex-worker-strong)")
        row = self.journal().find("NCORE-01")
        self.assertEqual("Repair", tasks.status_word(row["status"]))
        self.assertEqual("W (codex-worker -> codex-worker-strong)", row["who"])
        log = self.journal().log("NCORE-01")
        self.assertEqual(["Not started", "Repair 1"], [entry["status"] for entry in log])
        self.assertIn("Goal: compact wire format", (self.dir / "NCORE-02.md").read_text(encoding="utf-8"))
        outcome = tasks.check(self.journal(), self.repo)
        self.assertEqual([], outcome["errors"])
        self.assertEqual({"Done": 1, "Repair": 1, "Not started": 2}, tasks.summary(self.journal())["counts"])

    def test_existing_hand_written_table_is_preserved(self):
        self.dir.mkdir(parents=True)
        (self.dir / "TASKS.md").write_text(
            "# Network core\n\nIntro text.\n\n| ID | Task | Where | Who | Depends on | Status |\n| --- | --- | --- | --- | --- | --- |\n"
            "| NCORE-00 | Fix P0 | superproject | M | — | Not started |\n\n## Notes\n\nKeep me.\n", encoding="utf-8")
        self.journal().add("NCORE-01", "Next | step", "x", "W", "00", "")
        text = (self.dir / "TASKS.md").read_text(encoding="utf-8")
        self.assertIn("Intro text.", text)
        self.assertIn("## Notes\n\nKeep me.", text)
        self.assertIn("Next \\| step", text)
        rows = self.journal().rows
        self.assertEqual(["NCORE-00", "NCORE-01"], [row["id"] for row in rows])
        self.assertEqual("Next | step", rows[1]["task"])

    def test_check_finds_journal_defects(self):
        self.dir.mkdir(parents=True)
        (self.dir / "TASKS.md").write_text(
            "| ID | Task | Where | Who | Depends on | Status |\n| --- | --- | --- | --- | --- | --- |\n"
            "| P-01 | A | x | W | 03 | Done |\n"
            "| P-02 | B | x | W | 99 | Almost done |\n"
            "| P-03 | C | x | W | 01 | In progress — [details](P-03.md) |\n"
            "| P-04 | E | x | W | — | Done (`deadbee`) |\n"
            "| P-05 | F | x | W | — | In review |\n"
            "| P-04 | Duplicate | x | W | — | Not started |\n", encoding="utf-8")
        (self.dir / "P-05.md").write_text("# P-05\n\n## Log\n\n- 2026-09-21 10:00 In progress — started\n", encoding="utf-8")
        outcome = tasks.check(self.journal(), self.repo)
        text = "\n".join(outcome["errors"])
        for expected in ("P-04: duplicate id", "P-01: Done needs at least one commit", "P-02: status must start",
                         "P-02: unknown dependency 99", "P-03: link target P-03.md does not exist",
                         "dependency cycle", "P-05: index says In review but the last log entry says In progress"):
            self.assertIn(expected, text)
        self.assertTrue(any("none of deadbee" in item for item in outcome["warnings"]))
        self.assertTrue(any("P-01: Done while dependencies are not Done: P-03" in item for item in outcome["warnings"]))

    def test_invalid_inputs(self):
        with self.assertRaisesRegex(HelperError, "task id"):
            self.journal().add("12 bad", "x", "", "", "", "")
        self.journal().add("P-01", "A", "", "", "", "")
        with self.assertRaisesRegex(HelperError, "already exists"):
            self.journal().add("P-01", "A", "", "", "", "")
        with self.assertRaisesRegex(HelperError, "status must be one of"):
            self.journal().update("P-01", "Finished")
        with self.assertRaisesRegex(HelperError, "no task"):
            self.journal().update("P-09", "Done")
        with self.assertRaisesRegex(HelperError, "hex SHA"):
            self.journal().update("P-01", "Done", commit="HEAD")

    def test_cli_round_trip(self):
        def run(*args):
            process = subprocess.run([*RUN, "tasks", *args, "--repo", str(self.repo)], capture_output=True,
                                     text=True, encoding="utf-8")
            return process.returncode, json.loads(process.stdout)

        self.assertEqual(0, run("add", "T-01", "--title", "First", "--who", "W")[0])
        self.assertEqual(0, run("update", "T-01", "--status", "In progress", "--note", "codex-worker on task/t-01")[0])
        code, listing = run("list")
        self.assertEqual({"In progress": 1}, listing["counts"])
        code, outcome = run("check")
        self.assertEqual((0, []), (code, outcome["errors"]))

    def test_configured_directory(self):
        (self.repo / ".agents").mkdir()
        (self.repo / ".agents" / "lemmings.json").write_text(json.dumps({"tasks": {"dir": "plans"}}), encoding="utf-8")
        subprocess.run([*RUN, "tasks", "add", "T-01", "--title", "First", "--repo", str(self.repo)], check=True,
                       capture_output=True)
        self.assertTrue((self.repo / "plans" / "TASKS.md").is_file())
