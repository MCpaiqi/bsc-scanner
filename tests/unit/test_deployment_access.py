"""Local bootstrap and rendered Compose boundaries, without starting services."""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from smms.config.settings import Settings

ROOT = Path(__file__).parents[2]


def generate(target):
    return subprocess.run([sys.executable, str(ROOT / "scripts/init_local_env.py"), "--output", str(target)],
                          capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)


def values(path):
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#"))


def test_bootstrap_creates_unique_private_configuration_without_stdout_secrets(tmp_path):
    first, second = tmp_path / ".env", tmp_path / ".env.second"
    for path in (first, second):
        result = generate(path)
        assert result.returncode == 0, "local bootstrap failed"
        data = values(path)
        for name in ("SMMS_POSTGRES_PASSWORD", "SMMS_ANALYST_KEY"):
            assert re.fullmatch(r"[A-Za-z0-9_-]{43}", data[name])
            assert data[name] not in result.stdout + result.stderr
        assert data["SMMS_POSTGRES_PASSWORD"] != data["SMMS_ANALYST_KEY"]
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "postgres:postgres@" not in path.read_text(encoding="utf-8")
    assert values(first)["SMMS_ANALYST_KEY"] != values(second)["SMMS_ANALYST_KEY"]


def test_bootstrap_refuses_to_replace_an_existing_user_file(tmp_path):
    path = tmp_path / ".env"
    original = b"preserve-existing-local-configuration\n"
    path.write_bytes(original)
    result = generate(path)
    assert result.returncode == 1 and path.read_bytes() == original
    assert "preserve-existing" not in result.stdout + result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink permission-independent regression")
def test_bootstrap_refuses_existing_and_dangling_symlinks(tmp_path):
    for name in ("exists", "missing"):
        target = tmp_path / name
        if name == "exists":
            target.write_text("preserve fixture", encoding="utf-8")
        link = tmp_path / (".env." + name)
        link.symlink_to(target)
        assert generate(link).returncode == 1
        assert link.is_symlink()
        assert target.read_text(encoding="utf-8") == "preserve fixture" if target.exists() else True


@pytest.fixture
def compose(tmp_path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI required; no daemon or service mutation")
    probe = subprocess.run([docker, "compose", "version", "--short"], capture_output=True, timeout=20, check=False)
    if probe.returncode:
        pytest.skip("Docker Compose plugin required; no daemon or service mutation")
    shutil.copy2(ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(("COMPOSE_", "SMMS_POSTGRES_PASSWORD", "SMMS_ANALYST_KEY"))}
    def render():
        return subprocess.run([docker, "compose", "--project-directory", str(tmp_path), "--env-file", str(tmp_path / ".env"),
                               "--project-name", "smms-boundary-test", "config", "--format", "json"],
                              cwd=tmp_path, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    return tmp_path, render


def test_compose_requires_an_explicit_database_credential(compose):
    path, render = compose
    (path / ".env").write_text("", encoding="utf-8")
    result = render()
    assert result.returncode != 0
    assert "SMMS_POSTGRES_PASSWORD" in result.stderr


def test_rendered_compose_keeps_databases_private_and_binds_ui_to_loopback(compose):
    path, render = compose
    assert generate(path / ".env").returncode == 0
    result = render()
    assert result.returncode == 0, "rendered Compose configuration failed"
    services = json.loads(result.stdout)["services"]
    assert not services["postgres"].get("ports") and not services["redis"].get("ports")
    for name in ("api", "web"):
        assert services[name]["ports"]
        assert all(port.get("host_ip") == "127.0.0.1" for port in services[name]["ports"])
    data = values(path / ".env")
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == data["SMMS_POSTGRES_PASSWORD"]
    for name, service in services.items():
        assert service.get("network_mode") != "host"
        if service.get("build", {}).get("target") == "runtime":
            settings = Settings(_env_file=None, **service["environment"])
            assert (settings.analyst_key is not None) == (name == "api")
            url = make_url(settings.database_url)
            assert (url.host, url.database, url.username, url.password) == (
                "postgres", "smms", "postgres", data["SMMS_POSTGRES_PASSWORD"])
