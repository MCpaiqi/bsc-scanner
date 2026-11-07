"""Canonical inputs and a final consistency fence for ad-hoc research reads.

Manifests travel with responses. They are not persisted snapshot revisions;
callers retaining a study must retain its response, including this evidence.
"""

from sqlalchemy import func, select

from smms.db.facts import canonical_fact_conditions
from smms.db.models import MarketEvent, Pair, TradeEvent, WalletSmartMoneySnapshot
from smms.evaluation import utc
from smms.research.lineage import manifest, row_values
from smms.research.state import capture_epoch

READ_POLICY = "canonical-research-read-v1"


class StaleResearchRead(ValueError):
    """A source epoch or selected dependency changed while calculating."""


class CanonicalResearchRead:
    def __init__(self, session, *, chain, as_of, parameters):
        self.session, self.chain, self.as_of = session, chain, utc(as_of)
        self.epoch = capture_epoch(session, chain)
        self.parameters = {**parameters, "chain": chain, "as_of": self.as_of,
                           "source_epoch": self.epoch, "input_policy": READ_POLICY}
        self.facts = {name: {} for name in ("trades", "pairs", "market_events", "wallet_snapshots")}
        self.queries = []

    def _record(self, name, rows):
        for row in rows:
            self.facts[name][row.id] = row_values(row)

    def _pairs(self, rows):
        addresses = {row.pair_address for row in rows if row.pair_address}
        if addresses:
            pairs = self.session.scalars(select(Pair).where(
                Pair.chain_id == 56, Pair.address.in_(addresses),
            ).execution_options(populate_existing=True)).all()
            self._record("pairs", pairs)

    def trades(self, *conditions, limit=20_000, purpose="trades"):
        rows = list(self.session.scalars(select(TradeEvent).where(
            TradeEvent.chain == self.chain, TradeEvent.timestamp <= self.as_of,
            *canonical_fact_conditions(TradeEvent), *conditions,
        ).order_by(TradeEvent.timestamp, TradeEvent.id).limit(limit + 1)
            .execution_options(populate_existing=True)))
        truncated = len(rows) > limit
        # Retain the sentinel too: it proves why the response is partial.
        self._record("trades", rows)
        self._pairs(rows)
        self.queries.append({"purpose": purpose, "limit": limit, "truncated": truncated,
                             "selected_ids": [row.id for row in rows[:limit]],
                             "sentinel_ids": [row.id for row in rows[limit:]]})
        return rows[:limit], truncated

    def neighborhood(self, address, *, limit=20_000):
        own, own_truncated = self.trades(TradeEvent.trader_address == address, limit=limit,
                                         purpose="wallet_token_discovery")
        tokens = sorted({row.token_address for row in own})
        if not tokens:
            return [], own_truncated
        rows, truncated = self.trades(TradeEvent.token_address.in_(tokens), limit=limit,
                                       purpose="wallet_neighborhood")
        return rows, own_truncated or truncated

    def wallet_snapshots(self, addresses, score_version):
        if not addresses:
            return []
        model = WalletSmartMoneySnapshot
        ranked = select(model.id, func.row_number().over(
            partition_by=model.wallet_address,
            order_by=(model.as_of.desc(), model.research_revision.desc(), model.id.desc()),
        ).label("rank")).where(model.chain == self.chain, model.wallet_address.in_(addresses),
                              model.score_version == score_version, model.as_of <= self.as_of).subquery()
        rows = list(self.session.scalars(select(model).where(model.id.in_(
            select(ranked.c.id).where(ranked.c.rank == 1),
        )).order_by(model.wallet_address).execution_options(populate_existing=True)))
        self._record("wallet_snapshots", rows)
        return rows

    def market_events(self, *, token, limit):
        query = select(MarketEvent).where(MarketEvent.chain_id == 56,
                                          MarketEvent.timestamp <= self.as_of,
                                          *canonical_fact_conditions(MarketEvent))
        if token:
            query = query.where(MarketEvent.token_address == token)
        rows = list(self.session.scalars(query.order_by(
            MarketEvent.block_number.desc().nulls_last(), MarketEvent.timestamp.desc(), MarketEvent.id.desc(),
        ).limit(limit + 1).execution_options(populate_existing=True)))
        self._record("market_events", rows)
        self._pairs(rows)
        self.queries.append({"purpose": "market_events", "limit": limit, "truncated": len(rows) > limit,
                             "selected_ids": [row.id for row in rows[:limit]],
                             "sentinel_ids": [row.id for row in rows[limit:]]})
        return rows[:limit], len(rows) > limit

    def finish(self):
        if capture_epoch(self.session, self.chain) != self.epoch:
            raise StaleResearchRead("canonical source epoch changed")
        expected = self.facts["wallet_snapshots"]
        if expected:
            rows = self.session.scalars(select(WalletSmartMoneySnapshot).where(
                WalletSmartMoneySnapshot.id.in_(expected),
            ).execution_options(populate_existing=True)).all()
            if {row.id: row_values(row) for row in rows} != expected:
                raise StaleResearchRead("wallet snapshot dependency changed")
        if capture_epoch(self.session, self.chain) != self.epoch:
            raise StaleResearchRead("canonical source epoch changed")
        return manifest(kind="ad_hoc_research_read", parameters=self.parameters,
                        facts={**{name: [rows[key] for key in sorted(rows)]
                                  for name, rows in self.facts.items()}, "queries": self.queries},
                        notes=["Inline evidence; retain this response for historical audit."])
