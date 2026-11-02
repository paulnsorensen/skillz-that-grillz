"""Source for the launcher stored as ``__main__.py`` in each archive."""

MAIN_SOURCE = '''\
import importlib
import importlib.machinery
import json
import os
import re
import sys
from zipfile import ZipFile

archive_path = os.path.abspath(sys.argv[0])
with ZipFile(archive_path) as archive:
    environment = json.loads(archive.read("environment.json"))

site_path = archive_path + "/site-packages"
sys.path.insert(0, site_path)


class _ArchiveMetadataFinder(importlib.machinery.PathFinder):
    _archive = None

    @classmethod
    def find_distributions(cls, context=None):
        from importlib.metadata import DistributionFinder, PathDistribution
        from zipfile import Path

        if context is None:
            context = DistributionFinder.Context()
        if site_path not in context.path:
            yield from importlib.machinery.PathFinder.find_distributions(context)
            return
        name = context.name
        normalized = re.sub(r"[-_.]+", "-", name).lower() if name else None
        for item in context.path:
            if item != site_path:
                yield from importlib.machinery.PathFinder.find_distributions(
                    DistributionFinder.Context(name=name, path=[item])
                )
                continue
            if cls._archive is None:
                cls._archive = Path(archive_path, at="site-packages/")
            for child in cls._archive.iterdir():
                if not child.name.endswith((".dist-info", ".egg-info")):
                    continue
                dist = PathDistribution(child)
                if normalized is None or re.sub(r"[-_.]+", "-", dist.metadata["Name"]).lower() == normalized:
                    yield dist


def _exec_pth_line(sitedir: str, line: str) -> None:
    exec(line)


sys.meta_path[sys.meta_path.index(importlib.machinery.PathFinder)] = _ArchiveMetadataFinder
with ZipFile(archive_path) as archive:
    for name in sorted(archive.namelist()):
        if not (name.startswith("site-packages/") and name.count("/") == 1 and name.endswith(".pth")):
            continue
        for line in archive.read(name).decode("utf-8-sig").splitlines():
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("import ", "import\\t")):
                _exec_pth_line(site_path, line)
                continue
            path = os.path.normpath(os.path.join(site_path, line))
            relative = os.path.relpath(path, archive_path).replace(os.sep, "/") + "/"
            if relative in archive.NameToInfo and path not in sys.path:
                sys.path.append(path)
module, function = environment["entry"].split(":", 1)
entry = importlib.import_module(module)
for attribute in function.split("."):
    entry = getattr(entry, attribute)
sys.exit(entry())
'''
