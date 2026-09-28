"""wedge: shiv a fromargs CLI into a skill as a content-addressed .pyz.

The build-only bundle command writes an executable skill archive directly under scripts.
Legacy lock, check, and publish commands retain their launcher and release workflows.

See wedge bundle, wedge build, wedge lock, wedge check, and wedge publish.
"""

from wedge._cli import main

__all__ = ["main"]
