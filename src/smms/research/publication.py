"""Serialize one logical snapshot publication without holding the epoch lock."""

from sqlalchemy import select

from smms.db.models import (
    ResearchSnapshotLock,
    TokenMarketSnapshot,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    WalletSmartMoneySnapshot,
)

from .lineage import digest, manifest, row_values
from .state import insert_once

IDENTITY = {
    TokenMarketSnapshot: ("chain", "token_address", "pair_address", "source", "as_of"),
    TokenRiskSnapshot: ("chain", "token_address", "as_of", "risk_version"),
    TokenSmartMoneySnapshot: ("chain", "token_address", "as_of", "score_version"),
    WalletSmartMoneySnapshot: ("chain", "wallet_address", "as_of", "score_version", "profile_version"),
}
MODELS = {model.__tablename__: model for model in IDENTITY}


def publish_snapshot(session, record):
    model = type(record)
    identity = {name: getattr(record, name) for name in IDENTITY[model]}
    key = digest({"kind": model.__tablename__, **identity})
    insert_once(session, ResearchSnapshotLock, {"identity": key}, ["identity"])
    session.scalar(select(ResearchSnapshotLock).where(ResearchSnapshotLock.identity == key).with_for_update())
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
    return record
