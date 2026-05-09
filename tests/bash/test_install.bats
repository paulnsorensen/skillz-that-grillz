#!/usr/bin/env bats
#
# Tests for scripts/install.sh.
#
# The script is sourced (the BASH_SOURCE != $0 guard skips main on source)
# so each test can call individual functions with controlled stubs.

setup() {
    REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"
    INSTALL_SH="$REPO_ROOT/scripts/install.sh"
    STUB_BIN="$BATS_TEST_TMPDIR/bin"
    STUB_LOG="$BATS_TEST_TMPDIR/calls.log"
    mkdir -p "$STUB_BIN"
    : > "$STUB_LOG"
    export STUB_LOG
    PATH_ORIG="$PATH"
    # Sparse PATH so only /usr/bin essentials + per-test stubs resolve.
    PATH="$STUB_BIN:/usr/bin:/bin"
    # shellcheck disable=SC1090
    source "$INSTALL_SH"
}

teardown() {
    PATH="$PATH_ORIG"
}

# Drop a stub binary into $STUB_BIN that records its argv to $STUB_LOG.
make_stub() {
    local name="$1" exit_code="${2:-0}"
    # Body uses a single-quoted heredoc so $name / $STUB_LOG / $exit_code
    # never accidentally interpolate at write time. The runtime values are
    # injected via printf %q (shell-safe quoting) before/after the body.
    {
        printf '#!/usr/bin/env bash\n'
        printf 'echo %q "$*" >> %q\n' "$name" "$STUB_LOG"
        printf 'exit %q\n' "$exit_code"
    } > "$STUB_BIN/$name"
    chmod +x "$STUB_BIN/$name"
}

# -- sg_tool_binary ----------------------------------------------------------

@test "sg_tool_binary maps graphite to gt" {
    [[ "$(sg_tool_binary graphite)" == "gt" ]]
}

@test "sg_tool_binary maps ripgrep to rg" {
    [[ "$(sg_tool_binary ripgrep)" == "rg" ]]
}

@test "sg_tool_binary returns identity for non-aliased tools" {
    [[ "$(sg_tool_binary gh)" == "gh" ]]
    [[ "$(sg_tool_binary just)" == "just" ]]
    [[ "$(sg_tool_binary prek)" == "prek" ]]
    [[ "$(sg_tool_binary sd)" == "sd" ]]
    [[ "$(sg_tool_binary fd)" == "fd" ]]
    [[ "$(sg_tool_binary ast-grep)" == "ast-grep" ]]
}

# -- sg_tool_formula ---------------------------------------------------------

@test "sg_tool_formula maps graphite to the withgraphite tap spec" {
    [[ "$(sg_tool_formula graphite)" == "withgraphite/tap/graphite" ]]
}

@test "sg_tool_formula returns identity for core formulas" {
    [[ "$(sg_tool_formula gh)" == "gh" ]]
    [[ "$(sg_tool_formula just)" == "just" ]]
    [[ "$(sg_tool_formula prek)" == "prek" ]]
}

# -- sg_validate_selection ---------------------------------------------------

@test "sg_validate_selection accepts subset of allowed list" {
    run sg_validate_selection "gh,just" "$SG_KNOWN_TOOLS"
    [ "$status" -eq 0 ]
}

@test "sg_validate_selection rejects unknown token" {
    run sg_validate_selection "gh,bogus" "$SG_KNOWN_TOOLS"
    [ "$status" -ne 0 ]
    [[ "$output" == *"Unknown selection: bogus"* ]]
}

@test "sg_validate_selection accepts the special 'none' MCP token" {
    run sg_validate_selection "none" "context7 none"
    [ "$status" -eq 0 ]
}

@test "sg_validate_selection rejects tilth (carry-over guard)" {
    run sg_validate_selection "tilth" "$SG_KNOWN_TOOLS"
    [ "$status" -ne 0 ]
    [[ "$output" == *"Unknown selection: tilth"* ]]
}

