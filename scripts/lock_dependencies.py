"""Check or explicitly refresh hash-pinned dependencies without modifying an environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

RESOLVER_VERSION = "0.12.5"
FILES = ("runtime.lock", "dev.lock")


def text_hash(path: Path) -> str:
    # Git/Windows may change line endings; dependency bytes otherwise stay exact.
    return hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()


def inputs(root: Path) -> dict:
    value = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = value["project"]
    return {"requires_python": project["requires-python"], "dependencies": sorted(project["dependencies"]),
            "extras": {name: sorted(items) for name, items in project.get("optional-dependencies", {}).items()},
            "build": value["build-system"]}


def input_hash(root: Path) -> str:
    return hashlib.sha256(json.dumps(inputs(root), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_frontend(root: Path) -> None:
    package = json.loads((root / "web/package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "web/package-lock.json").read_text(encoding="utf-8"))
    for group in ("dependencies", "devDependencies"):
        declared = package.get(group, {})
        if declared != lock["packages"][""].get(group, {}):
            raise ValueError("frontend declarations differ from package-lock.json")
        for name, version in declared.items():
            resolved = lock["packages"].get("node_modules/" + name, {})
            if (not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?", version)
                    or resolved.get("version") != version or not resolved.get("integrity")):
                raise ValueError("frontend direct dependencies must use exact locked versions with integrity")


def check(root: Path) -> None:
    manifest = json.loads((root / "requirements/lock-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("dependency_inputs_sha256") != input_hash(root):
        raise ValueError("Dependency declarations changed; refresh the dependency locks")
    for name in FILES:
        if manifest.get("files", {}).get(name) != text_hash(root / "requirements" / name):
            raise ValueError(f"Lockfile changed: requirements/{name}; refresh the lock manifest")
    check_frontend(root)


def update(root: Path, *, upgrade: bool, constraints: Path | None) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError(f"Lock generation requires uv=={RESOLVER_VERSION}; check/install does not")
    version = subprocess.run([uv, "--version"], capture_output=True, text=True, check=True).stdout.split()
    if len(version) < 2 or version[1] != RESOLVER_VERSION:
        raise ValueError(f"Lock generation requires uv=={RESOLVER_VERSION}")
    folder = root / "requirements"
    folder.mkdir(exist_ok=True)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("UV_", "PIP_"))}
    with tempfile.TemporaryDirectory(prefix="smms-lock-input-") as temporary:
        build = Path(temporary) / "build.in"
        build.write_text("\n".join(inputs(root)["build"]["requires"]) + "\n", encoding="utf-8")
        base = [uv, "--no-config", "--no-python-downloads", "pip", "compile", str(root / "pyproject.toml"), str(build),
                "--universal", "--generate-hashes", "--no-annotate", "--no-build",
                "--default-index", "https://pypi.org/simple", "--python-version", "3.12", "--python", sys.executable,
                "--custom-compile-command", "python scripts/lock_dependencies.py update"]
        if upgrade:
            base.append("--upgrade")
        if constraints is not None:
            base.extend(["--constraints", str(constraints.resolve())])
        subprocess.run([*base, "--all-extras", "--output-file", str(folder / "dev.lock")],
                       cwd=temporary, env=environment, stdout=subprocess.DEVNULL, check=True)
        subprocess.run([*base, "--constraints", str(folder / "dev.lock"), "--output-file", str(folder / "runtime.lock")],
                       cwd=temporary, env=environment, stdout=subprocess.DEVNULL, check=True)
    manifest = {"schema_version": 1, "resolver": f"uv=={RESOLVER_VERSION}",
                "dependency_inputs_sha256": input_hash(root),
                "files": {name: text_hash(folder / name) for name in FILES}}
    (folder / "lock-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    check(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "update"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--constraints", type=Path)
    arguments = parser.parse_args()
    try:
        root = arguments.root.resolve()
        if arguments.action == "update":
            update(root, upgrade=arguments.upgrade, constraints=arguments.constraints)
        else:
            check(root)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        # Do not dump requirement contents or resolver command arguments here.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(f"Dependency lock check failed: {message}", file=sys.stderr)
        return 1
    print("Dependency locks match declared inputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
