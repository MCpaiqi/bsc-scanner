"""Shared Windows/POSIX development and CI entry point."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, ENABLE_LIVE_TRADING="false", PYTHONHASHSEED="0", TZ="UTC",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTHONPATH", None)
    env.pop("PYTEST_ADDOPTS", None)
    # Unique per run; do not reuse or delete another run's pytest directories.
    temporary = Path(tempfile.mkdtemp(prefix=".pytest-run-temp-", dir=root)).resolve()
    if temporary.parent != root:
        raise RuntimeError("test temporary directory escaped workspace")
    try:
        commands = [
            [sys.executable, "-m", "pip", "install", "-e", ".[dev]"],
            [sys.executable, "-m", "pytest", "-p", "pytest_asyncio.plugin",
             "-p", "no:cacheprovider", "--basetemp", str(temporary / "pytest"), *sys.argv[1:]],
            [sys.executable, "-m", "ruff", "check", ".", "--no-cache"],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=root, env=env, check=False)
            if result.returncode:
                return result.returncode
        return 0
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
