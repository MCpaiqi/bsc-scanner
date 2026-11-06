"""Do not finalize corrected outputs before their invalidated inputs recover."""

from sqlalchemy import select

from smms.db.models import ResearchRebuildJob

from .publication import IDENTITY, MODELS
from .state import capture_epoch


class DependenciesPending(ValueError):
    pass


def require_rebuilt_dependencies(session, row):
    dependencies = list((row.input_manifest or {}).get("dependencies", []))
    for field, kind in (("smart_snapshot_id", "token_smart_money_snapshots"),
                        ("risk_snapshot_id", "token_risk_snapshots")):
        identity = getattr(row, field, None) or (getattr(row, "metadata_json", None) or {}).get(field)
        if identity:
            dependencies.append({"kind": kind, "id": identity})
    for dependency in dependencies:
        model = MODELS.get(dependency.get("kind"))
        if model is None:
            raise DependenciesPending("unsupported dependency kind")
        source = session.scalar(select(model).where(model.id == dependency["id"])
                                .execution_options(include_invalid_snapshots=True, populate_existing=True))
        if source is None:
            continue  # A deleted source is explicitly unavailable, not a reusable value.
        epoch = capture_epoch(session, source.chain)
        current = session.scalar(select(model.id).where(*[
            getattr(model, key) == getattr(source, key) for key in IDENTITY[model]
        ], model.source_epoch == epoch, model.snapshot_status.in_(("VALID", "UNAVAILABLE")))
            .execution_options(include_invalid_snapshots=True).limit(1))
        if current is not None or source.source_epoch == -1:
            continue
        unsupported = session.scalar(select(ResearchRebuildJob.id).where(
            ResearchRebuildJob.kind == dependency["kind"], ResearchRebuildJob.snapshot_id == source.id,
            ResearchRebuildJob.requested_epoch == epoch, ResearchRebuildJob.status == "UNSUPPORTED",
        ).limit(1))
        if unsupported is None:
            raise DependenciesPending("canonical research dependencies are rebuilding")
