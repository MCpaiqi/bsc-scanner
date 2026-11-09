"""Create a new private local configuration; never replace existing credentials."""

from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path


def create_env(target: Path, template: Path) -> None:
    if target.exists() or target.is_symlink():
        raise FileExistsError("Refusing to replace existing local configuration")
    contents = template.read_text(encoding="utf-8")
    for name in ("SMMS_POSTGRES_PASSWORD", "SMMS_ANALYST_KEY"):
        contents, count = re.subn(r"(?m)^" + name + r"=.*$", name + "=" + secrets.token_urlsafe(32), contents)
        if count != 1:
            raise ValueError("Local configuration template is missing a unique credential field")
    # Exclusive creation also rejects a symlink or file created after the check.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(contents)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / ".env")
    arguments = parser.parse_args()
    try:
        create_env(arguments.output, root / ".env.example")
    except FileExistsError:
        print("Refusing to replace existing local configuration; use the documented credential rotation procedure.")
        return 1
    except (OSError, ValueError):
        print("Unable to create local configuration; check the template and destination permissions.")
        return 1
    print(f"Created private local configuration: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
