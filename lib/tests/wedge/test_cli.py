"""AC-W9: the CLI's directory resolution rejects a contradictory or bad command."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import fromargs
import shutil
import subprocess
import sys

import pytest

from wedge._cli import check_cmd, lock_cmd

SKILLS = Path("skills")


@pytest.mark.ac("AC-W9")
def test_positional_dirs_and_root_are_exclusive(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)

    with pytest.raises(fromargs.CliError, match="exclusive"):
        _ = check_cmd(skill_dir=[str(SKILLS / "hello")], root=[str(SKILLS)])


@pytest.mark.ac("AC-W9")
def test_a_missing_root_is_an_error_even_when_another_root_finds_skills(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)

    with pytest.raises(fromargs.CliError, match="skills-typo is not a directory"):
        _ = check_cmd(root=[str(SKILLS), "skills-typo"])


@pytest.mark.ac("AC-W9")
def test_duplicate_skill_directories_collapse_to_one(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    hello = SKILLS / "hello"

    checked = check_cmd(skill_dir=[str(hello), str(consumer / hello)])

    assert checked["checked"] == [str(hello)]


@pytest.mark.ac("AC-W9")
@pytest.mark.parametrize("jobs", [0, -1])
def test_jobs_below_one_is_an_error(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)

    with pytest.raises(fromargs.CliError, match="--jobs must be at least 1"):
        _ = lock_cmd(root=[str(SKILLS)], jobs=jobs)


@pytest.mark.ac("AC-W9")
def test_bundle_writes_skill_named_executable_archive(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    from wedge._cli import bundle_cmd
    result = bundle_cmd(skill_dir=[str(SKILLS / "hello")])
    path = Path(result["hello"]["path"])
    assert path.name == "hello.pyz"
    assert path.parent.name == "scripts"
    assert path.stat().st_mode & 0o111


@pytest.mark.ac("AC-W9")
def test_bundle_check_rejects_corrupt_bundle_without_mutation(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    scripts = consumer / SKILLS / "hello" / "scripts"
    for child in scripts.iterdir():
        _ = child.unlink()
    from wedge._cli import bundle_cmd
    result = bundle_cmd(skill_dir=[str(SKILLS / "hello")])
    path = Path(result["hello"]["path"])
    original = path.read_bytes()
    _ = path.write_bytes(b"not a zip")
    with pytest.raises(fromargs.CliError, match="invalid bundle"):
        _ = bundle_cmd(skill_dir=[str(SKILLS / "hello")], check=True)
    assert path.read_bytes() == b"not a zip"
    _ = path.write_bytes(original)
    path.chmod(path.stat().st_mode & ~0o111)
    with pytest.raises(fromargs.CliError, match="not executable"):
        _ = bundle_cmd(skill_dir=[str(SKILLS / "hello")], check=True)
    _ = path.write_bytes(original.replace(
        b"#!/usr/bin/env python3\n", b"#!/usr/bin/env python\n", 1
    ))
    path.chmod(path.stat().st_mode | 0o111)
    with pytest.raises(fromargs.CliError, match="non-canonical shebang"):
        _ = bundle_cmd(skill_dir=[str(SKILLS / "hello")], check=True)


@pytest.mark.ac("AC-W9")
def test_bundle_check_rejects_missing_bundle_without_mutation(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    scripts = consumer / SKILLS / "hello" / "scripts"
    for child in scripts.iterdir():
        _ = child.unlink()
    from wedge._cli import bundle_cmd
    _ = bundle_cmd(skill_dir=[str(SKILLS / "hello")])
    path = scripts / "hello.pyz"
    _ = path.unlink()
    with pytest.raises(fromargs.CliError, match="missing bundle"):
        _ = bundle_cmd(skill_dir=[str(SKILLS / "hello")], check=True)
    assert not path.exists()


@pytest.mark.ac("AC-W9")
def test_bundle_emits_clean_archive_that_runs_outside_checkout(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    scripts = consumer / SKILLS / "hello" / "scripts"
    for child in scripts.iterdir():
        _ = child.unlink()

    from wedge._cli import bundle_cmd

    result = bundle_cmd(skill_dir=[str(SKILLS / "hello")])
    assert sorted(child.name for child in scripts.iterdir()) == ["hello.pyz"]
    assert (scripts / "hello.pyz").stat().st_mode & 0o777 == 0o755
    archive = tmp_path / "outside" / "hello.pyz"
    archive.parent.mkdir()
    _ = archive.write_bytes((scripts / "hello.pyz").read_bytes())
    archive.chmod((scripts / "hello.pyz").stat().st_mode)
    env = {"HOME": str(tmp_path / "home"), "SHIV_ROOT": str(tmp_path / "shiv-root")}
    completed = subprocess.run(
        [sys.executable, "-I", str(archive), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert Path(result["hello"]["path"]) == scripts / "hello.pyz"


@pytest.mark.ac("AC-W9")
def test_bundle_check_returns_committed_path_without_mutation(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    from wedge._cli import bundle_cmd

    result = bundle_cmd(skill_dir=[str(SKILLS / "hello")])
    path = Path(result["hello"]["path"])
    before = (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
    checked = bundle_cmd(skill_dir=[str(SKILLS / "hello")], check=True)
    assert Path(checked["hello"]["path"]) == path
    assert (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns) == before


@pytest.mark.ac("AC-W9")
def test_bundle_check_rejects_stale_readable_bundle_without_mutation(
    tmp_path: Path,
    copy_consumer: Callable[[Path], Path],
    rewrite_pyz: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    from zipfile import ZipFile

    from wedge._cli import bundle_cmd

    result = bundle_cmd(skill_dir=[str(SKILLS / "hello")])
    path = Path(result["hello"]["path"])
    with ZipFile(path) as archive:
        member = archive.namelist()[0]
    mutated = rewrite_pyz(path, tmp_path / "mutated.pyz", replace={member: b"mutated"})
    mutated_bytes = mutated.read_bytes()
    _ = path.write_bytes(mutated_bytes)
    with pytest.raises(fromargs.CliError, match="stale bundle"):
        _ = bundle_cmd(skill_dir=[str(SKILLS / "hello")], check=True)
    assert path.read_bytes() == mutated_bytes


@pytest.mark.ac("AC-W9")
def test_bundle_rejects_symlinked_scripts_directory_without_touching_victim(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    scripts = consumer / SKILLS / "hello" / "scripts"
    _ = scripts.rename(tmp_path / "original-scripts")
    victim_dir = tmp_path / "victim-scripts"
    victim_dir.mkdir()
    victim = victim_dir / "hello.pyz"
    _ = victim.write_bytes(b"keep me")
    scripts.symlink_to(victim_dir, target_is_directory=True)

    from wedge._cli import bundle_cmd

    with pytest.raises(fromargs.CliError, match="scripts directory must not be symlink"):
        _ = bundle_cmd(skill_dir=[str(SKILLS / "hello")])
    assert victim.read_bytes() == b"keep me"


@pytest.mark.ac("AC-W9")
def test_bundle_rejects_symlinked_target_without_touching_victim(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    scripts = consumer / SKILLS / "hello" / "scripts"
    for child in scripts.iterdir():
        _ = child.unlink()
    victim = tmp_path / "victim.pyz"
    _ = victim.write_bytes(b"keep me")
    (scripts / "hello.pyz").symlink_to(victim)

    from wedge._cli import bundle_cmd

    with pytest.raises(fromargs.CliError, match="bundle target must not be symlink"):
        _ = bundle_cmd(skill_dir=[str(SKILLS / "hello")])
    assert victim.read_bytes() == b"keep me"


@pytest.mark.ac("AC-W9")
def test_bundle_isolates_scripts_file_error_and_continues_other_skills(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    hello = consumer / SKILLS / "hello"
    world = consumer / SKILLS / "world"
    _ = shutil.copytree(hello, world)
    _ = (world / "wedge.toml").write_text(
        (world / "wedge.toml").read_text().replace('name = "hello"', 'name = "world"')
    )
    scripts = hello / "scripts"
    _ = scripts.rename(tmp_path / "original-scripts")
    _ = scripts.write_text("not a directory")

    from wedge._cli import bundle_cmd

    with pytest.raises(fromargs.CliError, match="File exists"):
        _ = bundle_cmd(skill_dir=[str(hello), str(world)])
    world_scripts = world / "scripts"
    assert (world_scripts / "world.pyz").is_file()
    assert not list(world_scripts.glob(".*.pyz.*"))
