"""Point-in-time signal generation and statistically correct research backtesting."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from types import MappingProxyType
from typing import Any

from smms.evaluation import PATH_EVALUATION_VERSION, PricePoint, evaluate_price_path

HORIZONS = (("30s", timedelta(seconds=30)), ("1m", timedelta(minutes=1)), ("5m", timedelta(minutes=5)), ("15m", timedelta(minutes=15)), ("30m", timedelta(minutes=30)), ("1h", timedelta(hours=1)), ("4h", timedelta(hours=4)), ("24h", timedelta(hours=24)))
TARGETS = (Decimal("0.10"), Decimal("0.25"), Decimal("0.50"), Decimal(1), Decimal(2), Decimal(5), Decimal(10))
STOPS = (Decimal("-0.10"), Decimal("-0.25"), Decimal("-0.50"), Decimal("-0.90"))

def _dt(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

def _dec(value: Any) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

def _get(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)

def _observation_time(item: Any, default: datetime | None = None) -> datetime | None:
    value = _get(item, "timestamp") or _get(item, "as_of", default)
    if isinstance(value, datetime):
        return _dt(value)
    return _dt(datetime.fromisoformat(str(value))) if value else None

@dataclass(frozen=True)
class FeaturePriceHistory:
    """Signal-facing price capability: contains only observations at/before as_of."""

    as_of: datetime
    observations: tuple[Any, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(
            MappingProxyType(dict(p)) if isinstance(p, Mapping) else p
            for p in self.observations))
        if any(_observation_time(p) is None or _observation_time(p) > _dt(self.as_of)
               for p in self.observations):
            raise ValueError("feature price is not observable at as_of")

    @classmethod
    def from_observations(cls, prices: Iterable[Any], as_of: datetime) -> FeaturePriceHistory:
        return cls(_dt(as_of), tuple(p for p in prices
                   if _observation_time(p) is not None and _observation_time(p) <= _dt(as_of)))


@dataclass(frozen=True)
class SignalWeights:
    market: Decimal = Decimal("0.25")
    smart_money: Decimal = Decimal("0.30")
    early_buyer: Decimal = Decimal("0.20")
    liquidity: Decimal = Decimal("0.10")
    risk: Decimal = Decimal("0.15")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> SignalWeights:
        if not value:
            return cls()
        result = cls(**{name: Decimal(str(value[name])) for name in cls.__dataclass_fields__ if name in value})
        if any(getattr(result, name) < 0 for name in cls.__dataclass_fields__) or sum((getattr(result, name) for name in cls.__dataclass_fields__), Decimal()) <= 0:
            raise ValueError("signal weights must be non-negative and non-zero")
        return result

    def normalized(self) -> dict[str, Decimal]:
        total = sum((getattr(self, name) for name in self.__dataclass_fields__), Decimal())
        return {name: getattr(self, name) / total for name in self.__dataclass_fields__}

@dataclass(frozen=True)
class TokenSignal:
    token: str
    timestamp: datetime
    signal: str
    score: Decimal
    confidence: Decimal
    smart_money_score: Decimal | None
    risk_level: str
    market_score: Decimal | None
    entry_reasons: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    data_status: str = "OK"
    model_version: str = "signal-v1"
    entry_price: Decimal | None = None
    entry_market_cap: Decimal | None = None
    entry_liquidity: Decimal | None = None
    config_version: str = "config-v1"
    config_hash: str = ""
    entry_price_age_seconds: int | None = None
    entry_eligible: bool = False
    confidence_components: dict[str, Decimal] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        result["timestamp"] = self.timestamp.isoformat()
        for key in ("score", "confidence", "smart_money_score", "market_score", "entry_price", "entry_market_cap", "entry_liquidity"):
            if result[key] is not None:
                result[key] = str(result[key])
        result["confidence_components"] = {k: str(v) for k, v in self.confidence_components.items()}
        result["entry_reasons"] = list(self.entry_reasons)
        result["risk_flags"] = list(self.risk_flags)
        return result

@dataclass(frozen=True)
class Evaluation:
    horizon: str
    return_pct: Decimal | None
    high_return: Decimal | None
    low_return: Decimal | None
    available: bool
    hit_targets: tuple[str, ...] = ()
    hit_stops: tuple[str, ...] = ()
    time_to_ath_seconds: int | None = None
    time_to_drawdown_seconds: int | None = None
    rug: bool = False
    max_drawdown: Decimal | None = None
    max_adverse_excursion: Decimal | None = None
    max_favorable_excursion: Decimal | None = None
    reason: str | None = None
    endpoint_at: datetime | None = None
    endpoint_offset_seconds: float | None = None
    evaluation_version: str = PATH_EVALUATION_VERSION

    def as_dict(self) -> dict[str, Any]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        for key in ("return_pct", "high_return", "low_return", "max_drawdown", "max_adverse_excursion", "max_favorable_excursion"):
            if result[key] is not None:
                result[key] = str(result[key])
        result["hit_targets"] = list(result["hit_targets"])
        result["hit_stops"] = list(result["hit_stops"])
        result["endpoint_at"] = self.endpoint_at.isoformat() if self.endpoint_at else None
        return result

@dataclass(frozen=True)
class BacktestResult:
    strategy: str
    model_version: str
    status: str
    signals: tuple[TokenSignal, ...]
    evaluations: tuple[Evaluation, ...]
    summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "model_version": self.model_version, "status": self.status, "signals": [item.as_dict() for item in self.signals], "evaluations": [item.as_dict() for item in self.evaluations], "summary": self.summary}

def _latest(rows: Iterable[Any], timestamp: datetime) -> Any | None:
    cutoff = _dt(timestamp)
    eligible = [row for row in rows if (_observation_time(row, cutoff) or cutoff) <= cutoff]
    return max(eligible, key=lambda row: _observation_time(row, cutoff) or cutoff) if eligible else None

def _future_snapshot(value: Mapping[str, Any] | None, ts: datetime) -> bool:
    if not value:
        return False
    for key in ("as_of", "timestamp", "risk_as_of", "smart_money_as_of", "market_as_of"):
        if value.get(key) is not None and (_observation_time({"timestamp": value[key]}) or ts) > ts:
            return True
    return False

def _config_hash(strategy: str, threshold: Decimal, weights: SignalWeights, config_version: str) -> str:
    payload = {"strategy": strategy, "threshold": str(threshold), "weights": {k: str(v) for k, v in weights.__dict__.items()}, "config_version": config_version}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

def _market_score(market: Mapping[str, Any]) -> Decimal | None:
    direct = _dec(market.get("market_score", market.get("score")))
    if direct is not None:
        return max(Decimal(0), min(Decimal(100), direct))
    parts = [_dec(market.get(k)) for k in ("price_momentum", "volume_score", "market_state")]
    parts = [x for x in parts if x is not None]
    return sum(parts, Decimal()) / len(parts) if parts else None

def generate_signal(token: str, timestamp: datetime, *, market: Mapping[str, Any] | None = None, smart_money: Mapping[str, Any] | None = None, early_buyers: Iterable[Any] = (), risk: Mapping[str, Any] | None = None, prices: Iterable[Any] = (), feature_price_history: FeaturePriceHistory | None = None, weights: SignalWeights | None = None, threshold: Decimal = Decimal(50), model_version: str = "signal-v1", config_version: str = "config-v1", strategy: str = "composite", max_entry_price_age_seconds: int | None = 300, interesting_eligible: bool = False) -> TokenSignal:
    ts = _dt(timestamp); market, smart_money, risk = dict(market or {}), dict(smart_money or {}), dict(risk or {}); weights = weights or SignalWeights()
    market_score = _market_score(market); smart_score = _dec(smart_money.get("smart_money_score", smart_money.get("score"))); liquidity = _dec(market.get("liquidity", market.get("entry_liquidity")))
    history = FeaturePriceHistory.from_observations(
        feature_price_history.observations if feature_price_history is not None else prices, ts)
    price_row = _latest(history.observations, ts); entry_price = _dec(_get(price_row, "price")) if price_row else _dec(market.get("price", market.get("entry_price"))); entry_mc = _dec(_get(price_row, "market_cap")) if price_row else _dec(market.get("market_cap", market.get("entry_market_cap")) )
    age = int((ts - (_observation_time(price_row, ts) or ts)).total_seconds()) if price_row else None
    flags: list[str] = []
    future = _future_snapshot(market, ts) or _future_snapshot(smart_money, ts) or _future_snapshot(risk, ts)
    if future: flags.append("future_snapshot")
    early = [item for item in early_buyers if (_get(item, "first_buy_time", _get(item, "timestamp"))) is not None and _dt(_get(item, "first_buy_time", _get(item, "timestamp"))) <= ts]
    early_score = min(Decimal(100), Decimal(len(early)) * Decimal(10))
    # Older materialized dataset-v2 rows use `level`; preserve their risk meaning.
    risk_level = str(
        risk.get("overall_risk", risk.get("risk_level", risk.get("level", "UNKNOWN")))
    ).upper()
    risk_score = {"LOW": Decimal(100), "MEDIUM": Decimal(60), "HIGH": Decimal(20), "CRITICAL": Decimal(0)}.get(risk_level)
    values = {"market": market_score or Decimal(0), "smart_money": smart_score or Decimal(0), "early_buyer": early_score, "liquidity": min(Decimal(100), liquidity / Decimal(1000)) if liquidity is not None else Decimal(0), "risk": risk_score or Decimal(0)}
    key = strategy.replace("-", "_").lower()
    if key in {"market_only", "early_buyer_only", "early_buyer", "random", "universe", "universe_baseline"}:
        risk_level = "UNKNOWN"
        risk_score = None
        values["risk"] = Decimal(0)
    if key == "market_only": score = values["market"] * Decimal("0.7") + values["liquidity"] * Decimal("0.3")
    elif key in {"early_buyer_only", "early_buyer"}: score = values["early_buyer"]
    elif key in {"random", "universe", "universe_baseline"}: score = Decimal(int(hashlib.sha256(f"{token.lower()}:{ts.isoformat()}".encode()).hexdigest()[:8], 16) % 101)
    else:
        normalized = weights.normalized(); score = sum((values[name] * normalized[name] for name in values), Decimal())
    components = {"market": Decimal(1) if market_score is not None else Decimal(0), "smart_money": Decimal(1) if smart_score is not None else Decimal(0), "early_buyer": Decimal(1) if early else Decimal(0), "liquidity": Decimal(1) if liquidity is not None else Decimal(0), "risk": Decimal(1) if risk_score is not None else Decimal(0), "price_freshness": Decimal(1) if age is not None and (max_entry_price_age_seconds is None or age <= max_entry_price_age_seconds) else Decimal(0)}
    if key == "market_only":
        components["smart_money"] = Decimal(0)
        components["early_buyer"] = Decimal(0)
        components["risk"] = Decimal(0)
    confidence = sum(components.values(), Decimal()) / Decimal(len(components))
    if risk_level == "UNKNOWN": flags.append("risk_evidence_insufficient"); confidence *= Decimal("0.8")
    stale = age is not None and max_entry_price_age_seconds is not None and age > max_entry_price_age_seconds
    if stale: flags.append("STALE_ENTRY_PRICE")
    if risk_level in {"HIGH", "CRITICAL"}: flags.append(f"risk:{risk_level}")
    if liquidity is None: flags.append("missing_liquidity")
    signal = "HIGH_RISK" if risk_level in {"HIGH", "CRITICAL"} else "STRONG" if score >= threshold + 20 else "INTERESTING" if score >= threshold else "WATCH"
    eligible = (signal == "STRONG" or (signal == "INTERESTING" and interesting_eligible)) and not future and not stale
    status = "INSUFFICIENT_DATA" if future or entry_price is None else ("DEGRADED" if risk_level == "UNKNOWN" or stale else "OK")
    if future or stale: entry_price = None
    reasons = tuple(k for k, v in values.items() if v > 0) + tuple(flag for flag in flags if flag in {"future_snapshot", "STALE_ENTRY_PRICE", "risk_evidence_insufficient"})
    return TokenSignal(token, ts, signal, score.quantize(Decimal("0.01")), confidence.quantize(Decimal("0.01")), smart_score, risk_level, market_score, reasons, tuple(flags), status, model_version, entry_price, entry_mc, liquidity, config_version, _config_hash(strategy, threshold, weights, config_version), age, eligible, components)

def _tol(name: str, tolerance: timedelta | Mapping[str, timedelta] | None) -> timedelta:
    if isinstance(tolerance, Mapping): return tolerance.get(name, timedelta(0))
    return tolerance if isinstance(tolerance, timedelta) else timedelta(seconds=5)

def evaluate_signal(signal: TokenSignal, prices: Iterable[Any], *, rug_threshold: Decimal = Decimal("-0.90"), coverage_tolerance: timedelta | Mapping[str, timedelta] | None = None) -> tuple[Evaluation, ...]:
    points = [PricePoint(timestamp, _get(row, "price")) for row in prices
              if (timestamp := _observation_time(row)) is not None]
    out = []
    for name, delta in HORIZONS:
        result = evaluate_price_path(
            signal.entry_price, signal.timestamp, points, horizon=delta,
            tolerance=_tol(name, coverage_tolerance),
        )
        if result is None:
            out.append(Evaluation(name, None, None, None, False,
                                  reason="INSUFFICIENT_HORIZON_COVERAGE"))
            continue
        high, low = result.high_return, result.low_return
        ath = result.path[result.returns.index(high)]
        first_loss = next((p for p, value in zip(result.path, result.returns, strict=True)
                           if value <= Decimal("-0.10")), None)
        out.append(Evaluation(
            name, result.return_pct, high, low, True,
            tuple(f"{int(t * 100)}%" for t in TARGETS if high >= t),
            tuple(f"{int(abs(s) * 100)}%" for s in STOPS if low <= s),
            int((ath.timestamp - signal.timestamp).total_seconds()),
            int((first_loss.timestamp - signal.timestamp).total_seconds()) if first_loss else None,
            low <= rug_threshold, result.max_drawdown, low, high,
            endpoint_at=result.endpoint.timestamp,
            endpoint_offset_seconds=result.endpoint_offset_seconds,
        ))
    return tuple(out)

def _metric(items: list[Evaluation]) -> dict[str,Any]:
    vals=[x.return_pct for x in items if x.available and x.return_pct is not None]
    if not vals: return {"sample_size":0,"win_rate":None,"median_return":None,"mean_return":None,"profit_factor":None,"MFE":None,"MAE":None,"max_drawdown":None,"rug_rate":None,"target_hit_rates":{}}
    gains=sum((v for v in vals if v>0),Decimal()); losses=-sum((v for v in vals if v<0),Decimal())
    return {"sample_size":len(vals),"win_rate":str(sum(v>0 for v in vals)/len(vals)),"median_return":str(median(vals)),"mean_return":str(sum(vals,Decimal())/len(vals)),"profit_factor":str(gains/losses) if losses else None,"MFE":str(max((x.max_favorable_excursion for x in items if x.max_favorable_excursion is not None),default=Decimal(0))),"MAE":str(min((x.max_adverse_excursion for x in items if x.max_adverse_excursion is not None),default=Decimal(0))),"max_drawdown":str(min((x.max_drawdown for x in items if x.max_drawdown is not None),default=Decimal(0))),"rug_rate":str(sum(x.rug for x in items if x.available)/len(vals)),"target_hit_rates":{str(int(t*100))+"%":str(sum(str(int(t*100))+"%" in x.hit_targets for x in items if x.available)/len(vals)) for t in TARGETS}}

def summarize(evaluations: Iterable[Evaluation], *, capital: Decimal = Decimal(1000), unique_signal_count: int | None = None, strategy: str | None = None, model_version: str | None = None, data_status: str | None = None) -> dict[str,Any]:
    items=list(evaluations); per={name:_metric([x for x in items if x.horizon==name]) for name,_ in HORIZONS}; vals=[x.return_pct for x in items if x.available and x.return_pct is not None]; gains=sum((v for v in vals if v>0),Decimal()); losses=-sum((v for v in vals if v<0),Decimal()); result={"status":"OK" if vals else "INSUFFICIENT_DATA","sample_size":unique_signal_count if unique_signal_count is not None else len(vals),"unique_signal_count":unique_signal_count if unique_signal_count is not None else len(items),"per_horizon_metrics":per,"capital":str(capital),"win_rate":str(sum(v>0 for v in vals)/len(vals)) if vals else None,"median_return":str(median(vals)) if vals else None,"mean_return":str(sum(vals,Decimal())/len(vals)) if vals else None,"profit_factor":str(gains/losses) if losses else None,"max_drawdown":str(min((x.max_drawdown for x in items if x.max_drawdown is not None),default=Decimal(0))) if vals else None,"max_adverse_excursion":str(min((x.max_adverse_excursion for x in items if x.max_adverse_excursion is not None),default=Decimal(0))) if vals else None,"max_favorable_excursion":str(max((x.max_favorable_excursion for x in items if x.max_favorable_excursion is not None),default=Decimal(0))) if vals else None,"rug_rate":str(sum(x.rug for x in items if x.available)/len(vals)) if vals else None}
    result["evaluation_version"] = PATH_EVALUATION_VERSION
    if strategy is not None: result["strategy"]=strategy
    if model_version is not None: result["model_version"]=model_version
    if data_status is not None: result["data_status"]=data_status
    return result

def run_backtest(rows: Iterable[Mapping[str,Any]], *, strategy: str = "composite", threshold: Decimal = Decimal(50), capital: Decimal = Decimal(1000), model_version: str = "signal-v1", config_version: str = "config-v1", weights: SignalWeights | None = None, max_entry_price_age_seconds: int | None = 300, coverage_tolerance: timedelta | Mapping[str,timedelta] | None = None, interesting_eligible: bool = False) -> BacktestResult:
    rows=list(rows); signals=[]; evaluations=[]; seen=set()
    for row in rows:
        signal=generate_signal(row["token"],_dt(row["timestamp"]),market=row.get("market"),smart_money=row.get("smart_money"),early_buyers=row.get("early_buyers",()),risk=row.get("risk"),feature_price_history=FeaturePriceHistory.from_observations(row.get("feature_price_history", row.get("prices", ())), _dt(row["timestamp"])),threshold=threshold,model_version=model_version,config_version=config_version,strategy=strategy,weights=weights,max_entry_price_age_seconds=max_entry_price_age_seconds,interesting_eligible=interesting_eligible); identity=(signal.token.lower(),signal.timestamp,strategy)
        if identity in seen: continue
        seen.add(identity); signals.append(signal)
        if signal.entry_eligible: evaluations.extend(evaluate_signal(signal,row.get("evaluation_price_path", row.get("future_prices", row.get("prices", ()))),coverage_tolerance=coverage_tolerance))
    status="DEGRADED" if any(s.data_status!="OK" for s in signals) else "OK"; summary=summarize(evaluations,capital=capital,unique_signal_count=len(signals),strategy=strategy,model_version=model_version,data_status=status); return BacktestResult(strategy,model_version,summary["status"],tuple(signals),tuple(evaluations),summary)

def baseline_comparison(rows: Iterable[Mapping[str,Any]], *, threshold: Decimal = Decimal(50), capital: Decimal = Decimal(1000), model_version: str = "signal-v1") -> dict[str,Any]:
    rows=list(rows); results={name:run_backtest(rows,strategy=name,threshold=threshold,capital=capital,model_version=model_version) for name in ("composite","market_only","early_buyer_only","random")}; payload={name:r.summary for name,r in results.items()}; payload["smart_money"]=payload["composite"]; payload["status"] = "OK" if any(r.status=="OK" for r in results.values()) else "INSUFFICIENT_DATA"; return payload

def walk_forward(rows: Iterable[Mapping[str,Any]], *, train: float = 0.6, validation: float = 0.2, threshold: Decimal = Decimal(50), capital: Decimal = Decimal(1000), model_version: str = "signal-v1", n_folds: int = 3, mode: str = "expanding") -> dict[str,Any]:
    ordered=sorted(rows,key=lambda r:_dt(r["timestamp"])); n=len(ordered)
    if not ordered or train<=0 or validation<=0 or train+validation>=1: return {"status":"INSUFFICIENT_DATA","train":{},"validation":{},"test":{},"folds":[]}
    te=max(1,int(n*train)); ve=max(te+1,int(n*(train+validation))); partitions={"train":ordered[:te],"validation":ordered[te:ve],"test":ordered[ve:]}; result={k:run_backtest(v,threshold=threshold,capital=capital,model_version=model_version).summary for k,v in partitions.items()}; folds=[]; step=max(1,(n-ve)//max(1,n_folds))
    for i in range(n_folds):
        a=te+i*step; b=min(n,a+max(1,ve-te)); c=min(n,b+max(1,ve-te))
        if c<=b: break
        folds.append({"train":(ordered[0]["timestamp"],ordered[a-1]["timestamp"]),"validation":(ordered[a]["timestamp"],ordered[b-1]["timestamp"]),"test":(ordered[b]["timestamp"],ordered[c-1]["timestamp"]),"fold_isolation":True})
    result.update({"status":"OK" if result["test"].get("sample_size",0) else "INSUFFICIENT_DATA","model_version":model_version,"folds":folds,"fold_isolation":True}); return result

def as_of(rows: Iterable[Any], cutoff: datetime) -> list[Any]:
    point=_dt(cutoff); return [row for row in rows if (_observation_time(row) is not None and _observation_time(row)<=point)]

__all__=["HORIZONS","BacktestResult","Evaluation","SignalWeights","TokenSignal","as_of","baseline_comparison","evaluate_signal","generate_signal","run_backtest","summarize","walk_forward"]
