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

merge_json() {
  local target=$1 defaults=$2 set_tool_root=${3:-0}
  local merged
  merged=$(mktemp)
  if ! awk -v current_file="$target" -v defaults_file="$defaults" -v set_tool_root="$set_tool_root" '
    function read_file(path,    line, value, status) {
      value = ""
      while ((status = getline line < path) > 0) value = value line "\n"
      close(path)
      return value
    }
    function skip_ws() { while (pos <= length(source) && substr(source, pos, 1) ~ /[ \t\r\n]/) pos++ }
    function parse_string(    start, escaped, ch) {
      start = pos++
      escaped = 0
      while (pos <= length(source)) {
        ch = substr(source, pos++, 1)
        if (escaped) escaped = 0
        else if (ch == "\\") escaped = 1
        else if (ch == "\"") return substr(source, start, pos - start)
      }
      parse_error = "unterminated JSON string"
      return ""
    }
    function key_name(raw,    value) {
      value = substr(raw, 2, length(raw) - 2)
      gsub(/\\\"/, "\"", value); gsub(/\\\\/, "\\", value); gsub(/\\\//, "/", value)
      return value
    }
    function new_node(type, raw,    id) { id = ++node_count; kind[id] = type; atom[id] = raw; return id }
    function parse_value(    ch, start, raw, id) {
      skip_ws(); ch = substr(source, pos, 1)
      if (ch == "{") return parse_object()
      if (ch == "[") return parse_array()
      if (ch == "\"") return new_node("atom", parse_string())
      start = pos
      while (pos <= length(source) && substr(source, pos, 1) !~ /[,}\] \t\r\n]/) pos++
      if (start == pos) { parse_error = "expected JSON value at character " pos; return 0 }
      raw = substr(source, start, pos - start)
      if (raw !~ /^(true|false|null)$/ && raw !~ /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$/) {
        parse_error = "invalid JSON value at character " start
        return 0
      }
      return new_node("atom", raw)
    }
    function parse_object(    id, raw_key, child, ch) {
      id = new_node("object", ""); pos++; skip_ws()
      if (substr(source, pos, 1) == "}") { pos++; return id }
      while (!parse_error) {
        skip_ws(); if (substr(source, pos, 1) != "\"") { parse_error = "expected object key at character " pos; return id }
        raw_key = parse_string(); skip_ws()
        if (substr(source, pos, 1) != ":") { parse_error = "expected colon at character " pos; return id }
        pos++; child = parse_value(); entries[id]++; object_key[id, entries[id]] = raw_key; object_name[id, entries[id]] = key_name(raw_key); edge[id, entries[id]] = child
        skip_ws(); ch = substr(source, pos, 1); pos++
        if (ch == "}") return id
        if (ch != ",") { parse_error = "expected comma or closing brace at character " (pos - 1); return id }
      }
      return id
    }
    function parse_array(    id, child, ch) {
      id = new_node("array", ""); pos++; skip_ws()
      if (substr(source, pos, 1) == "]") { pos++; return id }
      while (!parse_error) {
        child = parse_value(); entries[id]++; edge[id, entries[id]] = child
        skip_ws(); ch = substr(source, pos, 1); pos++
        if (ch == "]") return id
        if (ch != ",") { parse_error = "expected comma or closing bracket at character " (pos - 1); return id }
      }
      return id
    }
    function parse_document(text, label,    root) {
      source = text; sub(/^\xef\xbb\xbf/, "", source); pos = 1; parse_error = ""
      root = parse_value(); skip_ws()
      if (!parse_error && pos <= length(source)) parse_error = "unexpected content at character " pos
      if (parse_error) { print "Cannot merge invalid JSON file " label ": " parse_error > "/dev/stderr"; exit 2 }
      if (kind[root] != "object") { print "JSON file " label " must contain an object" > "/dev/stderr"; exit 2 }
      return root
    }
    function find_key(id, name,    i) { for (i = 1; i <= entries[id]; i++) if (object_name[id, i] == name) return i; return 0 }
    function merge_missing(target, defaults,    i, found, target_child, default_child) {
      for (i = 1; i <= entries[defaults]; i++) {
        found = find_key(target, object_name[defaults, i]); default_child = edge[defaults, i]
        if (!found) {
          entries[target]++; object_key[target, entries[target]] = object_key[defaults, i]; object_name[target, entries[target]] = object_name[defaults, i]; edge[target, entries[target]] = default_child
        } else {
          target_child = edge[target, found]
          if (kind[target_child] == "object" && kind[default_child] == "object") merge_missing(target_child, default_child)
        }
      }
    }
    function spaces(count,    value) { value = sprintf("%*s", count, ""); return value }
    function render(id, indent,    i, value) {
      if (kind[id] == "atom") return atom[id]
      if (kind[id] == "array") {
        if (!entries[id]) return "[]"
        value = "[\n"
        for (i = 1; i <= entries[id]; i++) value = value spaces(indent + 2) render(edge[id, i], indent + 2) (i < entries[id] ? "," : "") "\n"
        return value spaces(indent) "]"
      }
      if (!entries[id]) return "{}"
      value = "{\n"
      for (i = 1; i <= entries[id]; i++) value = value spaces(indent + 2) object_key[id, i] ": " render(edge[id, i], indent + 2) (i < entries[id] ? "," : "") "\n"
      return value spaces(indent) "}"
    }
    BEGIN {
      defaults_text = read_file(defaults_file); defaults_root = parse_document(defaults_text, defaults_file)
      current_text = read_file(current_file); if (current_text == "") current_text = "{}"
      current_root = parse_document(current_text, current_file)
      merge_missing(current_root, defaults_root)
      if (set_tool_root == "1") {
        source_index = find_key(defaults_root, "toolRoot"); target_index = find_key(current_root, "toolRoot")
        if (target_index) edge[current_root, target_index] = edge[defaults_root, source_index]
      }
      print render(current_root, 0)
    }
  ' >"$merged"; then
    rm -f -- "$merged"
    return 1
  fi
  if [[ -f "$target" ]] && cmp -s "$target" "$merged"; then rm -f -- "$merged"; return; fi
  echo "write $target"
  if (( dry_run )); then rm -f -- "$merged"; return; fi
  mkdir -p "$(dirname "$target")"
  mv -f -- "$merged" "$target"
}

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
package_root=$(cd "$script_dir/.." && pwd)
package_root_lower=${package_root,,}
case "/${package_root_lower//\\//}/" in
  */library/packagecache/*) echo "Refusing to install from Unity's Library/PackageCache: $package_root" >&2; exit 1 ;;
esac
skill_source="$package_root/skills/lemmings"
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
existing_profile="$repo_root/.codex/lemmings.json"
if [[ -f "$existing_profile" ]] && grep -Eq '"complex-worker"[[:space:]]*:' "$existing_profile"; then
  echo "Unsupported legacy model role 'complex-worker' in $existing_profile. Remove it and use 'worker' before bootstrapping schema version 1." >&2
  exit 1
fi

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

skill_target="$repo_root/.agents/skills/lemmings"
if [[ ! -d "$skill_target" ]]; then
  echo "copy $skill_source -> $skill_target"
  if (( ! dry_run )); then mkdir -p "$skill_target"; cp -R "$skill_source/." "$skill_target/"; fi
elif ! diff -qr --strip-trailing-cr "$skill_source" "$skill_target" >/dev/null 2>&1; then
  if (( ! force )); then echo "Skill target differs from the package copy: $skill_target. Re-run with --force to replace it." >&2; exit 1; fi
  echo "replace $skill_target"
  if (( ! dry_run )); then rm -rf -- "$skill_target"; mkdir -p "$skill_target"; cp -R "$skill_source/." "$skill_target/"; fi
fi

repo_name=$(basename "$repo_root")
project_relative=$(json_escape "$(relative_path "$repo_root" "$project_path")")
defaults_file=$(mktemp)
trap 'rm -f -- "$defaults_file"' EXIT
cat >"$defaults_file" <<JSON
{
  "schemaVersion": 1,
  "mode": "auto",
  "roadmap": "docs/tasks/ROADMAP.md",
  "taskGlobs": ["docs/tasks/**/*.json"],
  "reviewGlobs": ["docs/tasks/reviews/*.json"],
  "worktreeRoot": "../lemmings-worktrees",
  "models": {
    "orchestrator": "gpt-5.6-sol:high",
    "reviewer": "gpt-5.6-sol:high",
    "worker": "gpt-5.6-luna:max",
    "validator": "gpt-5.6-terra:medium",
    "explorer": "gpt-5.6-luna:high",
    "summarizer": "gpt-5.6-luna:medium"
  },
  "workerPolicy": {
    "elevatedModel": "gpt-5.6-terra:max"
  },
  "fallback": { "allowed": [] },
  "game": {
    "engine": "unity",
    "projectPath": "$project_relative",
    "workspace": {
      "policy": "auto",
      "parallelStrategy": "hybrid",
      "largeThresholdGiB": 10,
      "validationBackend": "clone",
      "validationPath": "../$repo_name.lemmings.validation",
      "maxUnityEditors": 1
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
merge_json "$repo_root/.codex/lemmings.json" "$defaults_file" 0

if (( ! package_inside_repo )); then
  common_dir=$(git_value "$repo_root" rev-parse --git-common-dir)
  [[ -n "$common_dir" ]] || { echo "Cannot resolve Git common directory for '$repo_root'." >&2; exit 1; }
  common_path=$(absolute_path "$common_dir" "$repo_root")
  package_root_json=$package_root
  if command -v cygpath >/dev/null 2>&1; then package_root_json=$(cygpath -w "$package_root"); fi
  printf '{"schemaVersion": 1, "toolRoot": "%s"}\n' "$(json_escape "$package_root_json")" >"$defaults_file"
  merge_json "$common_path/lemmings/environment.json" "$defaults_file" 1
fi

if (( dry_run )); then echo "Lemmings bootstrap dry run complete."; else echo "Lemmings skill bootstrap complete."; fi
