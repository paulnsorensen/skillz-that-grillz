#!/usr/bin/env bash
#
# skillz-that-grillz installer — sets up the CLI tools and (optional) MCP
# servers used by the skills in this repo on macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/paulnsorensen/skillz-that-grillz/main/scripts/install.sh | bash
#
# Or with options:
#   curl -fsSL https://raw.githubusercontent.com/paulnsorensen/skillz-that-grillz/main/scripts/install.sh | bash -s -- --skip-mcp
#
# Run `bash install.sh --help` for the full flag list.

# strict-mode is enabled inside the BASH_SOURCE guard at the bottom of the
# file so sourcing (e.g. from a test suite) does not mutate the caller's
# shell options.

# CLI tools wrapped by the skills in this repo. Names listed here are the
# brew formula names, which sometimes differ from the binary name (see
# sg_tool_binary). graphite is the upstream package for the gt CLI;
# ripgrep installs the rg binary. sd / fd / ast-grep back the
# bash-shortening skill's modernize rules and ast-grep engine.
SG_KNOWN_TOOLS="gh just prek graphite ast-grep sd ripgrep fd"

# Repository the installer pulls skills from. Centralized so discovery and
# install both reference the same source.
SG_SKILL_REPO="paulnsorensen/skillz-that-grillz"

# Embedded fallback list of skill names. The installer normally discovers
# the live set via the git-trees API by selecting paths that match
# skills/<name>/SKILL.md, so it self-heals when new skills land — this list
# is only used when the API call is unavailable (offline, rate-limited,
# repo temporarily private).
SG_FALLBACK_SKILLS="bash-shortening commit copilot file-handler gh gh-bootstrap github-copilot-personal-instructions github-copilot-repo-instructions justfile pr-stack prek safe-settings"

# Default selections.
SG_DEFAULT_TOOLS="$SG_KNOWN_TOOLS"
SG_DEFAULT_MCP="context7"

# Map a brew formula name to the binary it installs (when they differ).
sg_tool_binary() {
    case "$1" in
        graphite) echo "gt" ;;
        ripgrep)  echo "rg" ;;
        ast-grep) echo "sg" ;;
        *)        echo "$1" ;;
    esac
}

# Map a brew formula name to a custom tap/formula spec (when needed).
sg_tool_formula() {
    case "$1" in
        graphite) echo "withgraphite/tap/graphite" ;;
        *)        echo "$1" ;;
    esac
}

sg_log() {
    printf '\033[1;36m==>\033[0m %s\n' "$*"
}

sg_warn() {
    printf '\033[1;33m!! \033[0m %s\n' "$*" >&2
}

sg_err() {
    printf '\033[1;31mxx \033[0m %s\n' "$*" >&2
}

sg_usage() {
    cat <<'USAGE'
skillz-that-grillz installer (macOS)

Usage:
  install.sh [options]

Options:
  --tools <list>       Comma-separated CLI tools to install. Default: all.
                       Choices: gh, just, prek, graphite, ast-grep, sd,
                       ripgrep, fd
  --mcp <list>         Comma-separated MCP servers to register. Default:
                       context7. Choices: context7, none
  --skip-mcp           Same as --mcp none.
  --skip-tools         Skip CLI tool installs (useful for MCP-only runs).
  --harness <selection> Harness to register skills + MCP servers with.
                       Default: auto-detect claude-code, cursor, and codex.
                       Accepts a single harness, a comma-separated list, or
                       'auto'. Other values include vscode, gemini, zed, copilot.
  --dry-run            Print what would happen without changing anything.
  -h, --help           Show this help.

Environment:
  SG_BREW   SG_GH      Override the brew / gh binaries (used by tests).
  SG_CLAUDE SG_CURSOR  Override claude / cursor binaries for detection.
  SG_CODEX             Override codex binary for detection.
  SG_NPX               Override npx (used to launch context7 MCP).
USAGE
}

# OS guard. Returns 0 on macOS, 1 otherwise.
sg_detect_os() {
    case "$(uname -s)" in
        Darwin) return 0 ;;
        *)      return 1 ;;
    esac
}

sg_cmd_exists() {
    command -v "$1" >/dev/null 2>&1
}

sg_brew() {
    "${SG_BREW:-brew}" "$@"
}

# Verify Homebrew is installed; print install hint and fail otherwise.
sg_ensure_homebrew() {
    if sg_cmd_exists "${SG_BREW:-brew}"; then
        return 0
    fi
    sg_err "Homebrew is required but was not found."
    sg_err "Install it from https://brew.sh and re-run this script."
    return 1
}

# Returns 0 if every comma-separated token in $1 is in the space-separated
# list $2. Prints the offending token to stderr otherwise.
sg_validate_selection() {
    local list="$1" allowed="$2" token
    local IFS=,
    for token in $list; do
        case " $allowed " in
            *" $token "*) ;;
            *)
                sg_err "Unknown selection: $token"
                return 1
                ;;
        esac
    done
}

