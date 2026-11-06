"""One visibility contract for ORM queries, aliases and direct identity lookups."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import with_loader_criteria

from smms.db.facts import canonical_fact_conditions
from smms.db.models import (
    BacktestDataset,
    BacktestDatasetRow,
    EarlyBuyer,
    LiveSignalObservation,
    LiveSignalOutcome,
    RadarFeatureObservation,
    ResearchState,
    Signal,
    TokenMarketSnapshot,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    WalletSmartMoneySnapshot,
    WalletTokenPosition,
)

SNAPSHOT_MODELS = (TokenMarketSnapshot, TokenRiskSnapshot, TokenSmartMoneySnapshot, WalletSmartMoneySnapshot,
                   EarlyBuyer, WalletTokenPosition, RadarFeatureObservation, Signal, BacktestDataset,
                   LiveSignalObservation, LiveSignalOutcome)


def visible_conditions(model):
    epoch = select(ResearchState.epoch).where(ResearchState.chain == model.chain).scalar_subquery()
    return (model.snapshot_status == "VALID",
            or_(model.source_epoch == -1 if model is TokenMarketSnapshot else False,
                model.source_epoch == func.coalesce(epoch, 0)),
            *(canonical_fact_conditions(model) if model is TokenMarketSnapshot else ()))


def is_current_snapshot(session, row):
    if row is None or row.id is None:
        return False
    model = type(row)
    return session.scalar(select(model.id).where(model.id == row.id, *visible_conditions(model))
                          .execution_options(include_invalid_snapshots=True)) is not None


def apply_snapshot_visibility(execute_state):
    if execute_state.is_select and not execute_state.execution_options.get("include_invalid_snapshots"):
        execute_state.statement = execute_state.statement.options(*[
            with_loader_criteria(model, and_(*visible_conditions(model)), include_aliases=True)
            for model in SNAPSHOT_MODELS
        ], with_loader_criteria(BacktestDatasetRow, select(BacktestDataset.id).where(
            BacktestDataset.id == BacktestDatasetRow.dataset_id, *visible_conditions(BacktestDataset),
        ).exists(), include_aliases=True))
