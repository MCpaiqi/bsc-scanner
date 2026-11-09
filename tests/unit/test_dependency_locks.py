"""Clean-install locks must track declarations without depending on local state."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def check(root):
    return subprocess.run([sys.executable, str(ROOT / "scripts/lock_dependencies.py"), "check", "--root", str(root)],
                          capture_output=True, text=True, check=False)


@pytest.fixture
def checkout(tmp_path):
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(ROOT / "requirements", tmp_path / "requirements")
    (tmp_path / "web").mkdir()
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(ROOT / "web" / name, tmp_path / "web" / name)
    return tmp_path


def test_checked_in_dependencies_match_their_locks():
    result = check(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_declared_dependency_drift_requires_lock_refresh(checkout):
    path = checkout / "pyproject.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('"redis>=5.2,<6.0",',
                    '"redis>=5.2,<6.0", "unresolved-example>=1",'), encoding="utf-8")
    result = check(checkout)
    assert result.returncode != 0 and "declarations changed" in result.stderr


def test_unrelated_tool_configuration_does_not_invalidate_dependencies(checkout):
    path = checkout / "pyproject.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("line-length = 100", "line-length = 101"), encoding="utf-8")
    assert check(checkout).returncode == 0


def test_lock_content_drift_fails_without_printing_its_contents(checkout):
    path = checkout / "requirements/runtime.lock"
    path.write_text(path.read_text(encoding="utf-8") + "\n# private-lock-fixture-value\n", encoding="utf-8")
    result = check(checkout)
    assert result.returncode != 0 and "Lockfile changed" in result.stderr
    assert "private-lock-fixture-value" not in result.stdout + result.stderr


def test_crlf_checkout_preserves_lock_identity(checkout):
    for name in ("runtime.lock", "dev.lock"):
        path = checkout / "requirements" / name
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert check(checkout).returncode == 0


@pytest.mark.parametrize("drift", ["latest", "different_lock"])
def test_frontend_direct_versions_are_exact_and_match_lock(checkout, drift):
    path = checkout / "web/package.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["dependencies"]["react"] = "latest" if drift == "latest" else "0.0.1"
    path.write_text(json.dumps(value), encoding="utf-8")
    result = check(checkout)
    assert result.returncode != 0 and "frontend" in result.stderr.lower()
