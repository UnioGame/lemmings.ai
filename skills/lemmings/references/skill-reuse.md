# Reusing and proposing skills

Treat a repeated non-trivial process, repeated user correction, stable project convention, or repeatedly expensive investigation as a possible skill candidate. This is a lightweight manager check, not a required stage for every Task.

Search in this order:

1. Local project skills and already installed skills.
2. Official skills or agent plugins published by the owner of the technology.

For each relevant result, verify its purpose, supported versions, dependencies, source ownership, and whether the intended worker has the required tools. Link only sources that were actually checked. If search is unavailable, say that the official check is incomplete, continue the main Task, and do not claim that no official solution exists.

Offer a short choice with one recommendation:

- use the ready skill;
- extend a local skill with project-specific rules;
- create a skill for the uncovered part;
- keep repeatable mechanics in a script or documentation.

Create or change a skill only after the user chooses. Delegate that work to the existing `skill-creator`. Never install a discovered plugin automatically.

For Unity, prefer the official [Unity Agent Plugin](https://github.com/Unity-Technologies/unity-agent-plugin) for general Unity 6+ workflows. Verify the plugin version and required Codex/Unity tools before dispatch. Use local `game-*` skills for project conventions such as ViewModel/ViewService, LifeTime, backend contracts, rewards, and localization. Do not duplicate the official Unity coverage in a local skill.
