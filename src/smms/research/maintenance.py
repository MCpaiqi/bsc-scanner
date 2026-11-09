"""Non-blocking ownership for bounded reconciliation, including first startup."""

from hashlib import sha256

from sqlalchemy import func, select


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
