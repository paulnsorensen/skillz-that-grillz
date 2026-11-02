"""AC-W3: the built .pyz runs standalone under a clean interpreter."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import cast
from pathlib import Path

import pytest

from wedge._build import build
from wedge._zipapp import write_archive


@pytest.mark.parametrize("filename", ["native.so", "native.cpython-312-x86_64-linux-gnu.so", "native.pyd"])
@pytest.mark.parametrize("existing", [False, True])
def test_archive_rejects_native_modules_before_opening_output(tmp_path: Path, filename: str, existing: bool) -> None:
    site = tmp_path / "site" / "app"
    site.mkdir(parents=True)
    _ = (site / filename).write_bytes(b"native module")
    output = tmp_path / "app.pyz"
    if existing:
        _ = output.write_bytes(b"keep this archive")

    with pytest.raises(ValueError, match="native extension modules are not supported"):
        write_archive(site.parent, "app:main", output)

    if existing:
        assert output.read_bytes() == b"keep this archive"
    else:
        assert not output.exists()


@pytest.fixture(scope="module")
def built_pyz(tmp_path_factory: pytest.TempPathFactory, fixture_skill_dir: Path) -> Path:
    out_dir = tmp_path_factory.mktemp("wedge-pyz-runtime")
    result = build(fixture_skill_dir, out_dir)
    return result.path


def _run_pyz(pyz: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(pyz), *args],
        capture_output=True,
        text=True,
        cwd="/",
        env={"PATH": "/usr/bin:/bin"},
    )


@pytest.mark.ac("wedge-skill-packaging:AC-W3")
def test_pyz_runs_under_a_clean_interpreter_with_no_project_on_path(built_pyz: Path) -> None:
    result = _run_pyz(built_pyz, "--json", "wheels", "list")
    assert result.returncode == 0, result.stderr
    payload = cast(object, json.loads(result.stdout))
    assert isinstance(payload, list)
    assert payload


@pytest.mark.ac("gh-581:AC-1")
def test_pyz_runs_without_runtime_cache_or_extraction(built_pyz: Path, tmp_path: Path) -> None:
    roots = [tmp_path / name for name in ("home", "cache", "temp")]
    for root in roots:
        root.mkdir()
        root.chmod(0o555)
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(built_pyz), "--json", "wheels", "list"],
            capture_output=True,
            text=True,
            cwd="/",
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(roots[0]),
                "XDG_CACHE_HOME": str(roots[1]),
                "TMPDIR": str(roots[2]),
            },
        )
        assert result.returncode == 0, result.stderr
        assert isinstance(json.loads(result.stdout), list)
        assert all(list(root.iterdir()) == [] for root in roots)
    finally:
        for root in roots:
            root.chmod(0o755)


@pytest.mark.ac("gh-581:AC-1")
@pytest.mark.parametrize(("directory", "metadata"), [("gepa-0.1.4.dist-info", "METADATA"), ("gepa.egg-info", "PKG-INFO")])
def test_archive_exposes_nested_distribution_metadata(tmp_path: Path, directory: str, metadata: str) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "probe.py").write_text(
        """from importlib.metadata import distributions, version
import sys
import zipfile

def main():
    archive = sys.argv[0]
    reads = 0
    original = zipfile.ZipFile._RealGetContents
    def counted(instance):
        nonlocal reads
        if instance.filename == archive:
            reads += 1
        return original(instance)
    zipfile.ZipFile._RealGetContents = counted
    sys.argv[0] = "changed.pyz"
    for _ in range(20):
        assert version("GePa") == "0.1.4"
    assert [d.version for d in distributions(path=[])] == []
    print(version("gepa"))
    assert reads == 1, reads
"""
    )
    dist_info = site / directory
    dist_info.mkdir()
    _ = (dist_info / metadata).write_text("Metadata-Version: 2.1\nName: gepa\nVersion: 0.1.4\n")
    pyz = tmp_path / "probe.pyz"
    write_archive(site, "probe:main", pyz)

    result = _run_pyz(pyz)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.1.4"

@pytest.mark.ac("wedge-skill-packaging:AC-W3")
def test_pyz_self_heals_a_merged_argument(built_pyz: Path) -> None:
    result = _run_pyz(built_pyz, "age", "brie", "--weeks", "2 --dry-run")
    assert result.returncode == 0, result.stderr
    assert "note:" in result.stderr
    payload = cast(dict[str, object], json.loads(result.stdout))
    assert payload["dry_run"] is True


def test_archive_metadata_precedes_host_and_explicit_paths_still_work(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "probe.py").write_text(
        "from importlib.metadata import distributions, version\n"
        + "import sys\n"
        + "def main():\n"
        + "    sys.path.append(sys.argv[1])\n"
        + "    print(version('fixturepkg'))\n"
        + "    print(next(dist.version for dist in distributions(path=[sys.argv[1]]) if dist.metadata['Name'] == 'fixturepkg'))\n"
        + "    print(version('hostonly'))\n"
    )
    archive_metadata = site / "fixturepkg-99.dist-info"
    archive_metadata.mkdir()
    _ = (archive_metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: fixturepkg\nVersion: 99\n")
    host = tmp_path / "host"
    host.mkdir()
    for name, version in (("fixturepkg", "1"), ("hostonly", "2")):
        info = host / f"{name}-{version}.dist-info"
        info.mkdir()
        _ = (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    pyz = tmp_path / "probe.pyz"
    write_archive(site, "probe:main", pyz)
    result = _run_pyz(pyz, str(host))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["99", "1", "2"]


@pytest.mark.ac("gh-581:AC-1")
def test_archive_metadata_with_mixed_paths(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "probe.py").write_text(
        """from importlib.metadata import distributions, version
