from datetime import UTC, datetime

from sqlalchemy import DateTime, event, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


@event.listens_for(Session, "do_orm_execute")
def _research_visibility(execute_state):
    if execute_state.is_select and not execute_state.execution_options.get("include_invalid_snapshots"):
        from smms.research.visibility import apply_snapshot_visibility

        apply_snapshot_visibility(execute_state)


@event.listens_for(Session, "before_flush")
def _immutable_research_inputs(session, _context, _instances):
    from .research import SnapshotLineageMixin

    for row in session.dirty:
        if isinstance(row, SnapshotLineageMixin):
            state = inspect(row)
            status = state.attrs.snapshot_status.history
            changed = any(state.attrs[column.key].history.has_changes()
                          for column in state.mapper.column_attrs if column.key != "snapshot_status")
            if changed or (status.has_changes() and row.snapshot_status != "SUPERSEDED"):
                raise ValueError("published research inputs and results are immutable; append a revision")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

