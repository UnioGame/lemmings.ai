from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def find_bash() -> str | None:
    candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    if os.name == "nt" and candidate.is_file():
        return str(candidate)
    return shutil.which("bash")


class HookWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        common = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.marker = Path(common) / "lemmings/active.json"
        self.payload = json.dumps({"cwd": str(self.repo), "hook_event_name": "PreToolUse", "tool_name": "Read"})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def restricted_path(self, bash: str, *extra: Path) -> str:
        git_root = Path(bash).resolve().parents[1]
        paths = [*extra, git_root / "cmd", git_root / "usr/bin", git_root / "mingw64/bin"]
        return os.pathsep.join(str(path) for path in paths if path.is_dir())

    def test_inactive_posix_hook_does_not_probe_available_python(self) -> None:
        bash = find_bash()
        if not bash:
            self.skipTest("bash is unavailable")
        fake_bin = Path(self.temporary.name) / "fake-bin"
        fake_bin.mkdir()
        sentinel = Path(self.temporary.name) / "python-called"
        fake = fake_bin / "python3"
        fake.write_text(f"#!/usr/bin/env sh\nprintf called > '{sentinel.as_posix()}'\nexit 99\n", encoding="utf-8")
        fake.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = self.restricted_path(bash, fake_bin)

        completed = subprocess.run(
            [bash, str(ROOT / "hooks/run.sh")],
            cwd=self.repo,
            input=self.payload,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual({}, json.loads(completed.stdout))
        self.assertFalse(sentinel.exists(), "inactive hook must not probe Python")

    def test_active_posix_hook_fails_explicitly_without_python(self) -> None:
        bash = find_bash()
        if not bash:
            self.skipTest("bash is unavailable")
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('{"schemaVersion":5}\n', encoding="utf-8")
        environment = os.environ.copy()
        environment["PATH"] = self.restricted_path(bash)

        completed = subprocess.run(
            [bash, str(ROOT / "hooks/run.sh")],
            cwd=self.repo,
            input=self.payload,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("runtime is active", completed.stderr)

    def test_powershell_wrapper_noops_inactive_and_forwards_active_payload(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "hooks/run.ps1")]
        inactive = subprocess.run(command, cwd=self.repo, input=self.payload, capture_output=True, text=True, check=False)
        self.assertEqual(0, inactive.returncode, inactive.stdout + inactive.stderr)
        self.assertEqual({}, json.loads(inactive.stdout))

        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('{"schemaVersion":5}\n', encoding="utf-8")
        active = subprocess.run(command, cwd=self.repo, input="{", capture_output=True, text=True, check=False)
        self.assertNotEqual(0, active.returncode)
        self.assertIn("invalid Lemmings hook input", active.stdout + active.stderr)

        git = shutil.which("git")
        if git:
            environment = os.environ.copy()
            environment["PATH"] = str(Path(git).parent)
            missing = subprocess.run(
                command,
                cwd=self.repo,
                input=self.payload,
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("runtime is active", missing.stdout + missing.stderr)

    def test_manifest_uses_python_optional_wrappers(self) -> None:
        manifest = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
        commands = [hook for entries in manifest["hooks"].values() for entry in entries for hook in entry["hooks"]]
        self.assertTrue(commands)
        self.assertTrue(all("hooks/run.sh" in hook["command"] for hook in commands))
        self.assertTrue(all("hooks/run.ps1" in hook["commandWindows"] for hook in commands))
        self.assertTrue(all("python" not in hook["command"].lower() for hook in commands))


if __name__ == "__main__":
    unittest.main()
