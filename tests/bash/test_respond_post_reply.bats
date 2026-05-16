#!/usr/bin/env bats
#
# Tests for skills/respond/scripts/post-reply.sh.
#
# We source the script with --help / wrong args to drive parser code paths,
# and call compose_body / resolve_handle directly with PATH stubs to keep
# every test offline.

setup() {
    REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"
    POST_REPLY_SH="$REPO_ROOT/skills/respond/scripts/post-reply.sh"
    STUB_BIN="$BATS_TEST_TMPDIR/bin"
    mkdir -p "$STUB_BIN"
    PATH_ORIG="$PATH"
    PATH="$STUB_BIN:/usr/bin:/bin"
    # The script runs argument parsing at module load. To source only the
    # functions and constants for unit testing, slice everything before the
    # "Argument parsing" section into a temp file and replace the tail with
    # `return 0` so the source call exits cleanly.
    awk '/^# --- Argument parsing/{print "return 0"; exit} {print}' \
        "$POST_REPLY_SH" > "$BATS_TEST_TMPDIR/post-reply-fns.sh"
    # shellcheck disable=SC1091
    source "$BATS_TEST_TMPDIR/post-reply-fns.sh"
}

teardown() {
    PATH="$PATH_ORIG"
    unset RESPOND_GH_HANDLE
}

make_stub() {
    local name="$1" exit_code="${2:-0}" stdout="${3:-}"
    {
        printf '#!/usr/bin/env bash\n'
        printf 'printf %q\n' "$stdout"
        printf 'exit %q\n' "$exit_code"
    } > "$STUB_BIN/$name"
    chmod +x "$STUB_BIN/$name"
}

# -- Constants are defined and exact ----------------------------------------

@test "ATTRIBUTION_PREFIX is the exact spec wording" {
    [[ "$ATTRIBUTION_PREFIX" == "agent on behalf of;" ]]
}

@test "ATTRIBUTION_SEPARATOR is a markdown horizontal rule" {
    [[ "$ATTRIBUTION_SEPARATOR" == "---" ]]
}

# -- compose_body appends the suffix correctly -------------------------------

@test "compose_body appends separator + attribution line" {
    # $(...) strips trailing newlines, so the expected has no final newline.
    out="$(compose_body "Fixed — added a guard." "octocat")"
    expected="Fixed — added a guard.

---
agent on behalf of; octocat"
    [[ "$out" == "$expected" ]]
}

@test "compose_body preserves multi-line bodies" {
    body=$'Line one.\nLine two.\nLine three.'
    out="$(compose_body "$body" "octocat")"
    [[ "$out" == *"Line one."* ]]
    [[ "$out" == *"Line two."* ]]
    [[ "$out" == *"Line three."* ]]
    [[ "$out" == *"---"* ]]
    [[ "$out" == *"agent on behalf of; octocat"* ]]
}

@test "compose_body is idempotent when attribution already present" {
    pre="Already done.

---
agent on behalf of; octocat"
    out="$(compose_body "$pre" "octocat")"
    [[ "$out" == "$pre" ]]
}

@test "compose_body appends when attribution is quoted mid-body but not at end" {
    # Body merely quotes the prefix (e.g. citing the spec); the suffix
    # check must still append the real attribution at the end.
    body='See the rule: lines must end with "agent on behalf of; <handle>".

Now, my actual reply continues below.'
    out="$(compose_body "$body" "octocat")"
    [[ "$out" == *"---"*"agent on behalf of; octocat"* ]]
    # Tail must be the real attribution line (with the leading separator).
    tail="$(printf '%s' "$out" | tail -n 2)"
    [[ "$tail" == "---"$'\n'"agent on behalf of; octocat" ]]
}

@test "compose_body keeps the exact 'agent on behalf of;' wording" {
    out="$(compose_body "x" "alice")"
    # Single space between semicolon and handle, lowercase, with semicolon.
    [[ "$out" == *"agent on behalf of; alice"* ]]
    # Negative checks — no paraphrases.
    [[ "$out" != *"Agent on behalf of"* ]]
    [[ "$out" != *"agent on behalf of:"* ]]
    [[ "$out" != *"agent on behalf of, alice"* ]]
}

# -- resolve_handle precedence ----------------------------------------------

@test "resolve_handle prefers RESPOND_GH_HANDLE" {
    export RESPOND_GH_HANDLE="env-handle"
    make_stub gh 0 "should-not-be-used"
    [[ "$(resolve_handle)" == "env-handle" ]]
}

@test "resolve_handle falls back to gh api user" {
    unset RESPOND_GH_HANDLE
    # gh api user --jq .login → "octocat"
    cat > "$STUB_BIN/gh" <<'EOF'
#!/usr/bin/env bash
printf 'octocat'
EOF
    chmod +x "$STUB_BIN/gh"
    [[ "$(resolve_handle)" == "octocat" ]]
}

@test "resolve_handle falls back to git config user.name when gh fails" {
    unset RESPOND_GH_HANDLE
    cat > "$STUB_BIN/gh" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$STUB_BIN/gh"
    cat > "$STUB_BIN/git" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "config" && "$2" == "user.name" ]]; then
    printf 'paul-fallback'
    exit 0
fi
exit 1
EOF
    chmod +x "$STUB_BIN/git"
    [[ "$(resolve_handle)" == "paul-fallback" ]]
}

@test "resolve_handle dies when nothing resolves" {
    unset RESPOND_GH_HANDLE
    cat > "$STUB_BIN/gh"  <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    cat > "$STUB_BIN/git" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$STUB_BIN/gh" "$STUB_BIN/git"
    run resolve_handle
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"could not resolve"* ]]
}

# -- End-to-end argument parsing (script entrypoint) -------------------------

@test "script exits non-zero on missing --pr" {
    run bash "$POST_REPLY_SH" --thread --comment-id 1 --body "hi"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"missing --pr"* ]]
}

@test "script exits non-zero on missing --body" {
    run bash "$POST_REPLY_SH" --thread --pr 42 --comment-id 1
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"missing --body"* ]]
}

@test "script rejects --comment-id with --issue mode" {
    run bash "$POST_REPLY_SH" --issue --pr 42 --comment-id 1 --body "hi"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"not valid for --issue"* ]]
}

@test "script rejects both --thread and --issue passed together" {
    run bash "$POST_REPLY_SH" --thread --issue --pr 42 --comment-id 1 --body "hi"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"cannot combine"* ]]
}

@test "script rejects --thread passed twice" {
    run bash "$POST_REPLY_SH" --thread --thread --pr 42 --comment-id 1 --body "hi"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"more than once"* ]]
}

@test "script rejects unknown args" {
    run bash "$POST_REPLY_SH" --pizza --pr 1 --body x
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"unknown argument"* ]]
}
