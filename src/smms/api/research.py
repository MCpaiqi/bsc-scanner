"""Read-only audit access, separate from trusted current research universes."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from smms.config.settings import get_settings
from smms.db.database import session_scope
from smms.db.models import (
    BacktestDataset,
    BacktestDatasetRow,
    ForwardEvaluationJob,
    LiveSignalObservation,
    ResearchEpoch,
    ResearchRebuildJob,
    ResearchState,
)
from smms.research.lineage import row_values
from smms.research.publication import MODELS

router = APIRouter(prefix="/api/research", tags=["research-audit"])


@router.get("/forward-jobs")
async def forward_jobs(limit: int = Query(50, ge=1, le=200), before_id: int | None = Query(None, ge=1)):
    query = select(ForwardEvaluationJob).join(LiveSignalObservation,
        LiveSignalObservation.id == ForwardEvaluationJob.observation_id).where(LiveSignalObservation.chain == "bsc")
    if before_id is not None:
        query = query.where(ForwardEvaluationJob.id < before_id)
    async with session_scope(get_settings()) as session:
        rows = (await session.scalars(query.order_by(ForwardEvaluationJob.id.desc()).limit(limit + 1)
                                      .execution_options(include_invalid_snapshots=True, populate_existing=True))).all()
    return {"audit_only": True, "jobs": [row_values(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.get("/datasets/{dataset_id}/rows")
async def dataset_audit_rows(dataset_id: int, limit: int = Query(50, ge=1, le=200),
                             before_id: int | None = Query(None, ge=1)):
    query = select(BacktestDatasetRow).where(BacktestDatasetRow.dataset_id == dataset_id)
    if before_id is not None:
        query = query.where(BacktestDatasetRow.id < before_id)
    async with session_scope(get_settings()) as session:
        dataset = await session.scalar(select(BacktestDataset.id).where(
            BacktestDataset.id == dataset_id, BacktestDataset.chain == "bsc",
        ).execution_options(include_invalid_snapshots=True))
        if dataset is None:
            raise HTTPException(404, "research dataset not found")
        rows = (await session.scalars(query.order_by(BacktestDatasetRow.id.desc()).limit(limit + 1)
                                      .execution_options(include_invalid_snapshots=True))).all()
    return {"audit_only": True, "dataset_id": dataset_id,
            "lineage_url": f"/api/research/snapshots/backtest_datasets/{dataset_id}",
            "rows": [row_values(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.get("/snapshots/{kind}/{snapshot_id}")
async def snapshot_lineage(kind: str, snapshot_id: int):
    model = MODELS.get(kind)
    if model is None:
        raise HTTPException(404, "research snapshot kind not found")
    async with session_scope(get_settings()) as session:
        row = await session.scalar(select(model).where(model.id == snapshot_id, model.chain == "bsc")
                                   .execution_options(include_invalid_snapshots=True, populate_existing=True))
        epoch = await session.scalar(select(ResearchState.epoch).where(ResearchState.chain == "bsc")) or 0
        payload = row_values(row) if row else None
    if payload is None:
        raise HTTPException(404, "research snapshot not found")
    current = row.snapshot_status == "VALID" and row.source_epoch in {-1, epoch}
    return {"audit_only": True, "current_epoch": epoch, "usable_current": current, "snapshot": payload}


@router.get("/epochs")
async def research_epochs(limit: int = Query(50, ge=1, le=200), before_epoch: int | None = Query(None, ge=1)):
    query = select(ResearchEpoch).where(ResearchEpoch.chain == "bsc")
    if before_epoch is not None:
        query = query.where(ResearchEpoch.epoch < before_epoch)
    async with session_scope(get_settings()) as session:
        rows = (await session.scalars(query.order_by(ResearchEpoch.epoch.desc()).limit(limit + 1))).all()
    return {"epochs": [row_values(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1].epoch if len(rows) > limit else None}


@router.get("/rebuilds")
async def research_rebuilds(limit: int = Query(50, ge=1, le=200), before_id: int | None = Query(None, ge=1)):
    query = select(ResearchRebuildJob)
    if before_id is not None:
        query = query.where(ResearchRebuildJob.id < before_id)
    async with session_scope(get_settings()) as session:
        rows = (await session.scalars(query.order_by(ResearchRebuildJob.id.desc()).limit(limit + 1))).all()
    return {"jobs": [row_values(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1].id if len(rows) > limit else None}
