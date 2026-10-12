"""The proposer seam: the reflection request text is frozen per edit mode, and the answer check keeps its error."""
from __future__ import annotations

import pytest

from skillz_experiments.search import accept_proposal, reflection_request

STE = ("Write every proposed Markdown component in ASD-STE100 Simplified Technical English: "
       "active voice, present tense, one instruction per sentence, at most 20 words per procedural sentence, "
       "and at most 25 words per descriptive sentence. "
       "Return complete component contents. Do not alter independent checks or permissions.\n")
PROSE = "Improve only the supplied skill text components. Preserve the helper CLI contract. " + STE
CLI_HEAD = ("Improve the supplied skill text and helper script components. "
             "Keep each script's command-line contract and output format. "
             "The fromargs package is the default for a wedged Python source. "
             "Use one parser. "
             "Print JSON errors on stderr. "
             "Exit with code 2 for a usage error and code 3 for a failed input contract. "
             "Print only results on stdout. "
             "A non-Python helper keeps its language. "
             "A Python helper may use a stdlib `argparse` helper only when the skill has no wedge.toml "
             "that can build a fromargs target. "
             "The @new-cli component adds one new fromargs wedge target as TOML with the keys name and module. "
             "The name matches [a-z][a-z0-9-]{1,39} and is new. "
             "The package is the name with each hyphen changed to an underscore. "
             "The module imports only the standard library, its own package, fromargs, and the site layer. "
             "The module defines a top-level main, and the entry is <package>:main. "
             "The host builds the target to scripts/<name>.pyz. "
             "SKILL.md calls it with python3 scripts/<name>.pyz. "
             "The host rejects a second target. ")
PROSE_CLI = CLI_HEAD + STE


@pytest.mark.ac("AC-1")
def test_the_prose_request_text_and_schema_are_pinned() -> None:
    prompt, schema = reflection_request("prose", {"SKILL.md": "t"}, {}, ["SKILL.md"])
    assert prompt == PROSE + '{"candidate": {"SKILL.md": "t"}, "feedback": {}}'
    assert schema == {"type": "object", "properties": {"SKILL.md": {"type": "string"}},
                      "required": ["SKILL.md"], "additionalProperties": False}


@pytest.mark.ac("AC-1")
def test_the_prose_and_cli_request_text_and_schema_are_pinned() -> None:
    components = ["SKILL.md", "@new-cli"]
    prompt, schema = reflection_request("prose+cli", {name: "t" for name in components}, {}, components)
    assert prompt == PROSE_CLI + '{"candidate": {"SKILL.md": "t", "@new-cli": "t"}, "feedback": {}}'
    assert schema == {"type": "object",
                      "properties": {"SKILL.md": {"type": "string"}, "@new-cli": {"type": "string"}},
                      "required": components, "additionalProperties": False}


@pytest.mark.ac("AC-2")
@pytest.mark.parametrize("answer", [{"SKILL.md": "x", "extra": "y"}, {}, {"SKILL.md": 1}])
def test_an_answer_with_other_keys_or_a_non_text_value_is_refused(answer: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="reflection keys differ from the frozen components"):
        _ = accept_proposal(answer, ["SKILL.md"])


@pytest.mark.ac("AC-2")
def test_an_answer_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(ValueError, match="expected a JSON object"):
        _ = accept_proposal(["SKILL.md"], ["SKILL.md"])


def test_a_matching_answer_is_returned() -> None:
    assert accept_proposal({"SKILL.md": "x"}, ["SKILL.md"]) == {"SKILL.md": "x"}
