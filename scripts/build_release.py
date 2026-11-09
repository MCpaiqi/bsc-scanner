"""Build a clean, audited source release artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
MANIFEST = ".release-manifest.json"


def should_include(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(
        part in EXCLUDED_DIRS or part.endswith(".egg-info") or part.startswith(EXCLUDED_PREFIXES)
        for part in relative.parts[:-1]
    ):
        return False
    # Runtime data is local to the deployment; retain only directory scaffolding.
    if relative.parts[0] == "data" and path.name != ".gitkeep":
        return False
    if path.name == MANIFEST or path.name.startswith((".phase", ".pytest-", ".release-test")):
        return False
    if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
        return False
    return path.suffix.lower() not in EXCLUDED_SUFFIXES


def source_files(root: Path, output: Path) -> tuple[list[Path], str | None]:
    """A checkout releases tracked inputs; an exported source tree is walked."""
    git = shutil.which("git")
    checkout_context = any((parent / ".git").exists() or (parent / ".git").is_symlink()
                           for parent in (root, *root.parents))
    checkout_context = checkout_context or any(name in os.environ for name in ("GIT_DIR", "GIT_WORK_TREE"))
    if git is None and checkout_context:
        raise ValueError("Git is required to package tracked checkout inputs")
    top = (subprocess.run([git, "rev-parse", "--show-toplevel"], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", check=False)
           if git else None)
    commit = None
    if top is not None and top.returncode == 0:
        if Path(top.stdout.strip()).resolve() != root:
            raise ValueError("source root is inside a different Git checkout; export outside the checkout")
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
        paths = [root / name for name in listed.stdout.decode("utf-8").split("\0") if name]
        clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=root, capture_output=True, check=False)
        if clean.returncode == 0:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                    text=True, check=True).stdout.strip()
    else:
        if checkout_context:
            raise ValueError("Git discovery failed in a checkout; refusing untracked source inputs")
        paths = []
        for current, directories, filenames in os.walk(root):
            current_path = Path(current)
            if current_path == output or output in current_path.parents:
                directories[:] = []
                continue
            directories[:] = [name for name in directories if name not in EXCLUDED_DIRS and not name.endswith(".egg-info")
                              and not name.startswith(EXCLUDED_PREFIXES)
                              and not (current_path / name / MANIFEST).is_file()]
            for name in directories:
                if (current_path / name).is_symlink():
                    raise ValueError("release source contains a symlink")
            paths.extend(current_path / name for name in filenames)
    selected = []
    for path in sorted(paths):
        if output == path or output in path.parents or not should_include(path, root):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("release source contains a symlink or non-file input")
        selected.append(path)
    return selected, commit


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def existing_matches(output: Path, archive: Path, manifest: dict) -> bool:
    """Existing artifacts are immutable; only an identical verified repeat succeeds."""
    try:
        marker = output / MANIFEST
        if not output.is_dir() or not archive.is_file() or marker.stat().st_size > 2_000_000:
            return False
        if json.loads(marker.read_text(encoding="utf-8")) != manifest:
            return False
        expected = {*manifest["files"], MANIFEST}
        files = [path for path in output.rglob("*") if path.is_file()]
        if any(path.is_symlink() for path in output.rglob("*")) or {str(path.relative_to(output).as_posix()) for path in files} != expected:
            return False
        if any(file_hash(output / name) != digest for name, digest in manifest["files"].items()):
            return False
        with zipfile.ZipFile(archive) as zipped:
            if set(zipped.namelist()) != expected or len(zipped.infolist()) != len(expected):
                return False
            for info in zipped.infolist():
                if info.file_size != (output / info.filename).stat().st_size:
                    return False
                with zipped.open(info) as handle:
                    if hashlib.file_digest(handle, "sha256").hexdigest() != file_hash(output / info.filename):
                        return False
        return True
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return False


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
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # Some locked-down workspaces cannot write beside the checkout. Keep
        # the explicit external default when possible, but remain usable there.
        if output != (root.parent / "artifacts" / "smms-release").resolve():
            raise
        output = root / "release"
        output.parent.mkdir(parents=True, exist_ok=True)
    paths, commit = source_files(root, output)
    manifest = {"schema_version": 1, "source_commit": commit,
                "files": {path.relative_to(root).as_posix(): file_hash(path) for path in paths}}
    archive = output.with_suffix(".zip")
    if output.exists() or archive.exists():
        if existing_matches(output, archive, manifest):
            return archive
        raise FileExistsError("Refusing to overwrite existing release output; choose a fresh output path")
    with tempfile.TemporaryDirectory(prefix="smms-release-build-", dir=output.parent) as temporary:
        stage = Path(temporary) / "source"
        stage.mkdir()
        for source in paths:
            name = source.relative_to(root)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if file_hash(target) != manifest["files"][name.as_posix()]:
                raise ValueError("release input changed during copy")
        if commit:
            clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=root,
                                   capture_output=True, check=False)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                  text=True, check=True).stdout.strip()
            if clean.returncode != 0 or head != commit:
                manifest["source_commit"] = None
        (stage / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result = subprocess.run([sys.executable, str(audit), str(stage)], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", check=False)
        if result.returncode:
            details = (result.stdout or result.stderr).strip()
            raise RuntimeError(f"release audit failed; artifact was not packaged:\n{details}")
        zipped = Path(temporary) / "release.zip"
        with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as handle:
            for item in sorted(stage.rglob("*")):
                if item.is_file():
                    info = zipfile.ZipInfo(item.relative_to(stage).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o100644 << 16
                    handle.writestr(info, item.read_bytes())
        stage.rename(output)
        # Exclusive creation cannot replace another process's/user's archive.
        with archive.open("xb") as target, zipped.open("rb") as source:
            shutil.copyfileobj(source, target)
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
