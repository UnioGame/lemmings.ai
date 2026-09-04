#!/usr/bin/env bash
set -euo pipefail

repo_arg=""
project_arg=""
dry_run=0
force=0

usage() {
  echo "Usage: $0 [--repo PATH] [--project PATH] [--dry-run] [--force]" >&2
}

while (($#)); do
  case "$1" in
    --repo) [[ $# -ge 2 ]] || { usage; exit 2; }; repo_arg=$2; shift 2 ;;
    --project) [[ $# -ge 2 ]] || { usage; exit 2; }; project_arg=$2; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --force) force=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

absolute_path() {
  local path=$1 base=${2:-$PWD}
  if [[ "$path" =~ ^[A-Za-z]:[\\/] ]] && command -v cygpath >/dev/null 2>&1; then
    path=$(cygpath -u "$path")
  fi
  if [[ "$base" =~ ^[A-Za-z]:[\\/] ]] && command -v cygpath >/dev/null 2>&1; then
    base=$(cygpath -u "$base")
  fi
  if [[ "$path" != /* ]]; then path="$base/$path"; fi
  local directory name
  directory=$(dirname "$path")
  name=$(basename "$path")
  (cd "$directory" 2>/dev/null && printf '%s/%s\n' "$PWD" "$name")
}

git_value() {
  local at=$1; shift
  git -C "$at" "$@" 2>/dev/null || true
}

git_root() {
  local at=$1
  [[ -d "$at" ]] || return 0
  git_value "$at" rev-parse --show-toplevel
}

is_within() {
  local child parent
  child=$(absolute_path "$1")
  parent=$(absolute_path "$2")
  [[ "$child" == "$parent" || "$child" == "$parent/"* ]]
}

relative_path() {
  local from to common suffix prefix=""
  from=$(absolute_path "$1")
  to=$(absolute_path "$2")
  if [[ "$from" == "$to" ]]; then printf '.\n'; return; fi
  common=$from
  while [[ "$to" != "$common/"* ]]; do
    [[ "$common" != / ]] || { echo "Cannot make paths relative: $from and $to" >&2; return 1; }
    common=$(dirname "$common")
    prefix="../$prefix"
  done
  suffix=${to#"$common/"}
  printf '%s%s\n' "$prefix" "$suffix"
}

json_escape() {
  printf '%s' "$1" | awk 'BEGIN { ORS="" } { if (NR > 1) printf "\\n"; gsub(/\\/, "\\\\"); gsub(/\"/, "\\\""); gsub(/\t/, "\\t"); gsub(/\r/, "\\r"); printf "%s", $0 }'
}

schema_is() {
  local file=$1 version=$2
  [[ -f "$file" ]] && grep -Eq '"schemaVersion"[[:space:]]*:[[:space:]]*'"$version"'([,}[:space:]]|$)' "$file"
}

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
package_root=$(cd "$script_dir/.." && pwd)
package_root_lower=${package_root,,}
case "/${package_root_lower//\\//}/" in
  */library/packagecache/*) echo "Refusing to install from Unity's Library/PackageCache: $package_root" >&2; exit 1 ;;
esac
skill_source="$package_root/skills/lemmings"
agents_source="$package_root/agents"
[[ -f "$skill_source/SKILL.md" ]] || { echo "Lemmings skill source is missing: $skill_source" >&2; exit 1; }

if [[ -n "$repo_arg" ]]; then
  repo_candidate=$(absolute_path "$repo_arg")
  repo_root=$(git_root "$repo_candidate")
else
  superproject=$(git_value "$package_root" rev-parse --show-superproject-working-tree)
  package_git_root=$(git_root "$package_root")
  cwd_git_root=$(git_root "$PWD")
  if [[ -n "$superproject" ]]; then
    repo_root=$(absolute_path "$superproject")
  elif [[ -n "$package_git_root" && "$(absolute_path "$package_git_root")" != "$package_root" ]]; then
    repo_root=$(absolute_path "$package_git_root")
  elif [[ -n "$cwd_git_root" && ( -z "$package_git_root" || "$(absolute_path "$cwd_git_root")" != "$(absolute_path "$package_git_root")" ) ]]; then
    repo_root=$(absolute_path "$cwd_git_root")
  else
    repo_root=$package_git_root
  fi
fi
[[ -n "${repo_root:-}" ]] || { echo "Cannot infer a consumer Git repository. Pass --repo PATH." >&2; exit 1; }
repo_root=$(absolute_path "$repo_root")
common_dir=$(git_value "$repo_root" rev-parse --git-common-dir)
[[ -n "$common_dir" ]] || { echo "Cannot resolve Git common directory for '$repo_root'." >&2; exit 1; }
common_path=$(absolute_path "$common_dir" "$repo_root")
is_unity_project() {
  [[ -d "$1/Assets" && -f "$1/Packages/manifest.json" && -f "$1/ProjectSettings/ProjectVersion.txt" ]]
}

if [[ -n "$project_arg" ]]; then
  project_path=$(absolute_path "$project_arg" "$repo_root")
  is_unity_project "$project_path" || { echo "Not a Unity game project (required: Assets, Packages/manifest.json, ProjectSettings/ProjectVersion.txt): $project_path" >&2; exit 1; }
else
  projects=()
  while IFS= read -r -d '' manifest; do
    candidate=$(dirname "$(dirname "$manifest")")
    is_unity_project "$candidate" && projects+=("$(absolute_path "$candidate")")
  done < <(find "$repo_root" -type d \( -name .git -o -name Library -o -name PackageCache \) -prune -o -path '*/Packages/manifest.json' -type f -print0)
  if ((${#projects[@]} == 0)); then echo "No Unity game project found under '$repo_root'. Pass --project PATH." >&2; exit 1; fi
  if ((${#projects[@]} > 1)); then echo "Multiple Unity game projects found under '$repo_root'. Pass --project PATH." >&2; exit 1; fi
  project_path=${projects[0]}
fi
is_within "$project_path" "$repo_root" || { echo "Unity project must be inside the consumer repository: $project_path" >&2; exit 1; }

repo_name=$(basename "$repo_root")
project_relative=$(json_escape "$(relative_path "$repo_root" "$project_path")")
defaults_file=$(mktemp)
trap 'rm -f -- "$defaults_file"' EXIT
cat >"$defaults_file" <<JSON
{
  "schemaVersion": 3,
  "distributionVersion": "3.3.0",
  "mode": "auto",
  "roadmap": "docs/tasks/ROADMAP.md",
  "taskGlobs": ["docs/tasks/**/*.json"],
  "reviewGlobs": ["docs/tasks/reviews/*.json"],
  "worktreeRoot": "../lemmings-worktrees",
  "modelRoutes": {
    "codex": {
      "worker": [
        { "providerId": "openai", "modelId": "gpt-5.6-luna", "variantId": "max", "specializations": ["default"] },
        { "providerId": "openai", "modelId": "gpt-5.6-terra", "variantId": "max", "specializations": ["default"] }
      ],
      "reviewer": [{ "providerId": "openai", "modelId": "gpt-5.6-sol", "variantId": "high", "specializations": ["default"] }],
      "explorer": [{ "providerId": "openai", "modelId": "gpt-5.6-luna", "variantId": "high", "specializations": ["default"] }]
    }
  },
  "contextPolicy": { "maxPacketBytes": 16384, "maxWorkingSetItems": 12, "maxExpansions": 1 },
  "orchestration": { "maxDelegationDepth": 1, "maxConcurrentWriters": 2, "maxConcurrentReaders": 2, "managerSlots": 1, "maxRepairs": 1, "maxTransportRetries": 1 },
  "workspacePool": { "enabled": true, "maxIdle": 2, "maxIdleGiB": 10, "eviction": "lru" },
  "game": {
    "engine": "unity",
    "projectPath": "$project_relative",
    "workspace": {
      "policy": "auto",
      "parallelStrategy": "hybrid",
      "largeThresholdGiB": 10,
      "validationBackend": "clone",
      "validationPath": "../$repo_name.lemmings.validation"
    }
  }
}
JSON

package_inside_repo=0
if is_within "$package_root" "$repo_root"; then
  package_inside_repo=1
  tooling_root=$(json_escape "$(relative_path "$repo_root" "$package_root")")
  defaults_without_closing=$(mktemp)
  sed '$d' "$defaults_file" >"$defaults_without_closing"
  printf ',\n  "tooling": { "root": "%s" }\n}\n' "$tooling_root" >>"$defaults_without_closing"
  mv -f -- "$defaults_without_closing" "$defaults_file"
fi
skill_target="$repo_root/.agents/skills/lemmings"
agents_target="$repo_root/.codex/agents"
profile_target="$repo_root/.agents/lemmings.json"
legacy_profile_target="$repo_root/.codex/lemmings.json"
active_marker_target="$common_path/lemmings/active.json"
environment_target="$common_path/lemmings/environment.json"
shopt -s nullglob
source_agents=("$agents_source"/lemmings-*.toml)
current_agent_names=(lemmings-worker.toml lemmings-reviewer.toml lemmings-explorer.toml)
obsolete_agent_names=(lemmings-orchestrator.toml lemmings-validator.toml lemmings-summarizer.toml)
owned_agent_names=("${current_agent_names[@]}" "${obsolete_agent_names[@]}")
installed_agents=()
for name in "${owned_agent_names[@]}"; do [[ -f "$agents_target/$name" ]] && installed_agents+=("$agents_target/$name"); done
legacy_recognized=0
if ! schema_is "$profile_target" 3; then
  schema_is "$legacy_profile_target" 2 && legacy_recognized=1
  schema_is "$active_marker_target" 2 && legacy_recognized=1
  for name in "${obsolete_agent_names[@]}"; do [[ -f "$agents_target/$name" ]] && legacy_recognized=1; done
fi
skill_drift=0
[[ -d "$skill_target" ]] && ! diff -qr --strip-trailing-cr "$skill_source" "$skill_target" >/dev/null 2>&1 && skill_drift=1
agent_drift=0
if ((${#installed_agents[@]})); then
  installed_current=()
  for name in "${current_agent_names[@]}"; do [[ -f "$agents_target/$name" ]] && installed_current+=("$agents_target/$name"); done
  ((${#source_agents[@]} == ${#installed_current[@]})) || agent_drift=1
  for name in "${obsolete_agent_names[@]}"; do [[ -f "$agents_target/$name" ]] && agent_drift=1; done
  for source_agent in "${source_agents[@]}"; do
    target_agent="$agents_target/$(basename "$source_agent")"
    [[ -f "$target_agent" ]] && cmp -s "$source_agent" "$target_agent" || agent_drift=1
  done
fi
config_drift=0
[[ -f "$profile_target" ]] && ! cmp -s "$profile_target" "$defaults_file" && config_drift=1
bundle_present=0
legacy_targets_present=0
[[ -f "$legacy_profile_target" ]] && legacy_targets_present=1
schema_is "$active_marker_target" 2 && legacy_targets_present=1
for name in "${obsolete_agent_names[@]}"; do [[ -f "$agents_target/$name" ]] && legacy_targets_present=1; done
[[ -d "$skill_target" || -f "$profile_target" || ${#installed_agents[@]} -gt 0 || $legacy_targets_present -eq 1 ]] && bundle_present=1
bundle_drift=0
(( skill_drift || agent_drift || config_drift )) && bundle_drift=1
if (( bundle_present )) && [[ ! -d "$skill_target" || ! -f "$profile_target" || ${#installed_agents[@]} -eq 0 ]]; then bundle_drift=1; fi
if (( bundle_drift && ! force && ! legacy_recognized )); then
  echo "Lemmings bundle differs from the canonical 3.3 distribution. Re-run with --force to replace it." >&2
  exit 1
fi
environment_needs_change=0
if (( package_inside_repo )); then
  schema_is "$environment_target" 2 && environment_needs_change=1
  schema_is "$environment_target" 3 && environment_needs_change=1
else
  schema_is "$environment_target" 3 || environment_needs_change=1
  if (( ! environment_needs_change )); then grep -Fq "$(json_escape "$package_root")" "$environment_target" || environment_needs_change=1; fi
fi
replacement_needed=0
(( ! bundle_present || bundle_drift || legacy_targets_present || environment_needs_change || force )) && replacement_needed=1

if (( dry_run )); then
  echo "replace: $skill_target"
  echo "replace: $profile_target"
  for name in "${current_agent_names[@]}"; do echo "replace: $agents_target/$name"; done
  [[ -e "$legacy_profile_target" ]] && echo "delete: $legacy_profile_target"
  schema_is "$active_marker_target" 2 && echo "delete: $active_marker_target"
  for name in "${obsolete_agent_names[@]}"; do [[ -e "$agents_target/$name" ]] && echo "delete: $agents_target/$name"; done
  if (( package_inside_repo )); then echo "delete if Lemmings-owned: $environment_target"; else echo "replace: $environment_target"; fi
elif (( replacement_needed )); then
  transaction=$(mktemp -d "$repo_root/.lemmings-install.XXXXXX")
  stage="$transaction/stage"
  backup="$transaction/backup"
  had_skill=0; [[ -d "$skill_target" ]] && had_skill=1
  had_config=0; [[ -f "$profile_target" ]] && had_config=1
  had_legacy_config=0; [[ -f "$legacy_profile_target" ]] && had_legacy_config=1
  had_active=0; [[ -f "$active_marker_target" ]] && had_active=1
  had_environment=0; [[ -f "$environment_target" ]] && had_environment=1
  injected_failure=0
  rollback() {
    rm -rf -- "$skill_target"
    (( had_skill )) && mv -- "$backup/skill" "$skill_target"
    for name in "${owned_agent_names[@]}"; do rm -f -- "$agents_target/$name"; done
    [[ -d "$backup/agents" ]] && { mkdir -p "$agents_target"; cp "$backup/agents"/* "$agents_target/"; }
    rm -f -- "$profile_target"
    (( had_config )) && mv -- "$backup/lemmings.json" "$profile_target"
    rm -f -- "$legacy_profile_target"
    (( had_legacy_config )) && { mkdir -p "$(dirname "$legacy_profile_target")"; mv -- "$backup/legacy-lemmings.json" "$legacy_profile_target"; }
    rm -f -- "$active_marker_target"
    (( had_active )) && { mkdir -p "$(dirname "$active_marker_target")"; mv -- "$backup/active.json" "$active_marker_target"; }
    rm -f -- "$environment_target"
    (( had_environment )) && { mkdir -p "$(dirname "$environment_target")"; mv -- "$backup/environment.json" "$environment_target"; }
  }
  if ! {
    mkdir -p "$stage/skill" "$stage/agents" "$backup"
    cp -R "$skill_source/." "$stage/skill/"
    cp "${source_agents[@]}" "$stage/agents/"
    cp "$defaults_file" "$stage/lemmings.json"
    (( had_skill )) && cp -R "$skill_target" "$backup/skill"
    if ((${#installed_agents[@]})); then mkdir -p "$backup/agents"; cp "${installed_agents[@]}" "$backup/agents/"; fi
    (( had_config )) && cp "$profile_target" "$backup/lemmings.json"
    (( had_legacy_config )) && cp "$legacy_profile_target" "$backup/legacy-lemmings.json"
    (( had_active )) && cp "$active_marker_target" "$backup/active.json"
    (( had_environment )) && cp "$environment_target" "$backup/environment.json"
    rm -rf -- "$skill_target"; mkdir -p "$(dirname "$skill_target")"; mv "$stage/skill" "$skill_target"
    [[ "${LEMMINGS_INSTALL_FAIL_AFTER:-}" != skill ]] || { echo "Injected failure after skill replacement." >&2; injected_failure=1; }
    mkdir -p "$agents_target"; for name in "${owned_agent_names[@]}"; do rm -f -- "$agents_target/$name"; done; cp "$stage/agents"/* "$agents_target/"
    [[ "${LEMMINGS_INSTALL_FAIL_AFTER:-}" != agents ]] || { echo "Injected failure after agent replacement." >&2; injected_failure=1; }
    mkdir -p "$(dirname "$profile_target")"; rm -f -- "$profile_target"; mv "$stage/lemmings.json" "$profile_target"
    rm -f -- "$legacy_profile_target"
    schema_is "$active_marker_target" 2 && rm -f -- "$active_marker_target"
    if (( package_inside_repo )); then
      if schema_is "$environment_target" 2 || schema_is "$environment_target" 3; then rm -f -- "$environment_target"; fi
    else
      package_root_json=$package_root
      if command -v cygpath >/dev/null 2>&1; then package_root_json=$(cygpath -w "$package_root"); fi
      mkdir -p "$(dirname "$environment_target")"
      printf '{"schemaVersion": 3, "toolRoot": "%s"}\n' "$(json_escape "$package_root_json")" >"$environment_target"
    fi
    [[ "${LEMMINGS_INSTALL_FAIL_AFTER:-}" != config ]] || { echo "Injected failure after config replacement." >&2; injected_failure=1; }
    diff -qr --strip-trailing-cr "$skill_source" "$skill_target" >/dev/null 2>&1 || { echo "Installed skill failed distribution validation." >&2; injected_failure=1; }
    cmp -s "$profile_target" "$defaults_file" || { echo "Installed profile failed distribution validation." >&2; injected_failure=1; }
    for source_agent in "${source_agents[@]}"; do cmp -s "$source_agent" "$agents_target/$(basename "$source_agent")" || { echo "Installed agent failed distribution validation: $(basename "$source_agent")" >&2; injected_failure=1; }; done
    [[ ! -f "$legacy_profile_target" ]] || { echo "Legacy Lemmings targets remain after installation." >&2; injected_failure=1; }
    schema_is "$active_marker_target" 2 && { echo "Legacy Lemmings active marker remains after installation." >&2; injected_failure=1; }
    for name in "${obsolete_agent_names[@]}"; do [[ ! -f "$agents_target/$name" ]] || { echo "Legacy Lemmings target remains: $name" >&2; injected_failure=1; }; done
    (( ! injected_failure ))
  }; then
    rollback
    rm -rf -- "$transaction"
    exit 1
  fi
  rm -rf -- "$transaction"
fi

if (( dry_run )); then echo "Lemmings bootstrap dry run complete."; else echo "Lemmings skill bootstrap complete."; fi
