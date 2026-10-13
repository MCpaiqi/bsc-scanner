"""Fail closed release-tree audit. Never print matched content."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|"
    r"(?:private[_ -]?key|api[_ -]?(?:key|secret)|client[_ -]?secret|"
    r"access[_ -]?token|password|mnemonic|seed[_ -]?phrase)"
    r"[\"']?[ \t]*[:=][ \t]*[\"']?([^\s\"',;}]+)|"
    r"(?:postgres(?:ql)?|mysql|redis)://[^\s/:]+:([^\s/@]+)@|"
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{20,})\b",
    re.IGNORECASE,
)
PLACEHOLDERS = {"", "none", "null", "false", "true", "changeme", "placeholder",
                "example", "test", "dummy", "...", "postgres", "str", "secretstr"}

# Source code commonly passes secrets through settings objects. Those values are
# references, not embedded credentials, and should not fail a release audit.
CONFIG_REFERENCE_PREFIXES = (
    "os.", "self.", "settings.", "config.", "_runtime_settings.",
)


def audit(root: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    def add(path: Path, reason: str, severity: str = "HIGH") -> None:
        findings.append({"path": str(path.relative_to(root)) or ".",
                         "reason": reason, "severity": severity})

    def visit(path: Path) -> None:
        name = path.name.lower()
        if path.is_symlink():
            add(path, "symlink cannot be verified")
            return
        if (name in {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
                     ".venv", "venv"}
                or (name.startswith(".phase") and "tmp" in name)
                or name.startswith("pytest-")
                or "pytest" in name and ("tmp" in name or "temp" in name)):
            add(path, "development or temporary artifact")
            return
        if path.is_dir():
            try:
                for child in sorted(path.iterdir()):
                    visit(child)
            except OSError:
                add(path, "unreadable directory")
            return
        if name == ".env" or name.startswith(".env.") and name != ".env.example":
            add(path, "environment secrets file", "CRITICAL")
        if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
            add(path, "key material", "CRITICAL")
        if (path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".dump", ".sql", ".bak",
                                   ".zip", ".gz", ".tar", ".7z"}
                or name.endswith((".db-wal", ".db-shm"))):
            add(path, "database dump, backup or uninspected archive")
        try:
            content = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            add(path, "unreadable file")
            return
        if path.name in {"release_audit.py", "build_release.py"}:
            return
        for match in SECRET.finditer(content):
            value = next((v for v in match.groups() if v is not None), None)
            field_name = match.group(0).split("=", 1)[0].split(":", 1)[0].strip().lower()
            if value is not None and (value.lower() in PLACEHOLDERS or
                                      value.lower() == field_name or
                                      value.startswith(("${", *CONFIG_REFERENCE_PREFIXES,
                                                        "str(", "Field("))):
                continue
            add(path, "possible embedded credential", "CRITICAL")
            break

    if not root.is_dir():
        return [{"path": str(root), "reason": "release root is not a directory", "severity": "HIGH"}]
    visit(root)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    findings = audit(args.root.resolve())
    for finding in findings:
        print(json.dumps(finding, ensure_ascii=True))
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
