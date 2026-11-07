"""Serialize one logical snapshot publication without holding the epoch lock."""

from sqlalchemy import and_, select, update

from smms.db.models import (
    BacktestDataset,
    EarlyBuyer,
    LiveSignalObservation,
    LiveSignalOutcome,
    RadarFeatureObservation,
    ResearchSnapshotDependency,
    ResearchSnapshotLock,
    Signal,
    TokenMarketSnapshot,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    WalletSmartMoneySnapshot,
    WalletTokenPosition,
)

from .lineage import digest, manifest, row_values
from .state import insert_once

IDENTITY = {
    BacktestDataset: ("chain", "dataset_version"),
    TokenMarketSnapshot: ("chain", "token_address", "pair_address", "source", "as_of"),
    TokenRiskSnapshot: ("chain", "token_address", "as_of", "risk_version"),
    TokenSmartMoneySnapshot: ("chain", "token_address", "as_of", "score_version"),
    WalletSmartMoneySnapshot: ("chain", "wallet_address", "as_of", "score_version", "profile_version"),
    EarlyBuyer: ("chain", "wallet_address", "token_address", "as_of", "recipe_hash"),
    WalletTokenPosition: ("chain", "wallet_address", "token_address", "as_of", "recipe_hash"),
    RadarFeatureObservation: ("chain", "candidate_id", "as_of", "config_hash"),
    Signal: ("chain", "token_id", "created_at", "model_version"),
    LiveSignalObservation: ("chain", "candidate_id", "signal_class"),
    LiveSignalOutcome: ("chain", "observation_root_id", "horizon", "evaluation_version"),
}
MODELS = {model.__tablename__: model for model in IDENTITY}


def lock_identity(session, model, identity):
    key = digest({"kind": model.__tablename__, **identity})
    insert_once(session, ResearchSnapshotLock, {"identity": key}, ["identity"])
    session.scalar(select(ResearchSnapshotLock).where(ResearchSnapshotLock.identity == key).with_for_update())
    return key


def _lock_dependencies(session, record):
    from .visibility import visible_conditions

    valid = True
    dependencies = sorted((record.input_manifest or {}).get("dependencies", []),
                          key=lambda item: (item.get("kind", ""), item.get("id", 0)))
    for dependency in dependencies:
        model = MODELS.get(dependency.get("kind"))
        if model is None:
            valid = False
            continue
        source = session.scalar(select(model).where(model.id == dependency["id"])
                                .with_for_update(read=True)
                                .execution_options(include_invalid_snapshots=True, populate_existing=True))
        conditions = visible_conditions(model)
        if record.snapshot_status == "UNAVAILABLE":
            conditions = (model.snapshot_status.in_(("VALID", "UNAVAILABLE")), *conditions[1:])
        if (source is None or session.scalar(select(model.id).where(
            model.id == source.id, *conditions,
        ).execution_options(include_invalid_snapshots=True)) is None
                or dependency.get("digest") != (source.input_manifest or {}).get("digest")):
            valid = False
    return valid


def _invalidate_dependents(session, source):
    edges = ResearchSnapshotDependency.__table__
    affected = select(edges.c.parent_kind.label("kind"), edges.c.parent_id.label("id")).where(
        edges.c.source_kind == source.__tablename__, edges.c.source_id == source.id,
    ).cte("research_affected", recursive=True)
    affected = affected.union(select(edges.c.parent_kind, edges.c.parent_id).join(
        affected, and_(edges.c.source_kind == affected.c.kind, edges.c.source_id == affected.c.id),
    ))
    for kind, model in MODELS.items():
        session.execute(update(model).where(
            model.id.in_(select(affected.c.id).where(affected.c.kind == kind)),
            model.snapshot_status.in_(("VALID", "UNAVAILABLE")),
        ).values(snapshot_status="INVALIDATED").execution_options(synchronize_session=False))


def publish_snapshot(session, record):
    model = type(record)
    identity = {name: getattr(record, name) for name in IDENTITY[model]}
    key = lock_identity(session, model, identity)
    latest = session.scalar(select(model).where(*[
        getattr(model, name) == value for name, value in identity.items()
    ]).order_by(model.research_revision.desc(), model.id.desc()).limit(1)
        .execution_options(include_invalid_snapshots=True, populate_existing=True))
    if not record.input_manifest:
        # Direct/import callers retain epoch zero, unavailable after migration.
        record.input_manifest = manifest(kind=model.__tablename__, parameters=identity,
                                         facts={"legacy_import": row_values(record)},
                                         notes=["UNVERIFIED_DIRECT_IMPORT"])
    record.source_epoch = record.source_epoch if record.source_epoch is not None else 0
    status = record.snapshot_status or "VALID"
    if latest is not None and ((latest.source_epoch >= 0 and record.source_epoch >= 0
                                and latest.source_epoch > record.source_epoch)
                               or (latest.source_epoch == -1 and record.source_epoch != -1)):
        return latest
    if (session.info.get("research_rebuild_root") == key and latest is not None
            and latest.source_epoch == record.source_epoch
            and latest.snapshot_status in {"VALID", "UNAVAILABLE"}):
        # A live refresh won while this old job was reading facts. Keep its
        # known configuration instead of replacing it with a legacy default.
        return latest
    if not _lock_dependencies(session, record):
        status = "INVALIDATED"
    if (latest is not None and latest.source_epoch == record.source_epoch
            and latest.snapshot_status == status
            and latest.input_manifest.get("digest") == record.input_manifest.get("digest")):
        return latest
    if latest is not None:
        latest.snapshot_status = "SUPERSEDED"
    record.research_revision = latest.research_revision + 1 if latest is not None else 0
    record.previous_snapshot_id = latest.id if latest is not None else None
    record.snapshot_status = status
    session.add(record)
    session.flush()
    for dependency in record.input_manifest.get("dependencies", []):
        if dependency.get("kind") in MODELS:
            insert_once(session, ResearchSnapshotDependency, {
                "parent_kind": record.__tablename__, "parent_id": record.id,
                "source_kind": dependency["kind"], "source_id": dependency["id"],
                "source_digest": dependency.get("digest"),
            }, ["parent_kind", "parent_id", "source_kind", "source_id"])
    if latest is not None:
        _invalidate_dependents(session, latest)
    return record