# -- sg_parse_args -----------------------------------------------------------

@test "sg_parse_args defaults match the documented set" {
    sg_parse_args
    [[ "$SG_TOOLS" == *"gh"* ]]
    [[ "$SG_TOOLS" == *"just"* ]]
    [[ "$SG_TOOLS" == *"prek"* ]]
    [[ "$SG_TOOLS" == *"graphite"* ]]
    [[ "$SG_TOOLS" == *"ast-grep"* ]]
    [[ "$SG_TOOLS" == *"sd"* ]]
    [[ "$SG_TOOLS" == *"ripgrep"* ]]
    [[ "$SG_TOOLS" == *"fd"* ]]
    [[ "$SG_MCP" == "context7" ]]
    [[ "$SG_HARNESS" == "auto" ]]
    [[ "$SG_DRY_RUN" == "0" ]]
    [[ "$SG_DO_HELP" == "0" ]]
}

@test "sg_parse_args --tools with value parses comma list" {
    sg_parse_args --tools gh,just
    [[ "$SG_TOOLS" == "gh,just" ]]
}

@test "sg_parse_args --tools=value parses inline value" {
    sg_parse_args --tools=prek
    [[ "$SG_TOOLS" == "prek" ]]
}

@test "sg_parse_args --skip-mcp sets MCP to none" {
    sg_parse_args --skip-mcp
    [[ "$SG_MCP" == "none" ]]
}

@test "sg_parse_args --skip-tools sets the flag" {
    sg_parse_args --skip-tools
    [[ "$SG_SKIP_TOOLS" == "1" ]]
}

@test "sg_parse_args --dry-run sets DRY_RUN" {
    sg_parse_args --dry-run
    [[ "$SG_DRY_RUN" == "1" ]]
}

@test "sg_parse_args --harness overrides default harness" {
    sg_parse_args --harness cursor
    [[ "$SG_HARNESS" == "cursor" ]]
}

@test "sg_parse_args --harness accepts comma-separated list" {
    sg_parse_args --harness cursor,codex
    [[ "$SG_HARNESS" == "cursor,codex" ]]
}

@test "sg_parse_args -h sets DO_HELP" {
    sg_parse_args -h
    [[ "$SG_DO_HELP" == "1" ]]
}

@test "sg_parse_args --help sets DO_HELP" {
    sg_parse_args --help
    [[ "$SG_DO_HELP" == "1" ]]
}

@test "sg_parse_args rejects unknown flag with exit code 2" {
    run sg_parse_args --bogus
    [ "$status" -eq 2 ]
    [[ "$output" == *"Unknown option"* ]]
}

@test "sg_parse_args rejects positional arg with exit code 2" {
    run sg_parse_args stray-arg
    [ "$status" -eq 2 ]
    [[ "$output" == *"Unexpected positional"* ]]
}

@test "sg_parse_args --tools without value fails" {
    run sg_parse_args --tools
    [ "$status" -eq 2 ]
}

@test "sg_parse_args --mcp without value fails" {
    run sg_parse_args --mcp
    [ "$status" -eq 2 ]
}

@test "sg_parse_args rejects unknown tool selection" {
    run sg_parse_args --tools gh,foobar
    [ "$status" -eq 2 ]
    [[ "$output" == *"foobar"* ]]
}

@test "sg_parse_args rejects unknown mcp selection" {
    run sg_parse_args --mcp context7,bogus
    [ "$status" -eq 2 ]
    [[ "$output" == *"bogus"* ]]
}

@test "sg_parse_args -- terminates option parsing" {
    sg_parse_args --dry-run --
    [[ "$SG_DRY_RUN" == "1" ]]
}

# -- sg_detect_os ------------------------------------------------------------

@test "sg_detect_os returns 0 when uname reports Darwin" {
    cat > "$STUB_BIN/uname" <<'STUB'
#!/usr/bin/env bash
echo Darwin
STUB
    chmod +x "$STUB_BIN/uname"
    run sg_detect_os
    [ "$status" -eq 0 ]
}

