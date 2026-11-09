"""Run the Phase 7.9.5 release gate in a reproducible order."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


def run_gate(root: Path, *, skip_frontend: bool = False) -> dict[str, Any]:
    commands: list[list[str]] = [
        [sys.executable, "scripts/test.py"],
    ]
    if not skip_frontend:
        npm = shutil.which("npm") or shutil.which("npm.cmd") or "npm"
        commands.append([npm, "--prefix", "web", "test", "--", "--reporter=dot"])
        commands.append([npm, "run", "build", "--prefix", "web"])
    results = []
    environment = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    temp_root = Path(tempfile.mkdtemp(prefix="smms-release-gate-"))
    try:
        for command in commands:
            if command[0] == sys.executable and command[1:3] == ["-m", "pytest"]:
                command = [*command, "--basetemp", str(temp_root / "pytest"), "-p", "no:cacheprovider"]
            try:
                result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
                returncode = result.returncode
                stdout = result.stdout or ""
                stderr = result.stderr or ""
            except OSError as exc:
                returncode = 127
                stdout = ""
                stderr = str(exc)
            results.append({"command": command, "returncode": returncode, "stdout": stdout[-3000:], "stderr": stderr[-3000:]})
            if returncode:
                break
        artifact = None
        if all(item["returncode"] == 0 for item in results):
            try:
                output = root.parent / "artifacts" / ("smms-release-" + uuid.uuid4().hex[:12])
                built = subprocess.run([sys.executable, "scripts/build_release.py", "--output", str(output)], cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
                built_code = built.returncode
                built_stdout = built.stdout or ""
                built_stderr = built.stderr or ""
            except OSError as exc:
                built_code = 127
                built_stdout = ""
                built_stderr = str(exc)
            results.append({"command": [sys.executable, "scripts/build_release.py"], "returncode": built_code, "stdout": built_stdout[-3000:], "stderr": built_stderr[-3000:]})
            if built_code == 0:
                artifact = built_stdout.strip().splitlines()[-1] if built_stdout.strip() else None
                if artifact:
                    audit = subprocess.run([sys.executable, "scripts/release_audit.py", str(Path(artifact).with_suffix(""))], cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
                    results.append({"command": [sys.executable, "scripts/release_audit.py", str(Path(artifact).with_suffix(""))], "returncode": audit.returncode, "stdout": (audit.stdout or "")[-3000:], "stderr": (audit.stderr or "")[-3000:]})
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    return {"status": "PASS" if results and all(item["returncode"] == 0 for item in results) else "FAIL", "results": results, "artifact": artifact}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--summary", type=Path, default=Path("data/reports/phase7_9_5_release_gate.json"))
    args = parser.parse_args()
    result = run_gate(args.root.resolve(), skip_frontend=args.skip_frontend)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "summary": str(args.summary), "artifact": result["artifact"]}, ensure_ascii=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
