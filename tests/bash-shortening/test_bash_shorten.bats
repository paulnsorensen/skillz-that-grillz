#!/usr/bin/env bats
#
# Tests for skills/bash-shortening/scripts/bash-shorten.py.
#
# Three layers:
#   1. CLI surface  — flag/arg parsing, error paths, --list, --explain.
#   2. Engine       — dry-run vs --apply, stdin/stdout, no-op handling.
#   3. Per-rule end-to-end — every rule fires on a positive fixture
#      through the real CLI (complements the in-process --self-test
#      with subprocess-level coverage).

setup() {
    REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"
    SCRIPT="$REPO_ROOT/skills/bash-shortening/scripts/bash-shorten.py"
    FIXTURE="$BATS_TEST_TMPDIR/sample.sh"
}

# Pipe `$1` through the script in stdin mode and assert the stdout
# contains `$2` verbatim. Bats' `run` clobbers pipes, so we capture
# manually and use a printf+grep idiom.
assert_rewrite() {
    local input="$1" expected="$2" got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" - 2>/dev/null)"
    if [[ "$got" != *"$expected"* ]]; then
        printf 'input:    %s\nexpected: %s\ngot:      %s\n' \
            "$input" "$expected" "$got" >&2
        return 1
    fi
}

# -- CLI surface -------------------------------------------------------------

@test "--self-test exits 0 with all embedded fixtures green" {
    run python3 "$SCRIPT" --self-test
    [ "$status" -eq 0 ]
    [[ "$output" == *"passed"* ]]
    # Asserts no FAIL lines leaked through.
    [[ "$output" != *"FAIL"* ]]
}

@test "--list emits every documented rule id" {
    run python3 "$SCRIPT" --list
    [ "$status" -eq 0 ]
    for rule in basename dirname sed-replace-first sed-replace-all \
                echo-wc-c cut-c-substring \
                expr-arith-vars expr-increment expr-arith-literal \
                combined-tests test-numeric empty-default param-default \
                mkdir-guard for-range-expansion \
                backticks legacy-null-check empty-string-eq \
                find-exec-rm-delete cat-file-pipe-grep \
                sed-replace-to-sd grep-fixed-to-rg find-name-to-fd; do
        [[ "$output" == *"$rule"* ]] || {
            echo "missing rule in --list output: $rule" >&2
            return 1
        }
    done
}

@test "--explain prints id, description, pattern, examples for known rule" {
    run python3 "$SCRIPT" --explain test-numeric
    [ "$status" -eq 0 ]
    [[ "$output" == *"id:"* ]]
    [[ "$output" == *"description:"* ]]
    [[ "$output" == *"pattern:"* ]]
    [[ "$output" == *"examples:"* ]]
}

@test "--explain unknown rule exits 1 with diagnostic" {
    run python3 "$SCRIPT" --explain bogus
    [ "$status" -eq 1 ]
    [[ "$output" == *"unknown rule: bogus"* ]]
}

@test "missing file argument exits 2 (argparse error)" {
    run python3 "$SCRIPT"
    [ "$status" -eq 2 ]
    [[ "$output" == *"file is required"* ]]
}

@test "non-existent file exits 1 with diagnostic" {
    run python3 "$SCRIPT" "$BATS_TEST_TMPDIR/does-not-exist.sh"
    [ "$status" -eq 1 ]
    [[ "$output" == *"not a file"* ]]
}

@test "--apply with stdin is rejected" {
    run bash -c "echo x | python3 \"$SCRIPT\" --apply -"
    [ "$status" -eq 2 ]
    [[ "$output" == *"--apply is incompatible with stdin"* ]]
}

@test "--rules referencing unknown id exits 1" {
    printf 'X=$(basename "$P")\n' > "$FIXTURE"
    run python3 "$SCRIPT" --rules basename,bogus "$FIXTURE"
    [ "$status" -eq 1 ]
    [[ "$output" == *"unknown rules: bogus"* ]]
}

