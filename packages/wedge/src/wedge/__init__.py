"""wedge: shiv a fromargs CLI into a skill as a content-addressed .pyz.

The build-only bundle command writes an executable skill archive directly under scripts.
Legacy lock, check, and publish commands retain their launcher and release workflows.

See wedge bundle, wedge build, wedge lock, wedge check, and wedge publish.
"""

from wedge._build import (
    SiteLayer,
    build_from_layer,
    layer_import_names,
    open_site_layer,
    populate_site_layer,
    resolve_target,
    stage_sources,
)
from wedge._bundle import bundle_path
from wedge._cli import main
from wedge._config import TARGET_KEYS, ConfigError, WedgeConfig, defaults_path, load_targets, parse_targets
from wedge._key import BuildPaths, first_party_files

__all__ = [
    "TARGET_KEYS",
    "BuildPaths",
    "ConfigError",
    "SiteLayer",
    "WedgeConfig",
    "build_from_layer",
    "bundle_path",
    "defaults_path",
    "first_party_files",
    "layer_import_names",
    "load_targets",
    "main",
    "open_site_layer",
    "parse_targets",
    "populate_site_layer",
    "resolve_target",
    "stage_sources",
]
