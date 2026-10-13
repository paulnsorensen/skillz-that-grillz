from __future__ import annotations

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._contract import Contract, parse
from skillz_experiments.run._plan import editable_files

FILES = {"SKILL.md": "a", "references/r.md": "b", "scripts/run.py": "c", "scripts/other.py": "d"}


def contract_with(editable: list[str] | None) -> Contract:
    document: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": "echo-skill",
                                   "invocation": "$echo-skill run", "kinds": {"echo": {"grader": "exact-json"}}}
    if editable is not None:
        document["editable"] = editable
    return parse(document, "skill")


@pytest.mark.ac("gh-294:AC-3")
def test_prose_with_explicit_non_markdown_editable_names_stops_and_names_each() -> None:
    contract = contract_with(["SKILL.md", "scripts/run.py", "scripts/other.py"])
    with pytest.raises(CodedError) as caught:
        _ = editable_files(FILES, contract, "prose")
    assert caught.value.code == "editable-needs-cli"
    message = str(caught.value)
    assert "scripts/run.py" in message and "scripts/other.py" in message
    assert "SKILL.md" not in message.replace("--edit prose+cli", "")
    assert "prose+cli" in message


@pytest.mark.ac("gh-294:AC-4")
def test_prose_cli_and_prose_without_an_explicit_list_plan_without_the_stop() -> None:
    explicit = contract_with(["SKILL.md", "scripts/run.py"])
    assert editable_files(FILES, explicit, "prose+cli") == ["SKILL.md", "scripts/run.py"]
    derived = contract_with(None)
    assert editable_files(FILES, derived, "prose") == ["SKILL.md", "references/r.md"]
