"""One-statement candidate selection/counts; hydrate only the requested page."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Numeric,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    true,
    type_coerce,
)
from sqlalchemy.orm import load_only

from smms.db.facts import canonical_fact_conditions
from smms.db.models import Pair, RadarCandidate, RadarFeatureObservation, Signal, Token
from smms.research.state import capture_epoch


class StaleCandidateRead(ValueError):
    pass


@dataclass(frozen=True)
class CandidateSelection:
    candidate: RadarCandidate
    token: Token
    projection: RadarFeatureObservation | None
    signal: Signal | None
    pair: Pair | None
    projection_digest: str | None
    signal_digest: str | None
    values: dict


def _kind(document, key, dialect):
    return func.json_type(document, f"$.{key}") if dialect == "sqlite" else func.json_typeof(document[key])


def _metric(document, key, dialect):
    text = func.trim(cast(document[key].as_string(), String))
    pattern = r"^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]{1,3})?$"
    kind = _kind(document, key, dialect)
    valid_kind = kind.in_(("integer", "real", "text") if dialect == "sqlite" else ("number", "string"))
    # Bound exponent/text length before CAST, including corrupt legacy JSON.
    value = case((and_(valid_kind, func.length(text) <= 80, text.regexp_match(pattern)), cast(text, Numeric(asdecimal=False))))
    # The browser's numeric contract is finite IEEE-754. Preserve sub-cent
    # values without Numeric's default ten-decimal result quantization.
    representable = and_(value >= 0, value <= Decimal("1.7976931348623157e308"),
                         or_(and_(value == 0, text.regexp_match(r"^[+-]?(0+(\.0*)?|\.0+)([eE][+-]?[0-9]{1,3})?$")),
                             value >= Decimal("4.9406564584124654e-324")))
    return case((representable, value))


def _ordering(table, sort, direction):
    column = table.c[{"age": "discovered_at", "liquidity": "liquidity", "volume": "volume"}.get(sort, "priority")]
    return ((column.asc() if direction == "asc" else column.desc()).nulls_last(),
            table.c.chain, table.c.token_address, table.c.candidate_id)


def select_candidates(session, *, at, epoch, chain=None, status=None, min_priority=None, launchpad=None,
                      risk=None, age=None, limit=100, offset=0, sort="priority", priority=None,
                      smart_money=None, liquidity=None, volume=None, q=None, direction="desc",
                      view="all", freshness="all", include_stale=False, exact_token=None,
                      include_rejected=False):
    c, t = RadarCandidate.__table__.alias("browse_candidate"), Token.__table__.alias("browse_token")
    p, s = RadarFeatureObservation.__table__.alias("browse_projection"), Signal.__table__.alias("browse_signal")
    pair = Pair.__table__.alias("browse_pair")
    dialect = session.get_bind().dialect.name
    legacy = and_(literal(epoch == 0), c.c.projection_id.is_(None))
    usable = or_(legacy, p.c.snapshot_status == "VALID")
    pending = and_(p.c.id.is_(None), ~legacy)
    features = case((p.c.id.is_not(None), p.c.features_json), (legacy, c.c.features_json),
                    else_=literal({}, type_=JSON))
    ranking = case((p.c.id.is_not(None), p.c.priority_json), (legacy, c.c.priority_json),
                   else_=literal({}, type_=JSON))
    numeric_priority = _metric(ranking, "priority_score", dialect)
    priority_score = type_coerce(case((and_(legacy, _kind(ranking, "priority_score", dialect).is_(None)), c.c.priority_score),
                                     else_=numeric_priority), Numeric(asdecimal=False))
    risk_text = func.upper(func.trim(features["risk"].as_string()))
    risk_level = case((risk_text.in_(("LOW", "MEDIUM", "HIGH", "CRITICAL")), risk_text), else_="UNKNOWN")
    status_value = case((usable, cast(c.c.status, String)), (p.c.id.is_not(None), "REJECTED"), else_="UNKNOWN")
    observed = case((~pending, c.c.latest_seen_at))
    fresh = case((or_(observed.is_(None), observed > at), "UNKNOWN"),
                 (observed >= at - timedelta(seconds=60), "FRESH"),
                 (observed >= at - timedelta(seconds=3600), "STALE"), else_="EXPIRED")
    data_status = case((pending, "REBUILDING"), (p.c.id.is_not(None), p.c.data_status), else_=c.c.data_status)
    source = case((pending, "REBUILDING"),
                  (and_(data_status.not_in(("UNKNOWN", "ORPHANED", "REBUILDING")), fresh == "FRESH"), "OK"),
                  (fresh.in_(("STALE", "EXPIRED")), "STALE"), else_=data_status)
    pair_id = select(Pair.id).where(
        Pair.chain_id == 56, *canonical_fact_conditions(Pair), Pair.creation_timestamp <= at,
        or_(Pair.token0_address == t.c.address, Pair.token1_address == t.c.address),
    ).order_by(Pair.creation_block.desc().nulls_last(), Pair.id.desc()).limit(1).correlate(t).scalar_subquery()
    platform = func.coalesce(func.nullif(p.c.context_json["launchpad"].as_string(), ""),
                             case((legacy, func.nullif(t.c.launchpad, ""))),
                             case((pair.c.dex == "pancakeswap", "PancakeSwap V2"), else_="UNKNOWN"))
    volume_value = case((_kind(features, "volume_5m", dialect).is_(None), _metric(features, "volume", dialect)),
                        else_=_metric(features, "volume_5m", dialect))
    signal_score = case((and_(s.c.signal_strength >= 0, s.c.signal_strength <= 100), s.c.signal_strength))
    base = select(
        c.c.id.label("candidate_id"), t.c.id.label("token_id"), t.c.chain, t.c.address.label("token_address"),
        t.c.symbol, t.c.name, pair.c.address.label("pair_address"), p.c.id.label("projection_id"), s.c.id.label("signal_id"), pair.c.id.label("pair_id"),
        status_value.label("status"), priority_score.label("priority"), platform.label("launchpad"),
        _metric(features, "liquidity", dialect).label("liquidity"), volume_value.label("volume"),
        _metric(features, "smart_money_score", dialect).label("smart_money"), risk_level.label("risk"),
        signal_score.label("signal_score"), c.c.discovered_at, observed.label("observed_at"),
        fresh.label("freshness"), source.label("source_status"),
    ).select_from(c.join(t, c.c.token_id == t.c.id).outerjoin(p, and_(
        p.c.id == c.c.projection_id, p.c.candidate_id == c.c.id, p.c.chain == t.c.chain, p.c.token_address == t.c.address,
        p.c.source_epoch == epoch, p.c.snapshot_status.in_(("VALID", "UNAVAILABLE")), p.c.as_of <= at,
    )).outerjoin(s, and_(s.c.id == c.c.latest_signal_id, s.c.token_id == t.c.id, s.c.chain == t.c.chain,
                         s.c.source_epoch == epoch, s.c.snapshot_status == "VALID", usable,
                         s.c.created_at <= case((p.c.id.is_not(None), p.c.as_of), else_=at)))
      .outerjoin(pair, pair.c.id == pair_id))
    if chain:
        base = base.where(t.c.chain == chain)
    if exact_token:
        base = base.where(func.lower(t.c.address) == exact_token.lower())
    universe = base.cte("candidate_index")
    conditions = []
    if min_priority is not None or priority is not None:
        conditions.append(universe.c.priority >= max(min_priority or 0, priority or 0))
    if status is not None:
        conditions.append(universe.c.status == getattr(status, "value", status))
    elif not include_rejected:
        conditions.append(universe.c.status != "REJECTED")
    if not include_stale and status is None:
        conditions.append(universe.c.status != "STALE")
    if q and q.strip():
        needle = q.strip().lower()
        conditions.append(or_(*(func.lower(func.coalesce(column, "")).contains(needle, autoescape=True)
                                for column in (universe.c.symbol, universe.c.name, universe.c.token_address, universe.c.pair_address, universe.c.launchpad))))
    if launchpad:
        conditions.append(func.lower(universe.c.launchpad) == launchpad.strip().lower())
    if risk:
        conditions.append(universe.c.risk == risk)
    if age is not None:
        conditions.append(universe.c.discovered_at >= at - timedelta(seconds=age))
    for key, minimum in (("smart_money", smart_money), ("liquidity", liquidity), ("volume", volume)):
        if minimum is not None:
            conditions.append(universe.c[key] >= minimum)
    if freshness == "fresh":
        conditions.append(universe.c.source_status == "OK")
    elif freshness == "stale":
        conditions.append(universe.c.freshness.in_(("STALE", "EXPIRED")))
    elif freshness == "unknown":
        conditions.append(and_(universe.c.source_status != "OK", universe.c.freshness.not_in(("STALE", "EXPIRED"))))
    filtered = select(universe).where(*conditions).cte("filtered_candidates")
    high_risk = filtered.c.risk.in_(("HIGH", "CRITICAL"))
    facets = {"all": true(), "high_signal": filtered.c.signal_score >= 60, "high_risk": high_risk,
              "watch": and_(~high_risk, or_(filtered.c.signal_score.is_(None), filtered.c.signal_score < 60))}
    if view not in facets or sort not in {"priority", "age", "liquidity", "volume"} or direction not in {"asc", "desc"}:
        raise ValueError("unsupported candidate query")
    summary = select(func.count().filter(facets[view]).label("total"),
                     *(func.count().filter(condition).label("count_" + name) for name, condition in facets.items()),
                     select(func.count()).select_from(universe).scalar_subquery().label("universe_total"))
    summary = summary.select_from(filtered).cte("candidate_counts")
    page = select(filtered).where(facets[view]).order_by(*_ordering(filtered, sort, direction)).limit(limit).offset(offset).cte("candidate_page")
    statement = select(RadarCandidate, Token, RadarFeatureObservation, Signal, Pair,
                       RadarFeatureObservation.input_manifest["digest"].as_string().label("projection_digest"),
                       Signal.input_manifest["digest"].as_string().label("signal_digest"), *summary.c, *page.c).select_from(
        summary.outerjoin(page, true())).outerjoin(RadarCandidate, RadarCandidate.id == page.c.candidate_id).outerjoin(
        Token, Token.id == page.c.token_id).outerjoin(RadarFeatureObservation, RadarFeatureObservation.id == page.c.projection_id).outerjoin(
        Signal, Signal.id == page.c.signal_id).outerjoin(Pair, Pair.id == page.c.pair_id).order_by(*_ordering(page, sort, direction)).options(
            load_only(RadarFeatureObservation.id, RadarFeatureObservation.chain, RadarFeatureObservation.candidate_id,
                      RadarFeatureObservation.as_of, RadarFeatureObservation.snapshot_status, RadarFeatureObservation.source_epoch,
                      RadarFeatureObservation.research_revision, RadarFeatureObservation.features_json,
                      RadarFeatureObservation.priority_json, RadarFeatureObservation.context_json, RadarFeatureObservation.data_status),
            load_only(Signal.id, Signal.chain, Signal.token_id, Signal.created_at, Signal.signal_type, Signal.signal_strength,
                      Signal.source_epoch, Signal.research_revision, Signal.snapshot_status),
        ).execution_options(include_invalid_snapshots=True, populate_existing=True)
    rows = session.execute(statement).all()
    mapping = rows[0]._mapping
    metadata = {"total": mapping["total"], "universe_total": mapping["universe_total"],
                "tab_counts": {name: mapping["count_" + name] for name in facets}, "source_epoch": epoch}
    selected = [CandidateSelection(*row[:5], row._mapping["projection_digest"], row._mapping["signal_digest"],
                                    {column.key: row._mapping[column.key] for column in page.c})
                for row in rows if row[0] is not None]
    return selected, metadata


def finish_read(session, epoch, selected, chain="bsc"):
    if capture_epoch(session, chain) != epoch:
        raise StaleCandidateRead("candidate source epoch changed during read")
    for model, ids, statuses in (
        (RadarFeatureObservation, {row.projection.id for row in selected if row.projection}, ("VALID", "UNAVAILABLE")),
        (Signal, {row.signal.id for row in selected if row.signal}, ("VALID",)),
    ):
        if ids:
            valid = set(session.scalars(select(model.id).where(model.id.in_(ids), model.source_epoch == epoch,
                                                              model.snapshot_status.in_(statuses))
                                         .execution_options(include_invalid_snapshots=True)))
            if valid != ids:
                raise StaleCandidateRead("candidate research revision changed during read")
    if capture_epoch(session, chain) != epoch:
        raise StaleCandidateRead("candidate source epoch changed during read")