@test "--skip referencing unknown id exits 1" {
    printf 'echo hi\n' > "$FIXTURE"
    run python3 "$SCRIPT" --skip bogus "$FIXTURE"
    [ "$status" -eq 1 ]
    [[ "$output" == *"unknown rules: bogus"* ]]
}

# -- Engine behavior --------------------------------------------------------

@test "dry-run emits unified diff but leaves the file untouched" {
    printf 'X=$(basename "$P")\n' > "$FIXTURE"
    local before_hash
    before_hash="$(shasum "$FIXTURE" | awk '{print $1}')"

    run python3 "$SCRIPT" "$FIXTURE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"---"* ]]
    [[ "$output" == *"+++"* ]]
    [[ "$output" == *"-X="* ]]
    [[ "$output" == *'+X=${P##*/}'* ]]

    local after_hash
    after_hash="$(shasum "$FIXTURE" | awk '{print $1}')"
    [ "$before_hash" = "$after_hash" ]
}

@test "no-op file reports 'no rewrites applicable' and exits 0" {
    printf 'echo hello\nls -la\n' > "$FIXTURE"
    run python3 "$SCRIPT" "$FIXTURE"
    [ "$status" -eq 0 ]
    [[ "$output" == *"no rewrites applicable"* ]]
}

@test "--apply rewrites the file in place and leaves no temp files" {
    printf 'X=$(basename "$P")\n' > "$FIXTURE"
    run python3 "$SCRIPT" --apply "$FIXTURE"
    [ "$status" -eq 0 ]
    grep -q '^X=${P##\*/}$' "$FIXTURE"
    # Asserts the public contract (atomic write, no leftover temp siblings)
    # without coupling to the private temp-file naming convention.
    local fixture_dir leftovers
    fixture_dir="$(dirname "$FIXTURE")"
    leftovers="$(find "$fixture_dir" -maxdepth 1 -name '*.tmp' -not -path "$FIXTURE")"
    [ -z "$leftovers" ]
}

@test "--apply on no-op file does not rewrite" {
    printf 'echo hello\n' > "$FIXTURE"
    local before_hash
    before_hash="$(shasum "$FIXTURE" | awk '{print $1}')"
    run python3 "$SCRIPT" --apply "$FIXTURE"
    [ "$status" -eq 0 ]
    local after_hash
    after_hash="$(shasum "$FIXTURE" | awk '{print $1}')"
    [ "$before_hash" = "$after_hash" ]
}

@test "stdin mode writes rewritten content to stdout" {
    local got
    got="$(printf 'X=$(basename "$P")\n' | python3 "$SCRIPT" -)"
    [[ "$got" == *'X=${P##*/}'* ]]
}

# -- Rule selection ---------------------------------------------------------

@test "--rules subset only fires named rules" {
    printf 'X=$(basename "$P")\nY=$(dirname "$P")\n' > "$FIXTURE"
    run python3 "$SCRIPT" --rules basename --apply "$FIXTURE"
    [ "$status" -eq 0 ]
    grep -q '^X=${P##\*/}$' "$FIXTURE"
    # dirname must remain untouched
    grep -qF 'Y=$(dirname "$P")' "$FIXTURE"
}

@test "--skip excludes a rule even when its pattern matches" {
    printf 'X=$(basename "$P")\nY=$(dirname "$P")\n' > "$FIXTURE"
    run python3 "$SCRIPT" --skip dirname --apply "$FIXTURE"
    [ "$status" -eq 0 ]
    grep -q '^X=${P##\*/}$' "$FIXTURE"
    grep -qF 'Y=$(dirname "$P")' "$FIXTURE"
}

# -- Per-rule end-to-end via stdin (one positive fixture per rule) ---------
#
# These overlap the in-process --self-test by design: this layer proves the
# rules also fire when invoked through the real CLI (env, encoding, argv
# handling). If any of these regress while --self-test still passes, the
# regression is in the CLI plumbing, not the rules.

@test "rule basename rewrites via CLI" {
    assert_rewrite 'FILENAME=$(basename "$FULLPATH")' 'FILENAME=${FULLPATH##*/}'
}

@test "rule dirname rewrites via CLI" {
    assert_rewrite 'DIR=$(dirname "$P")' 'DIR=${P%/*}'
}

