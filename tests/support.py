"""Shared fixtures: hermetic environment and throwaway Git repositories."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "lemmings" / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "lemmings-telemetry"))

HOST_PREFIXES = ("ANTHROPIC", "CLAUDE", "OPENAI", "CODEX", "OPENCODE", "LEMMINGS", "GIT_")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


class HermeticTest(unittest.TestCase):
    """Isolate HOME and host-provider variables so the developer's setup cannot leak in."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lemmings-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith(HOST_PREFIXES)}
        home = self.tmp / "home"
        home.mkdir()
        env.update(HOME=str(home), USERPROFILE=str(home), GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_repo(self, name: str = "repo", files: dict[str, str] | None = None) -> Path:
        repo = self.tmp / name
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        for path, text in (files or {"README.md": "hello\n"}).items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init")
        return repo

    def commit(self, repo: Path, files: dict[str, str], message: str = "change") -> str:
        for path, text in files.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", message)
        return git(repo, "rev-parse", "HEAD")