@test "sg_detect_os returns 1 when uname reports Linux" {
    cat > "$STUB_BIN/uname" <<'STUB'
#!/usr/bin/env bash
echo Linux
STUB
    chmod +x "$STUB_BIN/uname"
    run sg_detect_os
    [ "$status" -eq 1 ]
}

# -- sg_ensure_homebrew ------------------------------------------------------

@test "sg_ensure_homebrew passes when brew exists" {
    make_stub brew
    run sg_ensure_homebrew
    [ "$status" -eq 0 ]
}

@test "sg_ensure_homebrew fails with hint when brew missing" {
    run sg_ensure_homebrew
    [ "$status" -eq 1 ]
    [[ "$output" == *"Homebrew is required"* ]]
    [[ "$output" == *"https://brew.sh"* ]]
}

# -- sg_brew_install_if_missing ----------------------------------------------

@test "sg_brew_install_if_missing skips when binary already on PATH" {
    make_stub gh
    make_stub brew
    export SG_BREW="$STUB_BIN/brew"
    run sg_brew_install_if_missing gh
    [ "$status" -eq 0 ]
    [[ "$output" == *"already installed"* ]]
    # brew should NOT have been invoked
    [ ! -s "$STUB_LOG" ] || ! grep -q "^brew install" "$STUB_LOG"
}

@test "sg_brew_install_if_missing dry-run prints would-run line for tap formula" {
    SG_DRY_RUN=1 run sg_brew_install_if_missing graphite
    [ "$status" -eq 0 ]
    [[ "$output" == *"would run 'brew install withgraphite/tap/graphite'"* ]]
}

@test "sg_brew_install_if_missing dry-run prints would-run line for core formula" {
    SG_DRY_RUN=1 run sg_brew_install_if_missing just
    [ "$status" -eq 0 ]
    [[ "$output" == *"would run 'brew install just'"* ]]
}

@test "sg_brew_install_if_missing invokes brew with mapped formula when missing" {
    make_stub brew
    export SG_BREW="$STUB_BIN/brew"
    run sg_brew_install_if_missing graphite
    [ "$status" -eq 0 ]
    grep -q "^brew install withgraphite/tap/graphite$" "$STUB_LOG"
}

@test "sg_brew_install_if_missing surfaces brew failure" {
    make_stub brew 1
    export SG_BREW="$STUB_BIN/brew"
    run sg_brew_install_if_missing prek
    [ "$status" -ne 0 ]
}

# -- sg_install_tools (dispatcher loop) --------------------------------------

@test "sg_install_tools visits each comma-separated tool" {
    make_stub brew
    export SG_BREW="$STUB_BIN/brew"
    run sg_install_tools "gh,just,prek"
    [ "$status" -eq 0 ]
    grep -q "^brew install gh$" "$STUB_LOG"
    grep -q "^brew install just$" "$STUB_LOG"
    grep -q "^brew install prek$" "$STUB_LOG"
}

@test "sg_install_tools routes graphite through the tap-spec formula" {
    make_stub brew
    export SG_BREW="$STUB_BIN/brew"
    run sg_install_tools "graphite"
    [ "$status" -eq 0 ]
    grep -q "^brew install withgraphite/tap/graphite$" "$STUB_LOG"
}

@test "sg_install_tools dry-run lists every tool without invoking brew" {
    SG_DRY_RUN=1 run sg_install_tools "gh,graphite"
    [ "$status" -eq 0 ]
    [[ "$output" == *"would run 'brew install gh'"* ]]
    [[ "$output" == *"would run 'brew install withgraphite/tap/graphite'"* ]]
    [ ! -s "$STUB_LOG" ] || ! grep -q "^brew install" "$STUB_LOG"
}

