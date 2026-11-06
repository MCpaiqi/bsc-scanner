"""Capture-before-read epochs make stale research publication fail closed."""

from sqlalchemy import select

from smms.db.models import ResearchEpoch, ResearchState


def insert_once(session, model, values, keys):
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    session.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=keys))


def capture_epoch(session, chain="bsc") -> int:
    # Production migration creates epoch one. Reading never acquires the state
    # writer lock, including an in-flight producer in an empty test database.
    return session.scalar(select(ResearchState.epoch).where(ResearchState.chain == chain)) or 0


def _initialize(session, chain):
    insert_once(session, ResearchState, {"chain": chain, "epoch": 1}, ["chain"])
    insert_once(session, ResearchEpoch, {"chain": chain, "epoch": 1, "reason": "INITIALIZED",
                                       "cause_json": {}}, ["chain", "epoch"])


def invalidate_derived(session, *, reason, cause, chain="bsc") -> int:
    _initialize(session, chain)
    state = session.scalar(select(ResearchState).where(ResearchState.chain == chain)
                           .with_for_update().execution_options(populate_existing=True))
    state.epoch += 1
    session.add(ResearchEpoch(chain=chain, epoch=state.epoch, reason=reason, cause_json=cause))
    session.flush()
    return state.epoch
