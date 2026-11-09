"""Non-blocking ownership for bounded reconciliation, including first startup."""

from contextlib import contextmanager
from hashlib import sha256

from sqlalchemy import func, select, text


def try_repair_ownership(session, component, version):
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return True
    schema = bind.get_execution_options().get("schema_translate_map", {}).get(None)
    if schema is None:
        schema = session.scalar(select(func.current_schema()))
    # PostgreSQL advisory locks are database-local; include the effective schema
    # so independent workspaces/tenants do not suppress each other's repairs.
    key = int.from_bytes(sha256(f"smms:{schema}:{component}:{version}".encode()).digest()[:8], "big", signed=True)
    return bool(session.scalar(select(func.pg_try_advisory_xact_lock(key))))


@contextmanager
def bounded_lock_wait(session, *, milliseconds=250):
    """Temporarily cap lock waits inside a caller-owned SAVEPOINT.

    Success restores the exact previous setting. On an exception the caller's
    SAVEPOINT rollback restores it, including an aborted PostgreSQL statement.
    This bounds lock waits, not query execution or CPU time.
    """
    if not session.in_nested_transaction():
        raise ValueError("bounded lock waits require a SAVEPOINT")
    if type(milliseconds) is not int or not 1 <= milliseconds <= 10_000:
        raise ValueError("lock wait must be between 1 and 10000 milliseconds")
    if session.get_bind().dialect.name != "postgresql":
        yield
        return
    previous, configured = session.execute(text(
        "SELECT current_setting('lock_timeout'), setting::bigint FROM pg_settings WHERE name='lock_timeout'",
    )).one()
    budget = min(configured or milliseconds, milliseconds)
    session.execute(select(func.set_config("lock_timeout", f"{budget}ms", True)))
    yield
    session.execute(select(func.set_config("lock_timeout", previous, True)))
