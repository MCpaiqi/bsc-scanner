"""Running verification must not replace the caller's installed dependencies."""

import importlib.util
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


def test_verification_does_not_install_or_resolve_dependencies(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/test.py"
    spec = importlib.util.spec_from_file_location("verification_entrypoint", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts/test.py"))
    commands = []
    def run(command, **_kwargs):
        assert "install" not in command and "sync" not in command, "verification mutated dependencies"
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main() == 0 and commands


def test_release_gate_rejects_dependency_drift_before_packaging(tmp_path):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("release_pipeline", root / "scripts/release_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "scripts").mkdir()
    for name in ("test.py", "lock_dependencies.py"):
        shutil.copy2(root / "scripts" / name, tmp_path / "scripts" / name)
    shutil.copytree(root / "requirements", tmp_path / "requirements")
    (tmp_path / "web").mkdir()
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(root / "web" / name, tmp_path / "web" / name)
    declaration = (root / "pyproject.toml").read_text(encoding="utf-8").replace("fastapi>=0.115", "fastapi>=0.116")
    (tmp_path / "pyproject.toml").write_text(declaration, encoding="utf-8")
    result = module.run_gate(tmp_path, skip_frontend=True)
    assert result["status"] == "FAIL" and result["artifact"] is None
    assert "Dependency declarations changed" in result["results"][0]["stderr"]


@pytest.mark.skipif(os.name != "nt", reason="Windows Git object files have the readonly attribute")
def test_verification_cleans_its_readonly_git_fixture(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("readonly_verification", Path(__file__).parents[2] / "scripts/test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts/test.py"))
    created = []
    def run(command, **_kwargs):
        if "--basetemp" in command:
            created.append(Path(command[command.index("--basetemp") + 1]).parent)
            parent = Path(command[command.index("--basetemp") + 1]) / "project/.git/objects"
            parent.mkdir(parents=True)
            readonly = parent / "test-object"
            readonly.write_text("owned git object fixture", encoding="utf-8")
            readonly.chmod(stat.S_IREAD)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main() == 0
    assert created and all(not path.exists() for path in created)
