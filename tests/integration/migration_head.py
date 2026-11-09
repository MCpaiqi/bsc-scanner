"""The repository migration head, distinct from a historical test target."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

CURRENT_HEAD = ScriptDirectory.from_config(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))).get_current_head()
