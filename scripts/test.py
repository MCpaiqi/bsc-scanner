"""Shared Windows/POSIX development and CI entry point."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def remove_readonly(function, path, error):
    """Retry only Windows readonly files inside this run's owned temp tree."""
    item = Path(path)
    if (os.name != "nt" or not isinstance(error, PermissionError) or item.is_symlink()
            or not item.is_file() or item.stat().st_mode & stat.S_IWRITE):
        raise error
    item.chmod(item.stat().st_mode | stat.S_IWRITE)
    function(path)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, ENABLE_LIVE_TRADING="false", PYTHONHASHSEED="0", TZ="UTC",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTHONPATH", None)
    env.pop("PYTEST_ADDOPTS", None)
    # Unique per run; do not reuse or delete another run's pytest directories.
    # Keep exported-source fixtures outside the parent checkout's Git context.
    temp_parent = Path(tempfile.gettempdir()).resolve()
    temporary = Path(tempfile.mkdtemp(prefix="smms-pytest-run-", dir=temp_parent)).resolve()
    if temporary.parent != temp_parent:
        raise RuntimeError("test temporary directory escaped its owned parent")
    try:
        commands = [
            [sys.executable, "scripts/lock_dependencies.py", "check"],
            [sys.executable, "-m", "pytest", "-p", "pytest_asyncio.plugin",
             "-p", "no:cacheprovider", "--basetemp", str(temporary / "pytest"), *sys.argv[1:]],
            [sys.executable, "-m", "ruff", "check", "src", "scripts", "tests", "--no-cache"],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=root, env=env, check=False)
            if result.returncode:
                return result.returncode
        return 0
    finally:
        shutil.rmtree(temporary, onexc=remove_readonly)


if __name__ == "__main__":
    raise SystemExit(main())