@test "rule sed-replace-first rewrites literal patterns" {
    assert_rewrite "X=\$(echo \"\$S\" | sed 's/foo/bar/')" 'X=${S/foo/bar}'
}

@test "rule sed-replace-first SKIPS regex metacharacters" {
    # Negative case: regex metachar must leave the input untouched.
    local input="X=\$(echo \"\$S\" | sed 's/.*/x/')"
    local got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" -)"
    # Strip trailing newline from stdin echo for stable comparison.
    [ "${got%$'\n'}" = "$input" ]
}

@test "rule sed-replace-all rewrites global literal patterns" {
    assert_rewrite "X=\$(echo \"\$S\" | sed 's/old/new/g')" 'X=${S//old/new}'
}

@test "rule echo-wc-c rewrites length idiom" {
    assert_rewrite 'LEN=$(echo -n "$S" | wc -c)' 'LEN=${#S}'
}

@test "rule expr-arith-vars rewrites numeric expr" {
    assert_rewrite 'R=$(expr $A + $B)' 'R=$((A + B))'
}

@test "rule expr-arith-vars handles escaped multiplication" {
    assert_rewrite 'R=$(expr $A \* $B)' 'R=$((A * B))'
}

@test "rule expr-arith-literal rewrites VAR + INT" {
    assert_rewrite 'R=$(expr $A + 1)' 'R=$((A + 1))'
}

@test "rule expr-increment rewrites matched-name self-increment" {
    # expr-increment must claim COUNT=$(expr $COUNT + 1) before
    # expr-arith-literal does (which would emit C=$((C + 1))).
    assert_rewrite 'COUNT=$(expr $COUNT + 1)' '((COUNT++))'
}

@test "rule expr-increment SKIPS mismatched names" {
    # different vars on each side — falls through to expr-arith-literal.
    assert_rewrite 'TOTAL=$(expr $A + 1)' 'TOTAL=$((A + 1))'
}

@test "rule cut-c-substring rewrites cut -c1-N to substring" {
    assert_rewrite 'PRE=$(echo "$NAME" | cut -c1-5)' 'PRE=${NAME:0:5}'
}

@test "rule param-default rewrites positional fallback" {
    assert_rewrite 'if [ -z "$1" ]; then NAME="anon"; fi' 'NAME=${1:-"anon"}'
}

@test "rule for-range-expansion collapses consecutive integers" {
    assert_rewrite 'for i in 1 2 3 4 5; do echo $i; done' \
                   'for i in {1..5}; do echo $i; done'
}

@test "rule for-range-expansion SKIPS non-consecutive sequences" {
    local input='for i in 1 3 5; do echo $i; done'
    local got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" -)"
    [ "${got%$'\n'}" = "$input" ]
}

@test "rule for-range-expansion SKIPS zero-padded literals" {
    local input='for i in 01 02 03; do echo $i; done'
    local got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" -)"
    [ "${got%$'\n'}" = "$input" ]
}

@test "rule combined-tests fuses paired single-bracket tests" {
    assert_rewrite 'if [ -f "$F" ] && [ -r "$F" ]; then' \
                   'if [[ -f "$F" && -r "$F" ]]; then'
}

@test "rule combined-tests SKIPS when nested brackets present" {
    local input='[ "${arr[0]}" = "x" ] && [ "$Y" = "y" ]'
    local got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" -)"
    [ "${got%$'\n'}" = "$input" ]
}

@test "rule test-numeric maps -gt to >" {
    assert_rewrite 'if [ $X -gt 100 ]; then' 'if (( X > 100 )); then'
}

@test "rule test-numeric maps -lt to <" {
    assert_rewrite 'while [ $i -lt 10 ]; do' 'while (( i < 10 )); do'
}

@test "rule empty-default collapses if/then/fi guard" {
    assert_rewrite 'if [ -z "$ENV" ]; then ENV="dev"; fi' 'ENV=${ENV:-"dev"}'
}

@test "rule mkdir-guard collapses redundant directory check" {
    assert_rewrite 'if [ ! -d "$DIR" ]; then mkdir -p "$DIR"; fi' \
                   'mkdir -p "${DIR}"'
}

