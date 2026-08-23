import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/usr/bin/bash.exe",
        ):
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which("bash")


def installers() -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh:
        values.append(("powershell", pwsh))
    bash = find_bash()
    if bash:
        values.append(("bash", bash))
    return values


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tool = self.root / "lemmings-tool"
        shutil.copytree(PACKAGE_ROOT / "scripts", self.tool / "scripts")
        shutil.copytree(PACKAGE_ROOT / "skills", self.tool / "skills")
        shutil.copytree(PACKAGE_ROOT / "agents", self.tool / "agents")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_repo(self, name: str = "game-repo", projects: tuple[str, ...] = ("GameClient",)) -> Path:
        repo = self.root / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for relative in projects:
            project = repo / relative
            (project / "Assets").mkdir(parents=True)
            (project / "Packages").mkdir()
            (project / "Packages/manifest.json").write_text("{}\n", encoding="utf-8")
            (project / "ProjectSettings").mkdir()
            (project / "ProjectSettings/ProjectVersion.txt").write_text(
                "m_EditorVersion: 6000.0.0f1\n", encoding="utf-8"
            )
        return repo

    def run_installer(
        self,
        kind: str,
        executable: str,
        tool: Path,
        repo: Path | None = None,
        project: str | None = None,
        *extra: str,
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if kind == "powershell":
            command = [executable, "-NoProfile", "-File", str(tool / "scripts/install.ps1")]
            if repo is not None:
                command.extend(["-Repo", str(repo)])
            if project is not None:
                command.extend(["-Project", project])
            command.extend("-" + value.title().replace("-", "") for value in extra)
        else:
            command = [executable, str(tool / "scripts/install.sh")]
            if repo is not None:
                command.extend(["--repo", str(repo)])
            if project is not None:
                command.extend(["--project", project])
            command.extend("--" + value for value in extra)
        return subprocess.run(
            command,
            cwd=cwd or self.root,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

    def test_external_bootstrap_refuses_drift_then_replaces_bundle(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"external-{kind}")
                profile_path = repo / ".agents/lemmings.json"
                profile_path.parent.mkdir()
                profile_path.write_text(
                    json.dumps(
                        {
                            "mode": "strict",
                            "unknown": {"keep": True},
                            "game": {"workspace": {"maxUnityEditors": 7}},
                        }
                    ),
                    encoding="utf-8",
                )

                refused = self.run_installer(kind, executable, self.tool, cwd=repo, check=False)
                self.assertNotEqual(0, refused.returncode)
                self.run_installer(kind, executable, self.tool, repo, None, "force")
                repeated = self.run_installer(kind, executable, self.tool, repo, check=False)
                self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)

                profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
                self.assertEqual(3, profile["schemaVersion"])
                self.assertEqual("3.2.0", profile["distributionVersion"])
                self.assertEqual("auto", profile["mode"])
                self.assertNotIn("unknown", profile)
                self.assertEqual("unity", profile["game"]["engine"])
                self.assertEqual("GameClient", profile["game"]["projectPath"])
                self.assertEqual(1, profile["game"]["workspace"]["maxUnityEditors"])
                self.assertEqual("hybrid", profile["game"]["workspace"]["parallelStrategy"])
                self.assertEqual("gpt-5.6-luna", profile["modelRoutes"]["codex"]["worker"][0]["modelId"])
                self.assertEqual("gpt-5.6-sol", profile["modelRoutes"]["codex"]["reviewer"][0]["modelId"])
                self.assertEqual("gpt-5.6-luna", profile["modelRoutes"]["codex"]["explorer"][0]["modelId"])
                self.assertEqual(2, profile["orchestration"]["maxConcurrentWriters"])
                self.assertEqual(2, profile["workspacePool"]["maxIdle"])
                self.assertNotIn("tooling", profile)
                self.assertEqual({"maxPacketBytes": 16384, "maxWorkingSetItems": 12, "maxExpansions": 1}, profile["contextPolicy"])
                self.assertTrue((repo / ".agents/skills/lemmings/SKILL.md").is_file())
                self.assertEqual(
                    sorted(path.name for path in (self.tool / "agents").glob("lemmings-*.toml")),
                    sorted(path.name for path in (repo / ".codex/agents").glob("lemmings-*.toml")),
                )
                common_dir = subprocess.run(
                    ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                environment_path = (repo / common_dir / "lemmings/environment.json").resolve()
                environment = json.loads(environment_path.read_text(encoding="utf-8-sig"))
                self.assertEqual(3, environment["schemaVersion"])
                self.assertEqual(self.tool.resolve(), Path(environment["toolRoot"]).resolve())
                staged = subprocess.run(
                    ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                self.assertEqual("", staged)
                self.assertFalse((repo / "hooks").exists())

    def test_embedded_bootstrap_uses_repo_relative_tooling_root(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"embedded-{kind}")
                embedded = repo / "GameClient/Game.Packages/lemmings"
                shutil.copytree(self.tool, embedded)
                self.run_installer(kind, executable, embedded, cwd=repo)
                profile = json.loads((repo / ".agents/lemmings.json").read_text(encoding="utf-8-sig"))
                self.assertEqual("GameClient/Game.Packages/lemmings", profile["tooling"]["root"])
                self.assertFalse((repo / ".git/lemmings/environment.json").exists())

    def test_dry_run_force_and_ambiguous_project_handling(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"controls-{kind}", ("GameOne", "GameTwo"))
                ambiguous = self.run_installer(kind, executable, self.tool, repo, check=False)
                self.assertNotEqual(0, ambiguous.returncode)
                self.assertIn("Multiple Unity game projects", ambiguous.stderr + ambiguous.stdout)

                self.run_installer(kind, executable, self.tool, repo, "GameOne", "dry-run")
                self.assertFalse((repo / ".agents/lemmings.json").exists())
                self.assertFalse((repo / ".agents/skills/lemmings").exists())

                self.run_installer(kind, executable, self.tool, repo, "GameOne")
                target_skill = repo / ".agents/skills/lemmings/SKILL.md"
                target_skill.write_text("user change\n", encoding="utf-8")
                refused = self.run_installer(kind, executable, self.tool, repo, "GameOne", check=False)
                self.assertNotEqual(0, refused.returncode)
                self.run_installer(kind, executable, self.tool, repo, "GameOne", "force")
                self.assertEqual(
                    (self.tool / "skills/lemmings/SKILL.md").read_bytes(), target_skill.read_bytes()
                )

    def test_recognized_v2_bundle_is_replaced_without_force_and_data_is_preserved(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"legacy-{kind}")
                legacy_profile = repo / ".codex/lemmings.json"
                legacy_profile.parent.mkdir(parents=True)
                legacy_profile.write_text('{"schemaVersion": 2, "mode": "strict"}\n', encoding="utf-8")
                legacy_skill = repo / ".agents/skills/lemmings"
                legacy_skill.mkdir(parents=True)
                (legacy_skill / "SKILL.md").write_text("legacy\n", encoding="utf-8")
                agents = repo / ".codex/agents"
                agents.mkdir(parents=True)
                for name in ("worker", "reviewer", "explorer", "orchestrator", "validator", "summarizer"):
                    (agents / f"lemmings-{name}.toml").write_text(f"name = '{name}'\n", encoding="utf-8")
                foreign = agents / "foreign.toml"
                foreign.write_text("keep = true\n", encoding="utf-8")
                common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"], check=True, capture_output=True, text=True).stdout.strip()
                common_path = (repo / common).resolve()
                marker = common_path / "lemmings/active.json"
                marker.parent.mkdir(parents=True)
                marker.write_text('{"schemaVersion": 2}\n', encoding="utf-8")
                registry = common_path / "lemmings/workspaces-v3.json"
                registry.write_text('{"revision": 9}\n', encoding="utf-8")
                history = repo / "docs/tasks/history.json"
                history.parent.mkdir(parents=True)
                history.write_text('{"schemaVersion": 2}\n', encoding="utf-8")

                self.run_installer(kind, executable, self.tool, repo)

                self.assertFalse(legacy_profile.exists())
                self.assertFalse(marker.exists())
                self.assertEqual('{"revision": 9}\n', registry.read_text(encoding="utf-8"))
                self.assertEqual('{"schemaVersion": 2}\n', history.read_text(encoding="utf-8"))
                self.assertEqual("keep = true\n", foreign.read_text(encoding="utf-8"))
                self.assertEqual(
                    ["lemmings-explorer.toml", "lemmings-reviewer.toml", "lemmings-worker.toml"],
                    sorted(path.name for path in agents.glob("lemmings-*.toml")),
                )
                installed = json.loads((repo / ".agents/lemmings.json").read_text(encoding="utf-8-sig"))
                self.assertEqual(3, installed["schemaVersion"])
                self.assertEqual("auto", installed["mode"])
                marker.write_text('{"schemaVersion": 3, "taskPath": "docs/tasks/current.json"}\n', encoding="utf-8")
                self.run_installer(kind, executable, self.tool, repo)
                self.assertEqual(3, json.loads(marker.read_text(encoding="utf-8"))["schemaVersion"])
                installed["mode"] = "strict"
                (repo / ".agents/lemmings.json").write_text(json.dumps(installed), encoding="utf-8")
                legacy_profile.parent.mkdir(parents=True, exist_ok=True)
                legacy_profile.write_text('{"schemaVersion": 2}\n', encoding="utf-8")
                refused = self.run_installer(kind, executable, self.tool, repo, check=False)
                self.assertNotEqual(0, refused.returncode)

    def test_failed_v2_replacement_restores_legacy_config_marker_and_roles(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"legacy-rollback-{kind}")
                legacy_profile = repo / ".codex/lemmings.json"
                legacy_profile.parent.mkdir(parents=True)
                legacy_profile.write_text('{"schemaVersion": 2, "sentinel": true}\n', encoding="utf-8")
                agents = repo / ".codex/agents"
                agents.mkdir(parents=True)
                for name in ("worker", "reviewer", "explorer", "orchestrator", "validator", "summarizer"):
                    (agents / f"lemmings-{name}.toml").write_text(f"legacy = '{name}'\n", encoding="utf-8")
                common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"], check=True, capture_output=True, text=True).stdout.strip()
                marker = (repo / common / "lemmings/active.json").resolve()
                marker.parent.mkdir(parents=True)
                marker.write_text('{"schemaVersion": 2, "enabled": true}\n', encoding="utf-8")
                before_agents = {path.name: path.read_bytes() for path in agents.glob("lemmings-*.toml")}
                environment = os.environ.copy()
                environment["LEMMINGS_INSTALL_FAIL_AFTER"] = "config"

                failed = self.run_installer(kind, executable, self.tool, repo, check=False, env=environment)
                self.assertNotEqual(0, failed.returncode)
                self.assertEqual('{"schemaVersion": 2, "sentinel": true}\n', legacy_profile.read_text(encoding="utf-8"))
                self.assertEqual('{"schemaVersion": 2, "enabled": true}\n', marker.read_text(encoding="utf-8"))
                self.assertEqual(before_agents, {path.name: path.read_bytes() for path in agents.glob("lemmings-*.toml")})
                self.assertFalse((repo / ".agents/lemmings.json").exists())

    def test_force_replacement_rolls_back_owned_targets(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"rollback-{kind}")
                self.run_installer(kind, executable, self.tool, repo)
                foreign_profile = repo / ".codex/agents/foreign.toml"
                foreign_profile.write_text("name = 'foreign'\n", encoding="utf-8")
                skill_file = repo / ".agents/skills/lemmings/SKILL.md"
                skill_file.write_text("drift\n", encoding="utf-8")
                profile_path = repo / ".agents/lemmings.json"
                profile_path.write_text('{"drift": true}\n', encoding="utf-8")
                before_skill = skill_file.read_bytes()
                before_profile = profile_path.read_bytes()
                before_agents = {
                    path.name: path.read_bytes()
                    for path in (repo / ".codex/agents").glob("lemmings-*.toml")
                }
                environment = os.environ.copy()
                environment["LEMMINGS_INSTALL_FAIL_AFTER"] = "agents"
                failed = self.run_installer(
                    kind, executable, self.tool, repo, None, "force", check=False, env=environment
                )
                self.assertNotEqual(0, failed.returncode, failed.stdout + failed.stderr)
                self.assertEqual(before_skill, skill_file.read_bytes())
                self.assertEqual(before_profile, profile_path.read_bytes())
                self.assertEqual("name = 'foreign'\n", foreign_profile.read_text(encoding="utf-8"))
                self.assertEqual(
                    before_agents,
                    {
                        path.name: path.read_bytes()
                        for path in (repo / ".codex/agents").glob("lemmings-*.toml")
                    },
                )

    def test_rejects_package_cache_source(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"cache-{kind}")
                cached = self.root / f"cache-root-{kind}/Library/PackageCache/lemmings"
                shutil.copytree(self.tool, cached)
                result = self.run_installer(kind, executable, cached, repo, check=False)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Library/PackageCache", result.stderr + result.stdout)

    def test_bash_bootstrap_does_not_require_python_on_path(self) -> None:
        bash = find_bash()
        if not bash:
            self.skipTest("bash is unavailable")
        repo = self.make_repo("no-python")
        if os.name == "nt":
            git_root = Path(bash).parents[1]
            path_entries = [git_root / "cmd", git_root / "usr/bin", git_root / "mingw64/bin"]
            system_root = os.environ.get("SystemRoot")
            if system_root:
                path_entries.append(Path(system_root) / "System32")
            restricted_path = os.pathsep.join(str(path) for path in path_entries if path.is_dir())
        else:
            command_bin = self.root / "no-python-bin"
            command_bin.mkdir()
            for command in (
                "awk", "basename", "cmp", "cp", "diff", "dirname", "find", "git",
                "mkdir", "mktemp", "mv", "rm", "sed",
            ):
                source = shutil.which(command)
                if source:
                    (command_bin / command).symlink_to(source)
            restricted_path = str(command_bin)
        environment = os.environ.copy()
        environment["PATH"] = restricted_path
        probe = subprocess.run(
            [bash, "-c", "command -v python3 || command -v python"],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertNotEqual(0, probe.returncode, probe.stdout)

        self.run_installer("bash", bash, self.tool, repo, env=environment)
        profile = json.loads((repo / ".agents/lemmings.json").read_text(encoding="utf-8-sig"))
        self.assertEqual("unity", profile["game"]["engine"])


if __name__ == "__main__":
    unittest.main()
