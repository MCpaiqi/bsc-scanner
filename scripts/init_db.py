"""Apply database migrations to the configured PostgreSQL database."""

from alembic import command
from alembic.config import Config

if __name__ == "__main__":
    command.upgrade(Config("alembic.ini"), "head")
    print("Database migrations applied.")