import sys
def main():
    host, empty = sys.argv[1:]
    archive = next(path for path in sys.path if path.endswith('/site-packages'))
    sys.path.insert(0, empty)
    assert version('fixturepkg') == '99'
    assert [dist.version for dist in distributions(path=[empty, archive]) if dist.metadata['Name'] == 'fixturepkg'] == ['99']
    sys.path.insert(0, host)
    assert version('fixturepkg') == '1'
    assert version('hostonly') == '2'
    assert sorted(dist.version for dist in distributions(path=[host])) == ['1', '2']
    assert [dist.version for dist in distributions(path=[archive])] == ['99']
    assert [dist.version for dist in distributions(path=[host, archive]) if dist.metadata['Name'] == 'fixturepkg'] == ['1', '99']
    assert [dist.version for dist in distributions(path=[archive, host]) if dist.metadata['Name'] == 'fixturepkg'] == ['99', '1']
    assert [dist.version for dist in distributions() if dist.metadata['Name'] == 'fixturepkg'] == ['1', '99']
"""
    )
    bundled = site / "fixturepkg-99.dist-info"
    bundled.mkdir()
    _ = (bundled / "METADATA").write_text("Metadata-Version: 2.1\nName: fixturepkg\nVersion: 99\n")
    host = tmp_path / "host"
    host.mkdir()
    for name, version in (("fixturepkg", "1"), ("hostonly", "2")):
        info = host / f"{name}-{version}.dist-info"
        info.mkdir()
        _ = (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    empty = tmp_path / "empty"
    empty.mkdir()
    pyz = tmp_path / "probe.pyz"
    write_archive(site, "probe:main", pyz)
    result = _run_pyz(pyz, str(host), str(empty))
    assert result.returncode == 0, result.stderr


def test_relative_archive_survives_entrypoint_chdir(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "probe.py").write_text(
        "import os\nfrom importlib.metadata import version\n"
        + "def main():\n    os.chdir('/')\n    print(version('fixturepkg'))\n"
    )
    metadata = site / "fixturepkg-99.dist-info"
    metadata.mkdir()
    _ = (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: fixturepkg\nVersion: 99\n")
    pyz = tmp_path / "probe.pyz"
    write_archive(site, "probe:main", pyz)
    result = subprocess.run(
        [sys.executable, "-I", pyz.name], capture_output=True, text=True, cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "99"


@pytest.mark.ac("gh-581:AC-1")
def test_archive_initializes_bundled_pth_before_entry_import(tmp_path: Path) -> None:
    site = tmp_path / "site"
    vendor = site / "vendor"
    vendor.mkdir(parents=True)
    _ = (vendor / "fixturedep.py").write_text("VALUE = 42\n")
    _ = (site / "fixturedep.pth").write_text(
        "# Bundled import root\n\nvendor\nvendor\nmissing\n"
        + "import sys; sys._wedge_pth_initialized = True\n"
    )
    _ = (site / "probe.py").write_text(
        "import fixturedep\nimport sys\n"
        + "def main():\n"
        + "    assert sys._wedge_pth_initialized is True\n"
        + "    assert sys.path.count(sys.argv[0] + '/site-packages/vendor') == 1\n"
        + "    assert not any(path.endswith('/missing') for path in sys.path)\n"
        + "    print(fixturedep.VALUE)\n"
    )
    pyz = tmp_path / "probe.pyz"
    write_archive(site, "probe:main", pyz)
    result = _run_pyz(pyz)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "42\n"


@pytest.mark.ac("gh-581:AC-1")
def test_archive_pth_exec_has_site_locals(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "namespace.pth").write_text(
        "import sys; sys._wedge_pth_site = sys._getframe(1).f_locals['sitedir']; "
        + "environment = {}; archive_path = 'broken'\n"
    )
    _ = (site / "probe.py").write_text(
        "import sys\ndef main():\n    print(sys._wedge_pth_site)\n"
    )
    pyz = tmp_path / "probe.pyz"
    write_archive(site, "probe:main", pyz)
    result = _run_pyz(pyz)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(pyz) + "/site-packages\n"
    assert result.stderr == ""
