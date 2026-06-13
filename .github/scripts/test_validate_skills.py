#!/usr/bin/env python3
"""Unit tests for validate_skills.py."""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import validate_skills  # noqa: E402

VALID_BODY = """---
name: {name}
description: a test skill
license: MIT
---

body
"""


class ValidateSkillsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = Path.cwd()
        self.tmpdir = Path(tempfile.mkdtemp(prefix="validate-skills-"))
        self.addCleanup(self._restore)
        os.chdir(self.tmpdir)

    def _restore(self) -> None:
        os.chdir(self._cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, rel: str, content: str) -> None:
        path = self.tmpdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_skill(self, name: str, parent: str = "skills") -> None:
        self._write(f"{parent}/{name}/SKILL.md", VALID_BODY.format(name=name))

    def _run(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = validate_skills.main()
        return rc, out.getvalue(), err.getvalue()

    def test_valid_skill_passes(self) -> None:
        self._write_skill("foo")
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("validated 1", out)

    def test_skills_dir_missing(self) -> None:
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("skills/ directory not found", err)

    def test_no_skill_files(self) -> None:
        (self.tmpdir / "skills").mkdir()
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("no SKILL.md files found", err)

    def test_stray_outside_skills_fails(self) -> None:
        # Guard against a copy-pasted plugin tree silently passing validation.
        self._write_skill("foo")
        self._write_skill("bar", parent="plugins/other-plugin/skills")
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("plugins/other-plugin/skills/bar/SKILL.md", err)
        self.assertIn("not at the documented path", err)

    def test_nested_subskill_fails(self) -> None:
        self._write("skills/foo/bar/SKILL.md", VALID_BODY.format(name="bar"))
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("nested sub-skills are not supported", err)

    def test_hidden_dirs_skipped(self) -> None:
        self._write_skill("foo")
        self._write(".github/SKILL.md", VALID_BODY.format(name="github"))
        self._write(".cache/plugins/x/skills/y/SKILL.md", VALID_BODY.format(name="y"))
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("validated 1", out)

    def test_missing_frontmatter(self) -> None:
        self._write("skills/foo/SKILL.md", "no frontmatter here\n")
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("missing or malformed YAML frontmatter", err)

    def test_invalid_yaml(self) -> None:
        # Unterminated quoted string -> YAMLError.
        self._write(
            "skills/foo/SKILL.md",
            '---\nname: foo\ndescription: "unterminated\n---\n',
        )
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("invalid YAML frontmatter", err)

    def test_frontmatter_not_a_mapping(self) -> None:
        self._write(
            "skills/foo/SKILL.md",
            "---\n- just\n- a\n- list\n---\n",
        )
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("must be a YAML mapping", err)

    def test_name_dir_mismatch(self) -> None:
        self._write("skills/foo/SKILL.md", VALID_BODY.format(name="bar"))
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("does not match parent directory", err)

    def test_invalid_kebab_case(self) -> None:
        self._write("skills/Foo_Bar/SKILL.md", VALID_BODY.format(name="Foo_Bar"))
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("not kebab-case", err)

    def test_missing_description(self) -> None:
        self._write("skills/foo/SKILL.md", "---\nname: foo\n---\n\nbody\n")
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("missing required key 'description'", err)

    def test_disallowed_keys(self) -> None:
        self._write(
            "skills/foo/SKILL.md",
            "---\nname: foo\ndescription: x\nbogus: 1\n---\n",
        )
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("disallowed frontmatter keys", err)
        self.assertIn("bogus", err)

    def test_description_at_limit_passes(self) -> None:
        desc = "a" * validate_skills.DESCRIPTION_MAX_LEN
        self._write(
            "skills/foo/SKILL.md",
            f"---\nname: foo\ndescription: {desc}\n---\n",
        )
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("validated 1", out)

    def test_description_over_limit_fails(self) -> None:
        desc = "a" * (validate_skills.DESCRIPTION_MAX_LEN + 1)
        self._write(
            "skills/foo/SKILL.md",
            f"---\nname: foo\ndescription: {desc}\n---\n",
        )
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("'description' is 1025 characters", err)
        self.assertIn("Codex limit", err)

    def test_allowed_optional_keys_pass(self) -> None:
        self._write(
            "skills/foo/SKILL.md",
            "---\nname: foo\ndescription: x\nlicense: MIT\nallowed-tools: Read,Write\n---\n",
        )
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("validated 1", out)


BODY_SKILL = """---
name: {name}
description: a test skill
---

{body}
"""


class ValidateBodyTest(unittest.TestCase):
    """Cover the body-portability gate (validate_skills.validate_body).

    Curd 3 added validate_body() to flag Claude-only tool names and
    harness-coupled CLI fragments baked into a skill BODY as instruction
    logic — they silently no-op on Codex / opencode. The gate is scope-aware:
    a token is allowed only under an explicitly Claude-Code-scoped heading (or
    on a line carrying the same explicit label), and the `/plugin` matcher
    must not fire on documentation paths like `/plugin-dev` or
    `/reference/plugins/`. These tests lock that contract — without them a
    regression that drops the gate, broadens it to false-positive on doc
    paths, or loses the scope exemption would pass CI silently.
    """

    def _body_errors(self, body: str, name: str = "foo") -> list[str]:
        path = Path("skills") / name / "SKILL.md"
        with tempfile.TemporaryDirectory() as tmp:
            full = Path(tmp) / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(BODY_SKILL.format(name=name, body=body), encoding="utf-8")
            return validate_skills.validate_body(full)

    def test_body_violation_fails(self) -> None:
        # Each Claude-coupled token in unscoped body logic must be flagged.
        cases = {
            "Ask via AskUserQuestion to confirm.": "AskUserQuestion",
            "Track steps with TodoWrite.": "TodoWrite",
            "Run claude mcp add context7 to wire it.": "claude mcp add",
            "Then run /plugin to install.": "/plugin",
        }
        for body, label in cases.items():
            with self.subTest(token=label):
                errors = self._body_errors(body)
                self.assertEqual(
                    len(errors), 1, f"{label!r} body should yield exactly one error, got {errors}"
                )
                self.assertIn("harness-neutral", errors[0])
                self.assertIn(label, errors[0])

    def test_per_harness_scoped_heading_passes(self) -> None:
        # A token under an explicitly Claude-Code-scoped heading is deliberate.
        body = (
            "Default portable instructions here.\n\n"
            "## Claude Code only\n\n"
            "Ask via AskUserQuestion and track with TodoWrite.\n"
        )
        self.assertEqual(self._body_errors(body), [])

    def test_per_harness_scoped_inline_passes(self) -> None:
        # The exemption also applies when the offending line itself carries the
        # explicit Claude-Code label, not only via a preceding heading.
        body = "On Claude Code, ask via AskUserQuestion; elsewhere ask in plain text.\n"
        self.assertEqual(self._body_errors(body), [])

    def test_inline_scope_lead_in_list_item_passes(self) -> None:
        # A bulleted per-harness clause is a legitimate lead-in too — the
        # leading "- " must not defeat the inline-scope exemption.
        body = "- On Claude Code, track steps with TodoWrite.\n"
        self.assertEqual(self._body_errors(body), [])

    def test_incidental_claude_code_substring_does_not_exempt(self) -> None:
        # The inline exemption is anchored to a scope LEAD-IN, not any
        # appearance of the substring "claude-code". A line that mentions
        # `claude-code` for an unrelated reason — here the Serena CONTEXT name,
        # exactly the serena-config:50 shape that motivated tightening this —
        # must NOT exempt a coupled token planted on the same line. Without the
        # anchor a future edit that drops AskUserQuestion onto such a line would
        # pass CI silently — the whitewash this gate exists to stop.
        body = (
            "Pick a Serena context (`claude-code`, `ide`); "
            "then ask via AskUserQuestion.\n"
        )
        errors = self._body_errors(body)
        self.assertEqual(len(errors), 1, f"incidental substring must not exempt, got {errors}")
        self.assertIn("AskUserQuestion", errors[0])

    def test_trailing_scope_mention_does_not_exempt(self) -> None:
        # Only a lead-in scopes the line. A scope label that appears AFTER the
        # coupled token is not a deliberate per-harness clause and must not
        # whitewash it.
        errors = self._body_errors("Use AskUserQuestion (Claude Code only).\n")
        self.assertEqual(len(errors), 1, f"trailing scope must not exempt, got {errors}")
        self.assertIn("AskUserQuestion", errors[0])

    def test_scope_resets_after_unscoped_heading(self) -> None:
        # A Claude-scoped section must NOT leak its exemption into the next,
        # unscoped section — otherwise one labelled heading whitewashes the
        # whole rest of the file.
        body = (
            "## Claude Code only\n\n"
            "Use AskUserQuestion here — fine.\n\n"
            "## General usage\n\n"
            "Use AskUserQuestion here too — should fail.\n"
        )
        errors = self._body_errors(body)
        self.assertEqual(len(errors), 1, f"expected one error from the unscoped section, got {errors}")
        self.assertIn("AskUserQuestion", errors[0])

    def test_doc_paths_not_false_positive(self) -> None:
        # The `/plugin` matcher uses a negative lookahead so documentation
        # references do not trip the gate; only the bare command does.
        body = (
            "See the /plugin-dev skill and /reference/plugins/ docs.\n"
            "The /plugins marketplace lists everything.\n"
        )
        self.assertEqual(self._body_errors(body), [])

    def test_bare_plugin_command_fails(self) -> None:
        # Guard the other side of the lookahead: a bare /plugin command (here
        # followed by end-of-line) is a real Claude-Code coupling and must fail,
        # so the doc-path exclusion can't be widened into a blanket pass.
        errors = self._body_errors("Install it by running /plugin\n")
        self.assertEqual(len(errors), 1, f"bare /plugin should fail, got {errors}")
        self.assertIn("/plugin", errors[0])

    def test_clean_body_passes(self) -> None:
        body = "Ask via your harness's interactive-prompt mechanism; if none, ask in plain text.\n"
        self.assertEqual(self._body_errors(body), [])


if __name__ == "__main__":
    unittest.main()