# Idempotently install one brew formula. Returns 0 if installed (or already
# present), 1 if brew failed. Honors $SG_DRY_RUN.
sg_brew_install_if_missing() {
    local tool="$1"
    local binary formula
    binary="$(sg_tool_binary "$tool")"
    formula="$(sg_tool_formula "$tool")"

    if sg_cmd_exists "$binary"; then
        sg_log "$tool: already installed ($binary on PATH)"
        return 0
    fi

    if [[ "${SG_DRY_RUN:-0}" == "1" ]]; then
        sg_log "$tool: would run 'brew install $formula'"
        return 0
    fi

    sg_log "$tool: installing via 'brew install $formula'"
    sg_brew install "$formula"
}

# Install every tool in the comma-separated list. Accumulates failures so a
# single broken tool doesn't get masked by a successful later install when the
# script is sourced (tests) without `set -e`.
sg_install_tools() {
    local list="$1" tool rc=0
    local IFS=,
    for tool in $list; do
        sg_brew_install_if_missing "$tool" || rc=1
    done
    return "$rc"
}

# Register a single MCP server with the chosen harness.
sg_install_mcp() {
    local server="$1" harness="$2"
    case "$server" in
        context7)
            sg_install_mcp_context7 "$harness"
            ;;
        none)
            sg_log "MCP: skipping (none selected)"
            ;;
        *)
            sg_err "Unknown MCP server: $server"
            return 1
            ;;
    esac
}

sg_install_mcp_context7() {
    local harness="$1"
    local claude="${SG_CLAUDE:-claude}"
    local npx="${SG_NPX:-npx}"
    if [[ "$harness" != "claude-code" ]]; then
        sg_warn "context7 MCP: only claude-code is auto-registered; configure $harness manually."
        return 0
    fi
    if ! sg_cmd_exists "$claude"; then
        sg_warn "context7 MCP: claude CLI not found; install Claude Code first."
        return 1
    fi
    if [[ "${SG_DRY_RUN:-0}" == "1" ]]; then
        sg_log "context7 MCP: would run '$claude mcp add context7 -- $npx -y @upstash/context7-mcp@latest'"
        return 0
    fi
    sg_log "context7 MCP: registering with claude-code"
    "$claude" mcp add context7 -- "$npx" -y @upstash/context7-mcp@latest
}

sg_install_mcp_list() {
    local list="$1" harness="$2" server rc=0
    local IFS=,
    for server in $list; do
        sg_install_mcp "$server" "$harness" || rc=1
    done
    return $rc
}

# Detect the main-line harness CLIs that can receive Agent Skills directly.
sg_detect_harnesses() {
    local claude_cli="${SG_CLAUDE:-claude}"
    local cursor_cli="${SG_CURSOR:-cursor}"
    local codex_cli="${SG_CODEX:-codex}"

    if sg_cmd_exists "$claude_cli"; then
        printf 'claude-code\n'
    fi
    if sg_cmd_exists "$cursor_cli"; then
        printf 'cursor\n'
    fi
    if sg_cmd_exists "$codex_cli"; then
        printf 'codex\n'
    fi
}

