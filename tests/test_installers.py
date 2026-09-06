from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def find_bash() -> str | None:
    candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    if os.name == "nt" and candidate.is_file():
        return str(candidate)
    return shutil.which("bash")


def launchers() -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh:
        result.append(("powershell", pwsh))
    bash = find_bash()
    if bash:
        result.append(("bash", bash))
    return result


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tool = self.root / "Lemmings source"
        for name in ("scripts", "skills", "agents"):
            shutil.copytree(PACKAGE_ROOT / name, self.tool / name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_repo(self, name: str = "игра с пробелом", projects: tuple[str, ...] = ("GameClient",)) -> Path:
        repo = self.root / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for relative in projects:
            project = repo / relative
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "Packages/manifest.json").write_text("{}\n", encoding="utf-8")
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.0f1\n", encoding="utf-8")
        return repo

    def run_launcher(self, kind: str, executable: str, repo: Path, project: str | None = None, *, dry_run: bool = False, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        if kind == "powershell":
            command = [executable, "-NoProfile", "-File", str(self.tool / "scripts/install.ps1"), "-Repo", str(repo)]
            if project:
                command += ["-Project", project]
            if dry_run:
                command += ["-DryRun"]
        else:
            command = [executable, str(self.tool / "scripts/install.sh"), "--repo", str(repo)]
            if project:
                command += ["--project", project]
            if dry_run:
                command += ["--dry-run"]
        return subprocess.run(command, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=check, env=env)

    def run_python(self, repo: Path, *, stage: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if stage:
            environment["LEMMINGS_INSTALL_FAIL_AFTER"] = stage
        return subprocess.run(
            [sys.executable, str(self.tool / "skills/lemmings/scripts/install.py"), "--repo", str(repo)],
            cwd=repo, capture_output=True, text=True, check=check, env=environment,
        )

    def test_launchers_replace_complete_bundle_and_installed_runtime_is_independent(self) -> None:
        for index, (kind, executable) in enumerate(launchers()):
            with self.subTest(installer=kind):
                repo = self.make_repo(f"игра {kind} {index}")
                agents = repo / ".codex/agents"
                agents.mkdir(parents=True)
                (agents / "foreign.toml").write_text("keep = true\n", encoding="utf-8")
                (agents / "lemmings-orchestrator.toml").write_text("obsolete\n", encoding="utf-8")
                profile = repo / ".agents/lemmings.json"
                profile.parent.mkdir(parents=True)
                profile.write_text('{"schemaVersion": 3, "mode": "strict"}\n', encoding="utf-8")

                completed = self.run_launcher(kind, executable, repo, "GameClient", check=False)
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn("installed and verified", completed.stdout)
                installed = json.loads(profile.read_text(encoding="utf-8"))
                self.assertEqual(4, installed["schemaVersion"])
                self.assertEqual("auto", installed["mode"])
                self.assertEqual(2, installed["orchestration"]["maxConcurrentWriters"])
                self.assertTrue((repo / ".agents/skills/lemmings/scripts/lemmings/invocations.py").is_file())
                self.assertFalse((agents / "lemmings-orchestrator.toml").exists())
                self.assertTrue((agents / "foreign.toml").is_file())


        repo = self.make_repo("source removed")
        self.run_python(repo)
        shutil.rmtree(self.tool)
        doctor = subprocess.run(
            [sys.executable, str(repo / ".agents/skills/lemmings/scripts/run.py"), "doctor", "--repo", str(repo)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, doctor.returncode, doctor.stdout + doctor.stderr)
        report = json.loads(doctor.stdout)
        self.assertEqual("4.1.1", report["data"]["runtimeVersion"])
        self.assertIn(str(repo / ".agents/skills/lemmings"), report["data"]["runtimePath"])
        entrypoint = repo / ".agents/skills/lemmings/scripts/run.py"
        inactive = subprocess.run(
            [sys.executable, str(entrypoint), "hook"], input=json.dumps({"cwd": str(repo), "hook_event_name": "SubagentStop"}),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, inactive.returncode, inactive.stdout + inactive.stderr)
        self.assertEqual({}, json.loads(inactive.stdout))
        broken = subprocess.run([sys.executable, str(entrypoint), "hook"], input="{", capture_output=True, text=True, check=False)
        self.assertNotEqual(0, broken.returncode)
        self.assertIn("invalid Lemmings hook input", broken.stdout)

    def test_reinstall_resets_settings_and_rolls_back_every_replacement_stage(self) -> None:
        repo = self.make_repo("rollback")
        self.run_python(repo)
        profile = repo / ".agents/lemmings.json"
        profile.write_text('{"custom": true}\n', encoding="utf-8")
        self.run_python(repo)
        self.assertNotIn("custom", json.loads(profile.read_text(encoding="utf-8")))

        skill = repo / ".agents/skills/lemmings/SKILL.md"
        agent = repo / ".codex/agents/lemmings-worker.toml"
        for stage in ("skill", "agents", "config"):
            skill.write_text(f"old skill {stage}\n", encoding="utf-8")
            agent.write_text(f"old agent {stage}\n", encoding="utf-8")
            profile.write_text(json.dumps({"old": stage}), encoding="utf-8")
            before = (skill.read_bytes(), agent.read_bytes(), profile.read_bytes())
            failed = self.run_python(repo, stage=stage, check=False)
            self.assertNotEqual(0, failed.returncode)
            self.assertEqual(before, (skill.read_bytes(), agent.read_bytes(), profile.read_bytes()))

    def test_active_runtime_and_busy_workspace_block_update(self) -> None:
        repo = self.make_repo("busy")
        common = repo / ".git/lemmings"
        common.mkdir(parents=True)
        marker = common / "active.json"
        marker.write_text('{"schemaVersion": 4}\n', encoding="utf-8")
        self.assertNotEqual(0, self.run_python(repo, check=False).returncode)
        marker.unlink()
        registry = common / "workspaces-v4.json"
        registry.write_text(json.dumps({"entries": [{"workspaceId": "W1", "state": "active"}]}), encoding="utf-8")
        self.assertNotEqual(0, self.run_python(repo, check=False).returncode)
        self.assertFalse((repo / ".agents/lemmings.json").exists())

    def test_dry_run_and_explicit_project_resolve_ambiguity(self) -> None:
        repo = self.make_repo("nested", ("GameOne", "GameTwo"))
        failed = self.run_python(repo, check=False)
        self.assertNotEqual(0, failed.returncode)
        process = subprocess.run(
            [sys.executable, str(self.tool / "skills/lemmings/scripts/install.py"), "--repo", str(repo), "--project", "GameTwo", "--dry-run"],
            cwd=repo / "GameOne", capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, process.returncode, process.stdout + process.stderr)
        self.assertFalse((repo / ".agents/lemmings.json").exists())
        process = subprocess.run(
            [sys.executable, str(self.tool / "skills/lemmings/scripts/install.py"), "--repo", str(repo), "--project", "GameTwo"],
            cwd=repo / "GameOne", capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, process.returncode, process.stdout + process.stderr)
        self.assertEqual("GameTwo", json.loads((repo / ".agents/lemmings.json").read_text(encoding="utf-8"))["game"]["projectPath"])

    def test_launcher_reports_missing_python(self) -> None:
        bash = find_bash()
        if not bash:
            self.skipTest("bash is unavailable")
        repo = self.make_repo("no-python")
        environment = os.environ.copy()
        git_root = Path(bash).parents[1]
        environment["PATH"] = os.pathsep.join(str(path) for path in (git_root / "cmd", git_root / "usr/bin", git_root / "mingw64/bin") if path.is_dir())
        probe = subprocess.run([bash, "-c", "command -v python3 || command -v python"], capture_output=True, text=True, env=environment)
        if probe.returncode == 0:
            self.skipTest("restricted shell still exposes Python")
        failed = self.run_launcher("bash", bash, repo, check=False, env=environment)
        self.assertNotEqual(0, failed.returncode)
        self.assertIn("requires Python 3.10", failed.stderr)


if __name__ == "__main__":
    unittest.main()
