#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
handler="$script_dir/../skills/lemmings/scripts/run.py"
payload_file=$(mktemp "${TMPDIR:-/tmp}/lemmings-hook.XXXXXX")
trap 'rm -f "$payload_file"' EXIT HUP INT TERM
cat >"$payload_file"

if ! command -v git >/dev/null 2>&1; then
    printf '%s\n' 'Lemmings hook cannot determine runtime state because Git is unavailable.' >&2
    exit 1
fi

# JSON parsing is deliberately avoided here: inactive hooks must not require an
# interpreter. POSIX hosts use slash paths, so this bounded extraction is only
# a cwd hint; the host working directory remains the fallback.
cwd=$(sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"\\]*\)".*/\1/p' "$payload_file" | sed -n '1p')
[ -n "$cwd" ] || cwd=$PWD
if [ ! -d "$cwd" ]; then
    printf '%s\n' "Lemmings hook cwd does not exist: $cwd" >&2
    exit 1
fi

inside=$(git -C "$cwd" rev-parse --is-inside-work-tree 2>/dev/null || true)
if [ "$inside" != true ]; then
    printf '%s\n' '{}'
    exit 0
fi
common_dir=$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
if [ -z "$common_dir" ]; then
    printf '%s\n' 'Lemmings hook could not resolve the Git common directory.' >&2
    exit 1
fi
active_marker="$common_dir/lemmings/active.json"
if [ ! -f "$active_marker" ]; then
    printf '%s\n' '{}'
    exit 0
fi

# An active marker makes the existing Python policy authoritative. Forward the
# original payload bytes and the handler exit status unchanged.
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
        && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        set +e
        "$candidate" "$handler" hook "$@" <"$payload_file"
        status=$?
        set -e
        rm -f "$payload_file"
        trap - EXIT HUP INT TERM
        exit "$status"
    fi
done
printf '%s\n' 'Lemmings runtime is active but Python 3.10 or newer is unavailable; deactivate only after a safe handoff.' >&2
exit 1
