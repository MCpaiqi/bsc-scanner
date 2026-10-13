"""Build a clean, audited source release artifact."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "dist", "build", "coverage", "release",
    "tmp", "temp", "cache", "caches", "temporary",
    "tests",
}
EXCLUDED_PREFIXES = (".phase", ".pytest-", ".release-test", "release", "_release")
EXCLUDED_SUFFIXES = (
    ".db", ".sqlite", ".sqlite3", ".pyc", ".zip", ".dump", ".sql", ".bak",
    ".gz", ".tar", ".7z", ".log", ".tsbuildinfo",
    ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm",
    ".sqlite3-wal", ".sqlite3-shm",
)


def should_include(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(
        part in EXCLUDED_DIRS or part.startswith(EXCLUDED_PREFIXES)
        for part in relative.parts[:-1]
    ):
        return False
    # Runtime data is local to the deployment; retain only directory scaffolding.
    if relative.parts[0] == "data" and path.name != ".gitkeep":
        return False
    if path.name.startswith((".phase", ".pytest-", ".release-test")):
        return False
    if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
        return False
    return path.suffix.lower() not in EXCLUDED_SUFFIXES


def build_release(root: Path, output: Path | None = None) -> Path:
    """Build a release without ever walking the output tree.

    The default artifact directory is deliberately outside the source tree.
    An explicit in-tree output remains supported for callers that need it, but
    is pruned from the walk and can never be the source root itself.
    """
    root = root.resolve()
    audit = root / "scripts" / "release_audit.py"
    if not audit.is_file():
        raise ValueError(f"source root must contain scripts/release_audit.py: {root}")
    if output is None:
        output = root.parent / "artifacts" / "smms-release"
    output = output if output.is_absolute() else root / output
    output = output.resolve()
    if output == root:
        raise ValueError("release output must not be the source root")
    if output in root.parents:
        raise ValueError("release output must not be an ancestor of the source root")
    if output.exists():
        shutil.rmtree(output)
    try:
        output.mkdir(parents=True)
    except PermissionError:
        # Some locked-down workspaces cannot write beside the checkout. Keep
        # the explicit external default when possible, but remain usable there.
        if output != (root.parent / "artifacts" / "smms-release").resolve():
            raise
        output = root / "release"
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True)
    for current, directories, filenames in os.walk(root):
        current_path = Path(current)
        if current_path == output or output in current_path.parents:
            directories[:] = []
            continue
        directories[:] = [
            name
            for name in directories
            if name not in EXCLUDED_DIRS
            and not name.startswith(EXCLUDED_PREFIXES)
        ]
        for name in directories:
            if (current_path / name).is_symlink():
                raise ValueError(f"release source contains a symlink: {current_path / name}")
        for filename in filenames:
            source = current_path / filename
            if should_include(source, root):
                if source.is_symlink():
                    raise ValueError(f"release source contains a symlink: {source}")
                target = output / source.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    result = subprocess.run(
        [sys.executable, str(audit), str(output)], capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        details = (result.stdout or result.stderr).strip()
        suffix = f":\n{details}" if details else ""
        raise RuntimeError(f"release audit failed; artifact was not packaged{suffix}")
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        for item in output.rglob("*"):
            if item.is_file():
                handle.write(item, item.relative_to(output))
    return archive


def build(root: Path, output: Path | None = None) -> Path:
    """Backward-compatible wrapper for the release builder."""
    return build_release(root, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="artifact directory (default: ../artifacts/smms-release)",
    )
    args = parser.parse_args()
    print(build_release(Path(__file__).resolve().parents[1], args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
