"""The wedge package exports the API that skillz uses, so no caller reads a private module."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from collections.abc import Callable
from typing import cast

import pytest

import wedge

SCRIPTS = Path(__file__).resolve().parents[4] / ".github" / "scripts"
EXPORTED = {
    "BuildPaths", "ConfigError", "SiteLayer", "TARGET_KEYS", "WedgeConfig", "build_from_layer", "bundle_path",
    "defaults_path", "first_party_files", "layer_import_names", "load_targets", "main", "open_site_layer",
    "parse_targets", "populate_site_layer", "resolve_target", "stage_sources",
}


@pytest.mark.ac("gh-391:AC-1")
def test_the_crust_lists_and_resolves_every_name_skillz_uses() -> None:
    assert set(wedge.__all__) == EXPORTED
    assert all(hasattr(wedge, name) for name in EXPORTED)


@pytest.mark.ac("gh-391:AC-2")
def test_no_skillz_module_imports_a_private_wedge_module() -> None:
    spec = importlib.util.spec_from_file_location("check_architecture", SCRIPTS / "check_architecture.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    namespace = cast("dict[str, object]", vars(module))
    assert namespace["ALLOWED"] == frozenset()
    assert cast("Callable[[], int]", namespace["main"])() == 0