@test "rule mkdir-guard SKIPS when vars don't match" {
    local input='if [ ! -d "$A" ]; then mkdir -p "$B"; fi'
    local got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" -)"
    [ "${got%$'\n'}" = "$input" ]
}

@test "rule backticks rewrites to dollar-paren" {
    assert_rewrite 'VER=`git rev-parse HEAD`' 'VER=$(git rev-parse HEAD)'
}

@test "rule legacy-null-check maps x-prefix idiom to -z" {
    assert_rewrite '[ "x$VAR" = "x" ]' '[ -z "$VAR" ]'
}

@test "rule legacy-null-check maps not-equal x-prefix idiom to -n" {
    assert_rewrite '[ "x$VAR" != "x" ]' '[ -n "$VAR" ]'
}

@test "rule empty-string-eq maps explicit empty compare to -z" {
    assert_rewrite '[ "$X" = "" ]' '[ -z "$X" ]'
}

@test "rule find-exec-rm-delete swaps -exec rm for -delete" {
    assert_rewrite 'find /tmp -name "*.bak" -exec rm {} \;' \
                   'find /tmp -name "*.bak" -delete'
}

@test "rule cat-file-pipe-grep drops the useless cat" {
    assert_rewrite 'cat /etc/hosts | grep localhost' 'grep localhost /etc/hosts'
}

@test "rule cat-file-pipe-grep preserves quoted file argument" {
    assert_rewrite 'cat "$LOG" | grep -i error' 'grep -i error "$LOG"'
}

# -- Multi-rule integration -------------------------------------------------
#
# A miniature script exercising several rules at once. Catches regressions
# where rule ordering or partial matches corrupt subsequent passes.

@test "applies multiple rules to the same file in one pass" {
    cat > "$FIXTURE" <<'SH'
#!/usr/bin/env bash
FILENAME=$(basename "$FULLPATH")
DIR=$(dirname "$FULLPATH")
LEN=$(echo -n "$FILENAME" | wc -c)
if [ -z "$ENV" ]; then ENV="dev"; fi
if [ ! -d "$DIR" ]; then mkdir -p "$DIR"; fi
[ "x$USER" = "x" ] && exit 1
SH
    run python3 "$SCRIPT" --apply "$FIXTURE"
    [ "$status" -eq 0 ]
    grep -q 'FILENAME=${FULLPATH##\*/}' "$FIXTURE"
    grep -q 'DIR=${FULLPATH%/\*}' "$FIXTURE"
    grep -q 'LEN=${#FILENAME}' "$FIXTURE"
    grep -q 'ENV=${ENV:-"dev"}' "$FIXTURE"
    grep -q 'mkdir -p "${DIR}"' "$FIXTURE"
    grep -qF '[ -z "$USER" ]' "$FIXTURE"
}

# -- Modernize group: opt-in via --include modernize -----------------------

@test "modernize group is OFF by default — sed→sd does not fire without --include" {
    local input="echo \"\$LINE\" | sed 's/foo/bar/g'"
    local got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" -)"
    [ "${got%$'\n'}" = "$input" ]
}

@test "modernize group ON — sed→sd fires with --include modernize" {
    local got
    got="$(printf 'echo "$LINE" | sed '"'"'s/foo/bar/g'"'"'\n' \
        | python3 "$SCRIPT" --include modernize -)"
    [[ "$got" == *"sd 'foo' 'bar' <<< \"\$LINE\""* ]]
}

@test "modernize group ON — grep -F → rg -F fires" {
    local got
    got="$(printf 'grep -F localhost /etc/hosts\n' \
        | python3 "$SCRIPT" --include modernize -)"
    [[ "$got" == *"rg -F localhost /etc/hosts"* ]]
}

@test "modernize group ON — find -name → fd fires" {
    local got
    got="$(printf 'find . -type f -name "*.py"\n' \
        | python3 "$SCRIPT" --include modernize -)"
    [[ "$got" == *'fd -t f "*.py"'* ]]
}

