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
    (root / "src/example.egg-info").mkdir(parents=True)
    (root / "src/example.egg-info/PKG-INFO").write_text("generated install metadata", encoding="utf-8")
    with zipfile.ZipFile(module.build_release(root)) as archive:
        names = set(archive.namelist())
    assert "scripts/release_audit.py" in names
    assert "data/reports/.gitkeep" in names
    assert not names.intersection({"data/phase905-migration.sql", "data/reports/live.json", "server.log", "backup.sql", "web/tsconfig.tsbuildinfo"})
    assert "src/example.egg-info/PKG-INFO" not in names


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


def source_fixture(tmp_path):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/release_audit.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (root / "README.md").write_text("source version1\n", encoding="utf-8")
    return root


def test_git_release_contains_tracked_inputs_and_excludes_user_work(tmp_path):
    root = source_fixture(tmp_path)
    (root / "configs").mkdir()
    (root / "configs/default.yaml").write_text("mode: PAPER\n", encoding="utf-8")
    (root / "requirements").mkdir()
    (root / "requirements/runtime.lock").write_text("example==1.0\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "add", "--", "README.md", "scripts", "configs", "requirements"], cwd=root, check=True,
                   capture_output=True)
    (root / "research-notes.json").write_text('{"untracked":"private-research-fixture"}', encoding="utf-8")
    with zipfile.ZipFile(_builder_module().build_release(root, tmp_path / "artifact")) as archive:
        assert "research-notes.json" not in archive.namelist()
        assert {"requirements/runtime.lock", "configs/default.yaml"} <= set(archive.namelist())


def test_existing_unowned_output_is_preserved(tmp_path):
    root = source_fixture(tmp_path)
    output = tmp_path / "artifact"
    output.mkdir()
    marker = output / "user-notes.txt"
    marker.write_text("keep this user file", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _builder_module().build_release(root, output)
    assert marker.read_text(encoding="utf-8") == "keep this user file"


def test_source_manifest_verifies_every_packaged_file(tmp_path):
    import hashlib
    import json

    root = source_fixture(tmp_path)
    with zipfile.ZipFile(_builder_module().build_release(root, tmp_path / "artifact")) as archive:
        manifest = json.loads(archive.read(".release-manifest.json"))
        assert manifest["schema_version"] == 1
        assert set(manifest["files"]) == set(archive.namelist()) - {".release-manifest.json"}
        for name, expected in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected


def test_changed_source_does_not_overwrite_an_existing_release(tmp_path):
    root = source_fixture(tmp_path)
    output = tmp_path / "artifact"
    module = _builder_module()
    original = module.build_release(root, output)
    before = original.read_bytes()
    (root / "README.md").write_text("new source version2\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        module.build_release(root, output)
    assert original.read_bytes() == before


def test_same_source_produces_same_zip_when_file_mtime_changes(tmp_path):
    import os

    root = source_fixture(tmp_path)
    module = _builder_module()
    first = module.build_release(root, tmp_path / "first").read_bytes()
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (1_000_000_000, 1_000_000_000))
    second = module.build_release(root, tmp_path / "second").read_bytes()
    assert first == second


def test_exported_release_works_without_git(tmp_path, monkeypatch):
    root = source_fixture(tmp_path)
    module = _builder_module()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    original = module.subprocess.run
    def without_git(command, **kwargs):
        if command[0] == "git":
            raise FileNotFoundError("git")
        return original(command, **kwargs)
    monkeypatch.setattr(module.subprocess, "run", without_git)
    assert module.build_release(root, tmp_path / "artifact").is_file()


def test_checkout_without_git_refuses_to_include_untracked_work(tmp_path, monkeypatch):
    root = source_fixture(tmp_path)
    (root / ".git").mkdir()
    (root / "private-notes.json").write_text("private fixture", encoding="utf-8")
    module = _builder_module()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="Git is required"):
        module.build_release(root, tmp_path / "artifact")


def test_input_changed_after_clean_checkout_has_no_commit_claim(tmp_path, monkeypatch):
    import json

    root = source_fixture(tmp_path)
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "--quiet", "-m", "fixture"], cwd=root, check=True)
    module = _builder_module()
    original = module.source_files
    def source_files(*args):
        result = original(*args)
        assert result[1]
        (root / "README.md").write_text("changed during source snapshot\n", encoding="utf-8")
        return result
    monkeypatch.setattr(module, "source_files", source_files)
    with zipfile.ZipFile(module.build_release(root, tmp_path / "artifact")) as archive:
        assert json.loads(archive.read(".release-manifest.json"))["source_commit"] is None


def test_failed_git_discovery_cannot_package_untracked_notes(tmp_path, monkeypatch):
    root = source_fixture(tmp_path)
    (root / ".git").mkdir()
    (root / "private-study.txt").write_text("private research fixture", encoding="utf-8")
    module = _builder_module()
    original = module.subprocess.run
    def failed_discovery(command, **kwargs):
        if command[1:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(command, 128, stdout="", stderr="repository discovery rejected")
        return original(command, **kwargs)
    monkeypatch.setattr(module.subprocess, "run", failed_discovery)
    with pytest.raises(ValueError, match="Git discovery failed"):
        module.build_release(root, tmp_path / "artifact")
    assert not (tmp_path / "artifact.zip").exists()


def test_source_inside_another_checkout_cannot_package_untracked_notes(tmp_path):
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    root = source_fixture(tmp_path / "nested")
    (root / "private-study.txt").write_text("private research fixture", encoding="utf-8")
    with pytest.raises(ValueError, match="different Git checkout"):
        _builder_module().build_release(root, tmp_path / "artifact")
    assert not (tmp_path / "artifact.zip").exists()
