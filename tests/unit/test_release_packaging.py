from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def _builder_module():
    script = Path(__file__).parents[2] / "scripts" / "build_release.py"
    spec = importlib.util.spec_from_file_location("build_release", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repeated_release_build_does_not_copy_output_into_itself(tmp_path: Path) -> None:
    module = _builder_module()
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "release_audit.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    (root / "README.md").write_text("release fixture\n", encoding="utf-8")
    output = root / "release"

    module.build_release(root, output)
    module.build_release(root, output)

    assert output.is_dir()
    assert not any(path.is_dir() and path.name == "release" for path in output.rglob("*"))
    assert not (output / "release" / "release").exists()


def test_default_release_output_is_outside_source_tree(tmp_path: Path) -> None:
    module = _builder_module()
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "release_audit.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )

    archive = module.build_release(root)

    assert archive.parent == root.parent / "artifacts"
    assert root not in archive.parents


def test_runtime_outputs_excluded_but_release_tools_retained(tmp_path: Path) -> None:
    module = _builder_module()
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/release_audit.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (root / "data/reports").mkdir(parents=True)
    (root / "data/reports/.gitkeep").touch()
    (root / "data/phase905-migration.sql").write_text("generated migration", encoding="utf-8")
    (root / "data/reports/live.json").write_text("{}", encoding="utf-8")
    (root / "server.log").write_text("runtime log", encoding="utf-8")
    (root / "backup.sql").write_text("dump", encoding="utf-8")
    (root / "web").mkdir()
    (root / "web/tsconfig.tsbuildinfo").touch()
    with zipfile.ZipFile(module.build_release(root)) as archive:
        names = set(archive.namelist())
    assert "scripts/release_audit.py" in names
    assert "data/reports/.gitkeep" in names
    assert not names.intersection({"data/phase905-migration.sql", "data/reports/live.json", "server.log", "backup.sql", "web/tsconfig.tsbuildinfo"})


def test_output_ancestor_rejected_without_deleting_source(tmp_path: Path) -> None:
    module = _builder_module()
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    marker = root / "scripts/release_audit.py"
    marker.write_text("raise SystemExit(0)", encoding="utf-8")
    with pytest.raises(ValueError, match="ancestor"):
        module.build_release(root, tmp_path)
    assert marker.exists()


def test_cli_uses_script_location_from_other_working_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    script = root / "scripts/build_release.py"
    shutil.copy2(Path(__file__).parents[2] / "scripts/build_release.py", script)
    (root / "scripts/release_audit.py").write_text("raise SystemExit(0)", encoding="utf-8")
    subprocess.run(
        [sys.executable, str(script), "--output", "artifact"], cwd=tmp_path, check=True,
        capture_output=True,
    )
    assert (root / "artifact.zip").is_file()


def test_audit_still_blocks_embedded_credentials(tmp_path: Path) -> None:
    module = _builder_module()
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(
        Path(__file__).parents[2] / "scripts/release_audit.py",
        root / "scripts/release_audit.py",
    )
    (root / "config.txt").write_text("password=not-a-real-credential", encoding="utf-8")
    output = tmp_path / "artifact"
    with pytest.raises(RuntimeError, match="release audit failed"):
        module.build_release(root, output)
    assert not output.with_suffix(".zip").exists()
