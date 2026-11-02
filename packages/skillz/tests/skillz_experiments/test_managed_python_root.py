"""The fake host login keeps uv on the shared managed-Python root."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_uv_python_dir_stays_outside_the_per_test_tmp_path(host_login: Path, tmp_path: Path) -> None:
    """A wedge build installs its pinned CPython builds below `uv python dir`. A per-test root costs about 400 MB."""
    uv = shutil.which("uv")
    assert uv is not None
    home = host_login.parents[1]
    found = Path(subprocess.run([uv, "python", "dir"], check=True, capture_output=True, text=True).stdout.strip())
    assert not found.is_relative_to(tmp_path), f"uv installs CPython below the per-test HOME {home}"
