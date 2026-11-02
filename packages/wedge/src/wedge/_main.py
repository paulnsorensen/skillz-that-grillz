"""Source for the launcher stored as ``__main__.py`` in each archive."""

MAIN_SOURCE = '''\
import importlib
import importlib.machinery
import io
import linecache
import json
import os
import re
import shutil
import sys
import tokenize
from zipfile import ZipFile

archive_path = os.path.abspath(sys.argv[0])
with ZipFile(archive_path) as archive:
    environment = json.loads(archive.read("environment.json"))
    has_tree = sys.implementation.cache_tag + "/" in archive.NameToInfo

if sys.version_info < (3, 11):
    if os.name == "posix":
        import subprocess

        for minor in reversed(environment["python_minors"]):
            candidate = shutil.which("python" + minor)
            if candidate is not None:
                flags = ["-I"] if sys.flags.isolated else [
                    flag for active, flag in (
                        (sys.flags.ignore_environment, "-E"),
                        (sys.flags.no_user_site, "-s"),
                    ) if active
                ]
                if sys.flags.no_site:
                    flags.append("-S")
                try:
                    probe = subprocess.run(
                        [candidate, *flags, "-c",
                         "import sys; sys.exit('.'.join(map(str, sys.version_info[:2])) != sys.argv[1])", minor],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    if probe.returncode:
                        continue
                    os.execv(candidate, [candidate, *flags, archive_path, *sys.argv[1:]])
                except OSError:
                    continue
    found = ".".join(str(part) for part in sys.version_info[:2])
    print(json.dumps({"error": f"Python {found} found; Python 3.11 or newer required", "exit_code": 3}), file=sys.stderr)
    sys.exit(3)

site_path = archive_path + "/site-packages"
tree_path = archive_path + "/" + sys.implementation.cache_tag
sys.path.insert(0, site_path)
if has_tree:
    sys.path.insert(0, tree_path)


class _SourceFallbackFinder:
    def find_spec(self, fullname, path=None, target=None):
        if not has_tree or path is None:
            return None
        mapped = [site_path + item[len(tree_path):] for item in path if item.startswith(tree_path + "/")]
        if not mapped:
            return None
        tree_spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if tree_spec is not None and tree_spec.loader is not None:
            return tree_spec
        return importlib.machinery.PathFinder.find_spec(fullname, mapped, target)


_original_getlines = linecache.getlines


def _archive_getlines(filename, module_globals=None):
    if filename.startswith("site-packages/"):
        cached = linecache.cache.get(filename)
        if cached is not None and len(cached) == 4:
            return cached[2]
        with ZipFile(archive_path) as archive:
            try:
                source_bytes = archive.read(filename)
            except KeyError:
                pass
            else:
                encoding, _ = tokenize.detect_encoding(io.BytesIO(source_bytes).readline)
                source = source_bytes.decode(encoding)
                lines = io.StringIO(source, newline=None).readlines()
                linecache.cache[filename] = (len(source), None, lines, filename)
                return lines
    return _original_getlines(filename, module_globals)


linecache.getlines = _archive_getlines
if sys.version_info < (3, 13) and sys.excepthook is sys.__excepthook__:
    def _archive_excepthook(exc_type, exc_value, exc_traceback):
        import traceback
        traceback.print_exception(exc_type, exc_value, exc_traceback)

    sys.excepthook = _archive_excepthook
sys.meta_path.insert(sys.meta_path.index(importlib.machinery.PathFinder), _SourceFallbackFinder())


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
