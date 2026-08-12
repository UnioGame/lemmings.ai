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

    def test_external_bootstrap_merges_profile_and_writes_environment(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"external-{kind}")
                profile_path = repo / ".codex/lemmings.json"
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

                self.run_installer(kind, executable, self.tool, cwd=repo)
                self.run_installer(kind, executable, self.tool, repo)

                profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
                self.assertEqual("strict", profile["mode"])
                self.assertEqual({"keep": True}, profile["unknown"])
                self.assertEqual("unity", profile["game"]["engine"])
                self.assertEqual("GameClient", profile["game"]["projectPath"])
                self.assertEqual(7, profile["game"]["workspace"]["maxUnityEditors"])
                self.assertEqual("hybrid", profile["game"]["workspace"]["parallelStrategy"])
                self.assertEqual("gpt-5.6-luna:max", profile["models"]["worker"])
                self.assertEqual("gpt-5.6-luna:high", profile["models"]["validator"])
                self.assertEqual("gpt-5.6-luna:high", profile["models"]["explorer"])
                self.assertEqual("gpt-5.6-luna:medium", profile["models"]["summarizer"])
                self.assertEqual("gpt-5.6-terra:max", profile["workerPolicy"]["elevatedModel"])
                self.assertNotIn("highRiskModel", profile["workerPolicy"])
                self.assertNotIn("complex-worker", profile["models"])
                self.assertNotIn("tooling", profile)
                self.assertTrue((repo / ".agents/skills/lemmings/SKILL.md").is_file())
                common_dir = subprocess.run(
                    ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                environment_path = (repo / common_dir / "lemmings/environment.json").resolve()
                environment = json.loads(environment_path.read_text(encoding="utf-8-sig"))
                self.assertEqual(1, environment["schemaVersion"])
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
                profile = json.loads((repo / ".codex/lemmings.json").read_text(encoding="utf-8-sig"))
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
                self.assertFalse((repo / ".codex/lemmings.json").exists())
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

    def test_rejects_package_cache_source(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"cache-{kind}")
                cached = self.root / f"cache-root-{kind}/Library/PackageCache/lemmings"
                shutil.copytree(self.tool, cached)
                result = self.run_installer(kind, executable, cached, repo, check=False)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Library/PackageCache", result.stderr + result.stdout)

    def test_rejects_retired_worker_role_in_existing_profile(self) -> None:
        for kind, executable in installers():
            with self.subTest(installer=kind):
                repo = self.make_repo(f"legacy-role-{kind}")
                profile = repo / ".codex/lemmings.json"
                profile.parent.mkdir()
                profile.write_text('{"schemaVersion":1,"models":{"complex-worker":"gpt-5.6-sol:medium"}}\n', encoding="utf-8")
                result = self.run_installer(kind, executable, self.tool, repo, check=False)
                self.assertNotEqual(0, result.returncode)
                output = result.stdout + result.stderr
                self.assertIn("complex-worker", output)
                self.assertIn("schema version 1", output)

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
        profile = json.loads((repo / ".codex/lemmings.json").read_text(encoding="utf-8-sig"))
        self.assertEqual("unity", profile["game"]["engine"])


if __name__ == "__main__":
    unittest.main()
