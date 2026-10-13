"""Evidence-preserving, read-only token risk analysis."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]
RISK_VERSION = "risk-v1"


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {"true", "yes", "1", "enabled"}:
            return True
        if value.lower() in {"false", "no", "0", "disabled"}:
            return False
    return None


def _level_from_score(score: Decimal | None) -> RiskLevel:
    if score is None:
        return "UNKNOWN"
    if score >= 80:
        return "CRITICAL"
    if score >= 55:
        return "HIGH"
    if score >= 25:
        return "MEDIUM"
    return "LOW"


@dataclass(frozen=True)
class RiskDimension:
    level: RiskLevel
    evidence: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    confidence: str = "LOW"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskInput:
    """Stable risk context assembled from ORM state and evidence providers."""

    token_address: str
    liquidity: Decimal | None = None
    market_cap: Decimal | None = None
    contract_evidence: Mapping[str, Any] = field(default_factory=dict)
    holder_evidence: Any = None
    behavior_evidence: Any = None
    external_evidence: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_token(cls, token: Any, resolved_evidence: Mapping[str, Any] | None = None) -> RiskInput:
        evidence = resolved_evidence or {}
        def value(name: str) -> Any:
            item = evidence.get(name)
            return _get(item, "resolved_value", _get(item, "value")) if item is not None else None
        return cls(
            token_address=str(_get(token, "address", _get(token, "token", "unknown"))),
            liquidity=_dec(_get(token, "current_liquidity", None)) or _dec(value("liquidity_usd")),
            market_cap=_dec(_get(token, "current_market_cap", None)) or _dec(value("market_cap_usd")),
            contract_evidence=dict(_get(token, "metadata_json", {}) or {}),
            holder_evidence=value("holders") or value("holder_distribution"),
            external_evidence=dict(evidence),
        )


@dataclass(frozen=True)
class BehaviorRisk(RiskDimension):
    bot_probability: Decimal | None = None
    wash_probability: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = super().as_dict()
        payload.update({"bot_probability": self.bot_probability, "wash_probability": self.wash_probability})
        return payload


@dataclass(frozen=True)
class TokenRiskSnapshot:
    token: str
    contract_risk: RiskDimension
    liquidity_risk: RiskDimension
    holder_risk: RiskDimension
    behavior_risk: BehaviorRisk
    bot_probability: Decimal | None
    wash_probability: Decimal | None
    overall_risk: RiskLevel
    confidence: str
    evidence: list[str]
    as_of: datetime
    risk_version: str = RISK_VERSION
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    evidence_provenance: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "contract_risk": self.contract_risk.as_dict(),
            "liquidity_risk": self.liquidity_risk.as_dict(),
            "holder_risk": self.holder_risk.as_dict(),
            "behavior_risk": self.behavior_risk.as_dict(),
            "bot_probability": self.bot_probability,
            "wash_probability": self.wash_probability,
            "overall_risk": self.overall_risk,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "as_of": self.as_of,
            "risk_version": self.risk_version,
            "updated_at": self.updated_at,
            "evidence_provenance": self.evidence_provenance,
        }


def analyze_contract_risk(contract: Any) -> RiskDimension:
    """Evaluate only explicitly supplied contract facts."""
    keys = ("code_exists", "is_proxy", "upgradeable", "owner", "ownership_renounced", "mintable", "blacklist", "whitelist", "pause", "trading_restriction", "max_transaction", "max_wallet", "transfer_restriction", "buy_tax", "sell_tax", "external_calls")
    facts = {key: _get(contract, key) for key in keys}
    evidence: list[str] = []
    score = Decimal(0)
    known = 0
    adverse = {"mintable", "blacklist", "whitelist", "pause", "trading_restriction", "transfer_restriction", "is_proxy", "upgradeable", "external_calls"}
    for key, value in facts.items():
        if value is None:
            continue
        known += 1
        if key in adverse and _bool(value):
            score += Decimal(18 if key in {"mintable", "blacklist", "upgradeable", "is_proxy"} else 10)
            evidence.append(key)
        elif key == "code_exists" and _bool(value) is False:
            score += 35
            evidence.append("code_missing")
        elif key == "ownership_renounced" and _bool(value) is False:
            score += 8
            evidence.append("owner_active")
        elif key in {"buy_tax", "sell_tax"}:
            tax = _dec(value)
            if tax is not None and tax > 10:
                score += min(Decimal(25), tax)
                evidence.append(f"high_{key}")
    if known == 0:
        return RiskDimension("UNKNOWN", ["contract_evidence_unavailable"], {}, "LOW")
    level: RiskLevel = _level_from_score(score) if score > 0 or known == len(facts) else "UNKNOWN"
    confidence = "HIGH" if known >= 12 else ("MEDIUM" if known >= 6 else "LOW")
    return RiskDimension(level, evidence or ["no_adverse_contract_flags_observed"], {"known_fields": known}, confidence)


def analyze_liquidity_risk(token: Any, events: list[Any] | None = None) -> RiskDimension:
    current = _dec(_get(token, "current_liquidity", _get(token, "liquidity_usd")))
    initial = _dec(_get(token, "initial_liquidity"))
    market_cap = _dec(_get(token, "current_market_cap", _get(token, "market_cap_usd")))
    evidence: list[str] = []
    score = Decimal(0)
    known = 0
    if current is not None:
        known += 1
        if current < 10_000:
            score += 45; evidence.append("low_current_liquidity")
        elif current < 50_000:
            score += 20; evidence.append("moderate_current_liquidity")
    if initial is not None and current is not None and initial > 0:
        known += 1
        change = (current - initial) / initial * 100
        if change <= -50:
            score += 35; evidence.append("liquidity_removed")
        elif change < -20:
            score += 18; evidence.append("liquidity_declining")
    if current is not None and market_cap and market_cap > 0:
        known += 1
        if current / market_cap < Decimal("0.03"):
            score += 30; evidence.append("low_liquidity_to_market_cap")
    metadata = _get(token, "metadata_json", {}) or {}
    if metadata.get("lp_concentration") is not None:
        known += 1
        concentration = _dec(metadata.get("lp_concentration"))
        if concentration is not None and concentration > 80:
            score += 25; evidence.append("lp_concentration")
    if metadata.get("lp_lock_evidence"):
        evidence.append("lp_lock_evidence")
    if known == 0:
        return RiskDimension("UNKNOWN", ["liquidity_evidence_unavailable"], {}, "LOW")
    level: RiskLevel = _level_from_score(score) if score > 0 or known == 3 else "UNKNOWN"
    return RiskDimension(level, evidence or ["liquidity_observed"], {"current_liquidity": current, "initial_liquidity": initial}, "HIGH" if known >= 2 else "LOW")


def analyze_holder_risk(token: Any, holders: list[Any] | None = None) -> RiskDimension:
    metadata = _get(token, "metadata_json", {}) or {}
    source = holders if holders is not None else metadata.get("holders")
    if not source:
        concentration = _dec(_get(token, "top_holder_concentration"))
        if concentration is None:
            return RiskDimension("UNKNOWN", ["holder_evidence_unavailable"], {}, "LOW")
        source = [{"share": concentration, "kind": "creator"}]
    shares: list[Decimal] = []
    evidence: list[str] = []
    excluded = {"lp", "burn", "router", "exchange", "protocol"}
    for holder in source:
        kind = str(_get(holder, "kind", "")).lower()
        if kind in excluded:
            continue
        share = _dec(_get(holder, "share", _get(holder, "percentage")))
        if share is not None:
            shares.append(share)
        if kind in {"creator", "deployer"} and share is not None and share > 10:
            evidence.append("creator_concentration")
    if not shares:
        return RiskDimension("UNKNOWN", ["holder_evidence_unavailable"], {}, "LOW")
    top = max(shares); top5 = sum(sorted(shares, reverse=True)[:5]); top10 = sum(sorted(shares, reverse=True)[:10])
    score = (Decimal(55) if top > 30 else Decimal(25) if top > 15 else Decimal(0)) + (Decimal(25) if top5 > 60 else Decimal(0)) + (Decimal(20) if top10 > 80 else Decimal(0))
    if top > 30: evidence.append("top_holder_concentration")
    if top5 > 60: evidence.append("top_5_concentration")
    if top10 > 80: evidence.append("top_10_concentration")
    return RiskDimension(_level_from_score(score), evidence or ["holder_distribution_observed"], {"top_holder": top, "top_5": top5, "top_10": top10}, "HIGH" if len(shares) >= 10 else "MEDIUM")


def analyze_behavior_risk(trades: list[Any] | None) -> BehaviorRisk:
    if not trades:
        return BehaviorRisk("UNKNOWN", ["behavior_evidence_unavailable"], {}, "LOW", None, None)
    wallets = [str(_get(t, "trader_address", _get(t, "wallet_address", ""))).lower() for t in trades]
    sizes = [_dec(_get(t, "usd_value", _get(t, "native_amount"))) for t in trades]
    sizes = [x for x in sizes if x is not None]
    evidence: list[str] = []
    bot = Decimal(0); wash = Decimal(0)
    if len(trades) >= 20 and len(set(wallets)) <= max(2, len(trades) // 5):
        bot += Decimal("0.35"); evidence.append("high_frequency")
    if sizes and len(set(sizes)) / len(sizes) < Decimal("0.35"):
        bot += Decimal("0.25"); evidence.append("repeated_size")
    counts = {wallet: wallets.count(wallet) for wallet in set(wallets)}
    if counts and max(counts.values()) / len(wallets) > 0.5:
        wash += Decimal("0.35"); evidence.append("buy_sell_concentration")
    sides = {str(_get(t, "side", "")).upper() for t in trades}
    if {"BUY", "SELL"} <= sides and len(set(wallets)) <= 3:
        wash += Decimal("0.30"); evidence.append("circular_trading")
    blocks = [_get(trade, "block_number") for trade in trades]
    timestamps = [str(_get(trade, "timestamp", "")) for trade in trades]
    if len(set(wallets)) > 1 and (
        any(block is not None and blocks.count(block) >= 3 for block in blocks)
        or any(timestamp and timestamps.count(timestamp) >= 3 for timestamp in timestamps)
    ):
        bot += Decimal("0.20"); evidence.append("same_token_synchronization")
    bot = min(Decimal(1), bot); wash = min(Decimal(1), wash)
    score = max(bot * 100, wash * 100)
    return BehaviorRisk(_level_from_score(score) if evidence else "LOW", evidence or ["no_behavior_flags_observed"], {"trade_count": len(trades), "wallet_count": len(set(wallets))}, "HIGH" if len(trades) >= 20 else "LOW", bot, wash)


def analyze_token_risk(token: Any, *, trades: list[Any] | None = None, holders: list[Any] | None = None, as_of: datetime | None = None, resolved_evidence: Mapping[str, Any] | None = None) -> TokenRiskSnapshot:
    contract_facts = dict(_get(token, "metadata_json", {}) or {})
    provenance: dict[str, Any] = {}
    if resolved_evidence:
        for name, item in resolved_evidence.items():
            value = _get(item, "resolved_value", _get(item, "value"))
            if value is not None and name not in contract_facts:
                contract_facts[name] = value
            if hasattr(item, "as_dict"):
                provenance[name] = item.as_dict()
            elif isinstance(item, Mapping):
                provenance[name] = dict(item)
    risk_token = token
    context = RiskInput.from_token(token, resolved_evidence)
    if resolved_evidence:
        merged = dict(token) if isinstance(token, Mapping) else {
            "address": context.token_address,
            "current_liquidity": context.liquidity,
            "current_market_cap": context.market_cap,
            "metadata_json": dict(context.contract_evidence),
        }
        metadata = dict(merged.get("metadata_json") or {})
        for name, item in resolved_evidence.items():
            value = _get(item, "resolved_value", _get(item, "value"))
            if value is None:
                continue
            if name in {"price_usd", "liquidity_usd", "market_cap_usd", "holder_count", "top_holder_concentration"}:
                mapping = {"price_usd": "price", "liquidity_usd": "current_liquidity", "market_cap_usd": "current_market_cap", "holder_count": "holder_count", "top_holder_concentration": "top_holder_concentration"}
                merged[mapping[name]] = value
            elif name in {"holders", "holder_distribution"}:
                metadata["holders"] = value
        merged["metadata_json"] = metadata
        risk_token = merged
    contract = analyze_contract_risk(contract_facts)
    liquidity = analyze_liquidity_risk(risk_token)
    holder = analyze_holder_risk(risk_token, holders)
    behavior = analyze_behavior_risk(trades)
    levels = [contract.level, liquidity.level, holder.level, behavior.level]
    overall: RiskLevel = "CRITICAL" if "CRITICAL" in levels else ("HIGH" if "HIGH" in levels else ("MEDIUM" if "MEDIUM" in levels else ("UNKNOWN" if "UNKNOWN" in levels else "LOW")))
    known = sum(level != "UNKNOWN" for level in levels)
    confidence = "HIGH" if known == 4 else ("MEDIUM" if known >= 2 else "LOW")
    evidence = list(dict.fromkeys(contract.evidence + liquidity.evidence + holder.evidence + behavior.evidence))
    token_id = context.token_address
    return TokenRiskSnapshot(str(token_id), contract, liquidity, holder, behavior, behavior.bot_probability, behavior.wash_probability, overall, confidence, evidence, as_of or datetime.now(UTC), evidence_provenance=provenance)


class RiskEngine:
    """Stable facade for one-token risk analysis."""

    version = RISK_VERSION

    def analyze(
        self,
        token: Any,
        *,
        trades: list[Any] | None = None,
        holders: list[Any] | None = None,
        as_of: datetime | None = None,
        resolved_evidence: Mapping[str, Any] | None = None,
    ) -> TokenRiskSnapshot:
        return analyze_token_risk(token, trades=trades, holders=holders, as_of=as_of, resolved_evidence=resolved_evidence)


class ContractRiskEngine:
    def analyze(self, contract: Any) -> RiskDimension:
        return analyze_contract_risk(contract)


class LiquidityRiskEngine:
    def analyze(self, token: Any, events: list[Any] | None = None) -> RiskDimension:
        return analyze_liquidity_risk(token, events)


class HolderRiskEngine:
    def analyze(self, token: Any, holders: list[Any] | None = None) -> RiskDimension:
        return analyze_holder_risk(token, holders)


class BehaviorRiskEngine:
    def analyze(self, trades: list[Any] | None) -> BehaviorRisk:
        return analyze_behavior_risk(trades)


__all__ = ["RISK_VERSION", "BehaviorRisk", "BehaviorRiskEngine", "ContractRiskEngine", "HolderRiskEngine", "LiquidityRiskEngine", "RiskDimension", "RiskEngine", "RiskLevel", "TokenRiskSnapshot", "analyze_behavior_risk", "analyze_contract_risk", "analyze_holder_risk", "analyze_liquidity_risk", "analyze_token_risk"]
