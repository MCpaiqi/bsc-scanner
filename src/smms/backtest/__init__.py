"""Read-only point-in-time signal and backtesting primitives."""

from .dataset import (
    DatasetBuildReport,
    HistoricalDatasetBuilder,
    PointInTimeBacktestRow,
    load_materialized_dataset,
)
from .signal_engine import (
    BacktestResult,
    Evaluation,
    SignalWeights,
    TokenSignal,
    as_of,
    baseline_comparison,
    evaluate_signal,
    generate_signal,
    run_backtest,
    summarize,
    walk_forward,
)

__all__ = [
    "BacktestResult",
    "DatasetBuildReport",
    "Evaluation",
    "HistoricalDatasetBuilder",
    "PointInTimeBacktestRow",
    "SignalWeights",
    "TokenSignal",
    "as_of",
    "baseline_comparison",
    "evaluate_signal",
    "generate_signal",
    "load_materialized_dataset",
    "run_backtest",
    "summarize",
    "walk_forward",
]
