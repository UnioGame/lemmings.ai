"""Find the self-contained skill beside its runtime or in the target repository."""
from pathlib import Path


def skill_root(repo: Path | None = None) -> Path:
    source = Path(__file__).resolve().parents[2]
    candidates = [source]
    if repo is not None:
        candidates.append(repo.resolve() / ".agents/skills/lemmings")
    for candidate in candidates:
        if (candidate / "defaults.json").is_file() and (candidate / "SKILL.md").is_file():
            return candidate
    raise ValueError("Lemmings skill bundle is missing; install the repository skill before using this CLI")
