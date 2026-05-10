#!/usr/bin/env bats
#
# Tests for skills/file-handler/scripts/skillz.sh.
#
# The script is sourced so the BASH_SOURCE != $0 guard skips main, and
# each test can drive individual subcommand functions directly with a
# scratch SKILLZ_DIR under $BATS_TEST_TMPDIR.

setup() {
    REPO_ROOT="$(cd "${BATS_TEST_FILENAME%/*}/../.." && pwd)"
    SKILLZ_SH="$REPO_ROOT/skills/file-handler/scripts/skillz.sh"
    export SKILLZ_DIR="$BATS_TEST_TMPDIR/.skillz"
    # shellcheck disable=SC1090
    source "$SKILLZ_SH"
}

# -- skillz_validate_segment -------------------------------------------------

@test "validate_segment accepts kebab-case slug" {
    run skillz_validate_segment slug "release-cut"
    [ "$status" -eq 0 ]
}

@test "validate_segment accepts slug with dot extension" {
    run skillz_validate_segment slug "release.md"
    [ "$status" -eq 0 ]
}

@test "validate_segment rejects empty value" {
    run skillz_validate_segment slug ""
    [ "$status" -eq 2 ]
    [[ "$output" == *"must not be empty"* ]]
}

@test "validate_segment rejects path traversal" {
    run skillz_validate_segment type "../etc"
    [ "$status" -eq 2 ]
    [[ "$output" == *"must not contain"* ]]
}

@test "validate_segment rejects forward slash" {
    run skillz_validate_segment slug "a/b"
    [ "$status" -eq 2 ]
    [[ "$output" == *"path separators"* ]]
}

@test "validate_segment rejects backslash" {
    run skillz_validate_segment slug 'a\b'
    [ "$status" -eq 2 ]
    [[ "$output" == *"path separators"* ]]
}

@test "validate_segment rejects leading dot" {
    run skillz_validate_segment slug ".hidden"
    [ "$status" -eq 2 ]
    [[ "$output" == *"must not start with"* ]]
}

@test "validate_segment rejects space" {
    run skillz_validate_segment slug "has space"
    [ "$status" -eq 2 ]
    [[ "$output" == *"may only contain"* ]]
}

# -- skillz_root + skillz_path ------------------------------------------------

@test "root honors SKILLZ_DIR override" {
    [[ "$(skillz_root)" == "$BATS_TEST_TMPDIR/.skillz" ]]
}

@test "root defaults to PWD/.skillz when SKILLZ_DIR unset" {
    unset SKILLZ_DIR
    cd "$BATS_TEST_TMPDIR"
    [[ "$(skillz_root)" == "$BATS_TEST_TMPDIR/.skillz" ]]
}

@test "path joins type and slug under root" {
    run skillz_path note hello
    [ "$status" -eq 0 ]
    [[ "$output" == "$BATS_TEST_TMPDIR/.skillz/note/hello" ]]
}

# -- skillz_save_file --------------------------------------------------------

@test "save_file with inline content writes file and prints path" {
    run skillz_save_file note hello "hello world"
    [ "$status" -eq 0 ]
    [[ "$output" == "$SKILLZ_DIR/note/hello" ]]
    [[ "$(cat "$SKILLZ_DIR/note/hello")" == "hello world" ]]
}

@test "save_file reads stdin when content arg omitted" {
    run bash -c "printf 'line1\nline2\n' | source '$SKILLZ_SH' >/dev/null; printf 'line1\nline2\n' | { source '$SKILLZ_SH'; skillz_save_file plan release; }"
    [ "$status" -eq 0 ]
    [[ "$(cat "$SKILLZ_DIR/plan/release")" == $'line1\nline2' ]]
}

@test "save_file creates type directory on demand" {
    [ ! -d "$SKILLZ_DIR/new-type" ]
    skillz_save_file new-type slug "x" >/dev/null
    [ -d "$SKILLZ_DIR/new-type" ]
    [ -f "$SKILLZ_DIR/new-type/slug" ]
}

@test "save_file rejects missing args with exit 2" {
    run skillz_save_file note
    [ "$status" -eq 2 ]
    [[ "$output" == *"save_file"* ]]
}

@test "save_file rejects bad slug" {
    run skillz_save_file note "bad/slug" "x"
    [ "$status" -eq 2 ]
}

@test "save_file overwrites existing file" {
    skillz_save_file note hello "v1" >/dev/null
    skillz_save_file note hello "v2" >/dev/null
    [[ "$(cat "$SKILLZ_DIR/note/hello")" == "v2" ]]
}

# -- skillz_get_file ---------------------------------------------------------

@test "get_file prints saved content" {
    skillz_save_file note hello "hello world" >/dev/null
    run skillz_get_file note hello
    [ "$status" -eq 0 ]
    [[ "$output" == "hello world" ]]
}

@test "get_file exits 1 when file missing" {
    run skillz_get_file note nope
    [ "$status" -eq 1 ]
    [[ "$output" == *"not found"* ]]
}

@test "get_file rejects wrong arg count with exit 2" {
    run skillz_get_file note
    [ "$status" -eq 2 ]
}

@test "get_file rejects bad type" {
    run skillz_get_file ".." hello
    [ "$status" -eq 2 ]
}

# -- skillz_search_files -----------------------------------------------------

@test "search_files finds title matches" {
    skillz_save_file note release-notes "v1 ships tuesday" >/dev/null
    skillz_save_file note todo "ship the thing" >/dev/null
    run skillz_search_files release
    [ "$status" -eq 0 ]
    [[ "$output" == *"## titles"* ]]
    [[ "$output" == *"release-notes"* ]]
}

@test "search_files finds body matches with line numbers" {
    skillz_save_file note alpha "first
ship the thing
last" >/dev/null
    run skillz_search_files "ship the thing"
    [ "$status" -eq 0 ]
    [[ "$output" == *"## bodies"* ]]
    [[ "$output" == *"alpha:2:ship the thing"* ]]
}

@test "search_files returns both sections when title and body match" {
    skillz_save_file note ship-it "ship the thing" >/dev/null
    run skillz_search_files ship
    [ "$status" -eq 0 ]
    [[ "$output" == *"## titles"* ]]
    [[ "$output" == *"## bodies"* ]]
}

@test "search_files --type narrows scope" {
    skillz_save_file note todo "ship the thing" >/dev/null
    skillz_save_file plan release "ship the thing" >/dev/null
    run skillz_search_files ship --type plan
    [ "$status" -eq 0 ]
    [[ "$output" == *"plan/release"* ]]
    [[ "$output" != *"note/todo"* ]]
}

@test "search_files exits 1 when no matches" {
    skillz_save_file note alpha "nothing here" >/dev/null
    run skillz_search_files zzzz-no-match
    [ "$status" -eq 1 ]
}

@test "search_files exits 1 when storage root missing" {
    run skillz_search_files anything
    [ "$status" -eq 1 ]
}

@test "search_files rejects missing query with exit 2" {
    run skillz_search_files
    [ "$status" -eq 2 ]
}

@test "search_files rejects unknown flag with exit 2" {
    run skillz_search_files --bogus value
    [ "$status" -eq 2 ]
}

@test "search_files rejects bad --type" {
    run skillz_search_files query --type "../etc"
    [ "$status" -eq 2 ]
}

# -- main dispatch -----------------------------------------------------------

@test "main exits 2 when invoked with no args" {
    run bash "$SKILLZ_SH"
    [ "$status" -eq 2 ]
}

@test "main exits 2 on unknown subcommand" {
    run bash "$SKILLZ_SH" frobnicate
    [ "$status" -eq 2 ]
    [[ "$output" == *"unknown subcommand"* ]]
}

@test "main --help prints usage" {
    run bash "$SKILLZ_SH" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"skillz"* ]]
    [[ "$output" == *"save_file"* ]]
    [[ "$output" == *"get_file"* ]]
    [[ "$output" == *"search_files"* ]]
}

@test "main save_file end-to-end via subprocess" {
    run bash "$SKILLZ_SH" save_file note end2end "content"
    [ "$status" -eq 0 ]
    [[ "$(cat "$SKILLZ_DIR/note/end2end")" == "content" ]]
}

@test "main get_file end-to-end via subprocess" {
    bash "$SKILLZ_SH" save_file note hello "hi" >/dev/null
    run bash "$SKILLZ_SH" get_file note hello
    [ "$status" -eq 0 ]
    [[ "$output" == "hi" ]]
}