@test "sg_install_tools dry-run handles all bash-shortening dependencies" {
    SG_DRY_RUN=1 run sg_install_tools "ast-grep,sd,ripgrep,fd"
    [ "$status" -eq 0 ]
    [[ "$output" == *"would run 'brew install ast-grep'"* ]]
    [[ "$output" == *"would run 'brew install sd'"* ]]
    [[ "$output" == *"would run 'brew install ripgrep'"* ]]
    [[ "$output" == *"would run 'brew install fd'"* ]]
}

@test "sg_validate_selection accepts the new modernize-deps tools" {
    run sg_validate_selection "ast-grep,sd,ripgrep,fd" "$SG_KNOWN_TOOLS"
    [ "$status" -eq 0 ]
}

# -- sg_resolve_harnesses ----------------------------------------------------

@test "sg_resolve_harnesses 'auto' falls back to claude-code when no CLI detected" {
    run sg_resolve_harnesses auto
    [ "$status" -eq 0 ]
    [[ "$output" == *"claude-code"* ]]
}

@test "sg_resolve_harnesses 'auto' detects every supported harness CLI on PATH" {
    make_stub claude
    make_stub cursor
    make_stub codex
    run sg_resolve_harnesses auto
    [ "$status" -eq 0 ]
    [[ "$output" == *"claude-code"* ]]
    [[ "$output" == *"cursor"* ]]
    [[ "$output" == *"codex"* ]]
}

@test "sg_resolve_harnesses passes through a single named harness" {
    run sg_resolve_harnesses cursor
    [ "$status" -eq 0 ]
    [ "$output" = "cursor" ]
}

@test "sg_resolve_harnesses splits a comma-separated list" {
    run sg_resolve_harnesses claude-code,cursor,codex
    [ "$status" -eq 0 ]
    [[ "$output" == *"claude-code"* ]]
    [[ "$output" == *"cursor"* ]]
    [[ "$output" == *"codex"* ]]
}

@test "sg_resolve_harnesses rejects empty selection" {
    run sg_resolve_harnesses ""
    [ "$status" -ne 0 ]
    [[ "$output" == *"empty --harness selection"* ]]
}

@test "sg_resolve_harnesses rejects empty token in list" {
    run sg_resolve_harnesses "cursor,,codex"
    [ "$status" -ne 0 ]
    [[ "$output" == *"invalid --harness selection"* ]]
}

# -- sg_install_mcp_context7 -------------------------------------------------

@test "sg_install_mcp_context7 dry-run prints would-run line for claude-code" {
    make_stub claude
    export SG_CLAUDE="$STUB_BIN/claude"
    SG_DRY_RUN=1 run sg_install_mcp_context7 claude-code
    [ "$status" -eq 0 ]
    [[ "$output" == *"would run"* ]]
    [[ "$output" == *"context7-mcp"* ]]
}

@test "sg_install_mcp_context7 warns and skips for non-claude-code harness" {
    run sg_install_mcp_context7 cursor
    [ "$status" -eq 0 ]
    [[ "$output" == *"only claude-code is auto-registered"* ]]
}

@test "sg_install_mcp_context7 warns when claude CLI missing" {
    run sg_install_mcp_context7 claude-code
    [ "$status" -ne 0 ]
    [[ "$output" == *"claude CLI not found"* ]]
}

# -- sg_install_skills (gh-skill dispatch) -----------------------------------

@test "sg_install_skills warns and returns 0 when gh missing" {
    run sg_install_skills claude-code
    [ "$status" -eq 0 ]
    [[ "$output" == *"gh CLI not found"* ]]
}

@test "sg_install_skills dry-run lists every fallback skill" {
    make_stub gh
    export SG_GH="$STUB_BIN/gh"
    SG_DRY_RUN=1 run sg_install_skills claude-code
    [ "$status" -eq 0 ]
    [[ "$output" == *"commit"* ]]
    [[ "$output" == *"gh"* ]]
    [[ "$output" == *"gt"* ]]
    [[ "$output" == *"justfile"* ]]
    [[ "$output" == *"prek"* ]]
}