# Resolve --harness into a newline-delimited harness list. 'auto' installs to
# every detected main-line harness, falling back to claude-code on machines
# where no supported harness CLI is on PATH.
sg_resolve_harnesses() {
    local selection="$1" harnesses harness
    if [[ "$selection" == "auto" ]]; then
        harnesses="$(sg_detect_harnesses)"
        if [[ -n "$harnesses" ]]; then
            while IFS= read -r harness; do
                printf '%s\n' "$harness"
            done <<< "$harnesses"
        else
            sg_warn "No supported harness CLI detected; falling back to claude-code. Use --harness <name> to override."
            printf 'claude-code\n'
        fi
        return 0
    fi

    if [[ -z "${selection//[[:space:]]/}" ]]; then
        printf 'error: empty --harness selection. Use --harness auto or provide one or more comma-separated harness names.\n' >&2
        return 1
    fi

    local -a harness_tokens
    IFS=',' read -r -a harness_tokens <<< "$selection"
    for harness in "${harness_tokens[@]}"; do
        harness="${harness#"${harness%%[![:space:]]*}"}"
        harness="${harness%"${harness##*[![:space:]]}"}"
        if [[ -z "$harness" ]]; then
            printf 'error: invalid --harness selection "%s". Harness names must be non-empty and comma-separated.\n' "$selection" >&2
            return 1
        fi
        printf '%s\n' "$harness"
    done
}

sg_install_skills_for_harnesses() {
    local harnesses="$1" harness rc=0
    while IFS= read -r harness; do
        [[ -n "$harness" ]] || continue
        sg_install_skills "$harness" || rc=1
    done <<< "$harnesses"
    return $rc
}

sg_install_mcp_for_harnesses() {
    local list="$1" harnesses="$2" harness rc=0
    while IFS= read -r harness; do
        [[ -n "$harness" ]] || continue
        sg_install_mcp_list "$list" "$harness" || rc=1
    done <<< "$harnesses"
    return $rc
}

# Discover the live skill list via the git-trees API and keep only true
# skill roots (skills/<name>/SKILL.md). Prints one skill name per line on
# success; on failure (network, rate limit, private repo) returns non-zero
# with empty stdout and the caller falls back to SG_FALLBACK_SKILLS.
sg_discover_skills() {
    local gh="$1"
    "$gh" api "repos/${SG_SKILL_REPO}/git/trees/HEAD?recursive=1" \
        --jq '.tree[] | select(.type == "blob" and (.path | test("^skills/[^/]+/SKILL\\.md$"))) | .path | split("/")[1]' 2>/dev/null
}

# Install the skill set into the picked harness via 'gh skill'. User scope
# so they live alongside the user's other skills, not committed into the
# project. Requires gh to be authenticated.
sg_install_skills() {
    local harness="$1"
    local gh="${SG_GH:-gh}"
    if ! sg_cmd_exists "$gh"; then
        sg_warn "skills: gh CLI not found; skipping. Add 'gh' to --tools first."
        return 0
    fi

    local skills="$SG_FALLBACK_SKILLS"
    if [[ "${SG_DRY_RUN:-0}" != "1" ]]; then
        if ! "$gh" auth status >/dev/null 2>&1; then
            sg_warn "skills: gh is not authenticated. Run 'gh auth login' and re-run."
            return 1
        fi
        local discovered
        if discovered="$(sg_discover_skills "$gh")" && [[ -n "$discovered" ]]; then
            skills="$discovered"
        else
            sg_warn "skills: could not list skills via gh api; using embedded fallback list."
        fi
    fi

    local skill rc=0
    for skill in $skills; do
        if [[ "${SG_DRY_RUN:-0}" == "1" ]]; then
            sg_log "skills: would run '$gh skill install $SG_SKILL_REPO $skill --agent $harness --scope user --force'"
            continue
        fi
        sg_log "skills: installing $skill into $harness (user scope)"
        if ! "$gh" skill install "$SG_SKILL_REPO" "$skill" --agent "$harness" --scope user --force; then
            sg_warn "skills: failed to install $skill"
            rc=1
        fi
    done
    return $rc
}

# Parse argv into the SG_* config variables.
sg_parse_args() {
    SG_TOOLS="$SG_DEFAULT_TOOLS"
    SG_TOOLS="${SG_TOOLS// /,}"
    SG_MCP="$SG_DEFAULT_MCP"
    SG_MCP="${SG_MCP// /,}"
    SG_HARNESS="auto"
    SG_DRY_RUN="${SG_DRY_RUN:-0}"
    SG_SKIP_TOOLS="0"
    SG_DO_HELP="0"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --tools)
                shift
                [[ $# -gt 0 ]] || { sg_err "--tools requires a value"; return 2; }
                SG_TOOLS="$1"
                ;;
            --tools=*)
                SG_TOOLS="${1#*=}"
                ;;
            --mcp)
                shift
                [[ $# -gt 0 ]] || { sg_err "--mcp requires a value"; return 2; }
                SG_MCP="$1"
                ;;
            --mcp=*)
                SG_MCP="${1#*=}"
                ;;
            --skip-mcp)
                SG_MCP="none"
                ;;
            --skip-tools)
                SG_SKIP_TOOLS="1"
                ;;
            --harness)
                shift
                [[ $# -gt 0 ]] || { sg_err "--harness requires a value"; return 2; }
                SG_HARNESS="$1"
                ;;
            --harness=*)
                SG_HARNESS="${1#*=}"
                ;;
            --dry-run)
                SG_DRY_RUN="1"
                ;;
            -h|--help)
                SG_DO_HELP="1"
                ;;
            --)
                shift
                break
                ;;
            -*)
                sg_err "Unknown option: $1"
                return 2
                ;;
            *)
                sg_err "Unexpected positional argument: $1"
                return 2
                ;;
        esac
        shift
    done

    sg_validate_selection "$SG_TOOLS" "$SG_KNOWN_TOOLS" || return 2
    sg_validate_selection "$SG_MCP" "context7 none" || return 2
}

sg_main() {
    sg_parse_args "$@" || return $?

    if [[ "$SG_DO_HELP" == "1" ]]; then
        sg_usage
        return 0
    fi

    if ! sg_detect_os; then
        sg_err "skillz-that-grillz installer currently supports macOS only."
        sg_err "Detected: $(uname -s). See README for manual install on other platforms."
        return 1
    fi

    if [[ "$SG_SKIP_TOOLS" != "1" ]]; then
        sg_ensure_homebrew || return 1
        sg_install_tools "$SG_TOOLS"
    fi

    local harnesses
    harnesses="$(sg_resolve_harnesses "$SG_HARNESS")" || return $?
    sg_install_skills_for_harnesses "$harnesses" || return 1

    if [[ "$SG_MCP" != "none" ]]; then
        sg_install_mcp_for_harnesses "$SG_MCP" "$harnesses" || return 1
    fi

    sg_log "Done. Restart your harness so skills, MCP servers, and PATH changes take effect."
}

# Only run main when executed directly, not when sourced.
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    set -euo pipefail
    sg_main "$@"
fi