@test "modernize: plain grep (no -F) is NOT migrated to rg (regex flavors differ)" {
    local input='grep PAT /etc/hosts'
    local got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" --include modernize -)"
    [ "${got%$'\n'}" = "$input" ]
}

@test "--include rejects unknown group with diagnostic" {
    printf 'echo hi\n' > "$FIXTURE"
    run python3 "$SCRIPT" --include bogus "$FIXTURE"
    [ "$status" -eq 1 ]
    [[ "$output" == *"unknown groups: bogus"* ]]
}

@test "--list shows group tags and group summary" {
    run python3 "$SCRIPT" --list
    [ "$status" -eq 0 ]
    [[ "$output" == *"(modernize)"* ]]
    [[ "$output" == *"Groups:"* ]]
    [[ "$output" == *"core ("* ]]
    [[ "$output" == *"modernize ("* ]]
}

# -- ast-grep engine ------------------------------------------------------
#
# `sg` (ast-grep) is a hard requirement. The suite asserts the dispatch
# fires on its sg-handled rules and that the missing-sg path produces a
# friendly diagnostic.

@test "basename rewrite goes through sg, captures variable name" {
    local got
    got="$(printf 'X=$(basename "$FULLPATH")\n' | python3 "$SCRIPT" -)"
    [[ "$got" == *'X=${FULLPATH##*/}'* ]]
}

@test "dirname rewrite goes through sg" {
    local got
    got="$(printf 'D=$(dirname "$FULLPATH")\n' | python3 "$SCRIPT" -)"
    [[ "$got" == *'D=${FULLPATH%/*}'* ]]
}

@test "non-sg-handled rules still fire (echo-wc-c via regex path)" {
    local got
    got="$(printf 'LEN=$(echo -n "$S" | wc -c)\n' | python3 "$SCRIPT" -)"
    [[ "$got" == *'LEN=${#S}'* ]]
}

@test "backticks rule rewrites legit command substitution" {
    local got
    got="$(printf 'COUNT=`wc -l < file`\n' | python3 "$SCRIPT" -)"
    [[ "$got" == *'COUNT=$(wc -l < file)'* ]]
}

@test "backticks skips markdown spans in # comments (issue #16)" {
    local input='# Run `bash --help` for help.'
    local got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" -)"
    [ "${got%$'\n'}" = "$input" ]
}

@test "backticks skips quoted heredoc bodies (issue #16)" {
    local input
    input="$(printf "cat <<'EOF'\nliteral \`backticks\`\nEOF\n")"
    local got
    got="$(printf '%s' "$input" | python3 "$SCRIPT" -)"
    [ "${got%$'\n'}" = "${input%$'\n'}" ]
}

@test "report includes basename count" {
    local err
    err="$(printf 'X=$(basename "$FULLPATH")\n' \
        | python3 "$SCRIPT" - 2>&1 >/dev/null)"
    [[ "$err" == *"basename"* ]]
}

@test "missing sg exits with friendly diagnostic" {
    # Run with an empty PATH that excludes sg. Use `env -i` to clear,
    # then add /usr/bin so python3 still resolves.
    local out
    out="$(env -i PATH=/usr/bin:/bin python3 "$SCRIPT" /etc/hosts 2>&1 || true)"
    [[ "$out" == *"requires ast-grep"* ]]
    [[ "$out" == *"/bash-shortening"* ]]
}

# -- Real-world regression fixtures (skipped until bugs land) --------------
# Surfaced by dogfooding the rewriter on ~/Dev/dotfiles. The issue-#16
# backticks cases are no longer skipped — sg handles them correctly and
# they're covered by the engine tests above. Issue-#18 stays skipped
# because test-numeric still goes through the Python regex.

@test "rule test-numeric leaves [[ ... ]] form untouched (issue #18)" {
    skip "blocked on issue #18 — regex bleeds into [[ ]], producing [(( ... ))]"
    local input='if [[ $V -eq 0 ]]; then :; fi'
    local got
    got="$(printf '%s\n' "$input" | python3 "$SCRIPT" -)"
    [ "${got%$'\n'}" = "$input" ]
}
