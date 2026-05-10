#!/usr/bin/env bash
#
# skillz — local artifact storage for skills.
#
# Persists files under ./.skillz/<type>/<slug> in the current working
# directory (or $SKILLZ_DIR if set). Skills shell out to this script
# instead of inventing their own per-skill on-disk conventions, so a
# user's notes, plans, scratch buffers, etc. live in one predictable
# tree.
#
# Subcommands:
#   save_file <type> <slug> [content]
#       Write to .skillz/<type>/<slug>. If content is omitted, read
#       stdin. Parent directories are created on demand.
#
#   get_file <type> <slug>
#       Print .skillz/<type>/<slug> to stdout. Exits 1 if missing.
#
#   search_files <query> [--type <type>]
#       Search artifact titles (slug names) and bodies (grep). Prints
#       title hits first, then body hits as <path>:<line>:<text>.
#       Optional --type narrows to a single subdirectory.
#
# Environment:
#   SKILLZ_DIR  Override the storage root (default: $PWD/.skillz).
#
# Exit codes:
#   0  success
#   1  not found / no matches
#   2  usage error (missing args, bad slug, etc.)

set -euo pipefail

skillz_root() {
    printf '%s\n' "${SKILLZ_DIR:-$PWD/.skillz}"
}

skillz_die() {
    printf 'skillz: %s\n' "$*" >&2
    exit 2
}

# Reject empty values, path separators, parent traversal, and leading
# dots. The slug becomes a literal filename, so anything that could
# escape the type directory is a hard error.
skillz_validate_segment() {
    local kind="$1" value="$2"
    [[ -n "$value" ]] || skillz_die "$kind must not be empty"
    [[ "$value" != *..* ]] || skillz_die "$kind must not contain '..'"
    [[ "$value" != */* && "$value" != *\\* ]] \
        || skillz_die "$kind must not contain path separators"
    [[ "$value" != .* ]] || skillz_die "$kind must not start with '.'"
    [[ "$value" =~ ^[A-Za-z0-9._-]+$ ]] \
        || skillz_die "$kind may only contain [A-Za-z0-9._-]"
}

skillz_path() {
    local type="$1" slug="$2"
    skillz_validate_segment type "$type"
    skillz_validate_segment slug "$slug"
    printf '%s/%s/%s\n' "$(skillz_root)" "$type" "$slug"
}

skillz_save_file() {
    [[ $# -ge 2 ]] || skillz_die "save_file <type> <slug> [content]"
    local type="$1" slug="$2"
    shift 2
    local path
    # An explicit if-check is required: `local path=$(...)` would mask the
    # subshell's `exit 2` from skillz_die, leaving $path empty and causing
    # mkdir to fail with the wrong exit code.
    if ! path=$(skillz_path "$type" "$slug"); then
        return 2
    fi
    mkdir -p "${path%/*}"
    if [[ $# -gt 0 ]]; then
        printf '%s' "$*" > "$path"
    else
        cat > "$path"
    fi
    printf '%s\n' "$path"
}

skillz_get_file() {
    [[ $# -eq 2 ]] || skillz_die "get_file <type> <slug>"
    local path
    if ! path=$(skillz_path "$1" "$2"); then
        return 2
    fi
    if [[ ! -f "$path" ]]; then
        printf 'skillz: not found: %s\n' "$path" >&2
        return 1
    fi
    cat "$path"
}

skillz_search_files() {
    local query="" type=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --type)
                shift
                [[ $# -gt 0 ]] || skillz_die "--type requires a value"
                type="$1"
                ;;
            --type=*)
                type="${1#*=}"
                ;;
            --)
                shift
                [[ $# -gt 0 ]] || skillz_die "search_files <query> [--type <type>]"
                query="$1"
                shift
                break
                ;;
            -*)
                skillz_die "unknown flag: $1"
                ;;
            *)
                [[ -z "$query" ]] || skillz_die "only one query is supported"
                query="$1"
                ;;
        esac
        shift
    done
    [[ -n "$query" ]] || skillz_die "search_files <query> [--type <type>]"

    local root scope
    root="$(skillz_root)"
    if [[ -n "$type" ]]; then
        skillz_validate_segment type "$type"
        scope="$root/$type"
    else
        scope="$root"
    fi
    if [[ ! -d "$scope" ]]; then
        return 1
    fi

    local found=0 hits

    hits="$(find "$scope" -type f -iname "*${query}*" 2>/dev/null | sort || true)"
    if [[ -n "$hits" ]]; then
        printf '## titles\n%s\n' "$hits"
        found=1
    fi

    local body_hits
    body_hits="$(grep -rIn --binary-files=without-match -- "$query" "$scope" 2>/dev/null || true)"
    if [[ -n "$body_hits" ]]; then
        [[ "$found" -eq 1 ]] && printf '\n'
        printf '## bodies\n%s\n' "$body_hits"
        found=1
    fi

    [[ "$found" -eq 1 ]] || return 1
}

skillz_usage() {
    sed -n '3,32p' "$0" | sed 's/^# \{0,1\}//'
}

skillz_main() {
    [[ $# -ge 1 ]] || { skillz_usage >&2; exit 2; }
    local cmd="$1"
    shift
    case "$cmd" in
        save_file)    skillz_save_file "$@" ;;
        get_file)     skillz_get_file "$@" ;;
        search_files) skillz_search_files "$@" ;;
        -h|--help|help) skillz_usage ;;
        *) skillz_die "unknown subcommand: $cmd" ;;
    esac
}

if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    skillz_main "$@"
fi
