"""Immutable, exact-decimal input manifests for reproducible Alpha revisions."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import select

from smms.alpha.policy import LABEL_VERSION, SCORE_VERSION, policy_id
from smms.alpha.vwap import VWAP_ARITHMETIC
from smms.db.models import AlphaCoverageReceipt
from smms.evaluation import utc

MANIFEST_VERSION = "alpha-inputs-v1"


def _json(value):
    if isinstance(value, Decimal):
        # normalize() obeys Decimal's (usually 28-digit) arithmetic context;
        # string formatting retains all 38 stored digits without rounding.
        value = format(value, "f")
        return value.rstrip("0").rstrip(".") if "." in value else value
    if isinstance(value, datetime):
        return utc(value).isoformat(timespec="microseconds")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def digest_manifest(payload: dict) -> str:
    body = {key: value for key, value in payload.items() if key not in {"digest", "captured_at"}}
    return hashlib.sha256(json.dumps(_json(body), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def input_manifest(session, *, pair, trades, proof, start, end, now, minimum_quote,
                   basis, entry=None, entry_digest=None, receipts=None):
    """Record what was actually read; future facts never enter entry manifests."""
    ids = set(proof.get("receipt_ids", []))
    rows = (session.scalars(select(AlphaCoverageReceipt).where(
        AlphaCoverageReceipt.id.in_(ids),
    ).order_by(AlphaCoverageReceipt.start_block, AlphaCoverageReceipt.id)).all() if receipts is None else
        sorted((row for row in receipts if row.id in ids), key=lambda row: (row.start_block, row.id)))
    trade_fields = ("id", "chain", "token_address", "pair_address", "trader_address", "side",
                    "timestamp", "block_number", "block_hash", "tx_hash", "event_index",
                    "quote_amount", "token_amount", "price_quote", "token_decimals", "quote_decimals",
                    "base_decimals", "transaction_from", "trader_resolution", "trader_confidence",
                    "source", "metadata_json")
    receipt_fields = ("id", "job_id", "generation", "start_block", "end_block", "start_hash",
                      "end_hash", "start_parent_hash", "start_at", "end_at", "raw_count", "decoded_count",
                      "query_json", "completed_at")
    payload = _json({
        "schema_version": MANIFEST_VERSION, "chain_id": 56,
        "score_version": SCORE_VERSION, "label_version": LABEL_VERSION, "vwap_arithmetic": VWAP_ARITHMETIC,
        "policy_id": policy_id(minimum_quote), "minimum_quote": minimum_quote,
        "price_basis": basis, "entry_vwap": entry, "entry_manifest_digest": entry_digest,
        "start": start, "end": end, "pair": {
            "id": pair.id, "address": pair.address, "token0": pair.token0_address,
            "token1": pair.token1_address, "quote_token": pair.quote_token,
            "creation_block": pair.creation_block, "creation_hash": pair.creation_block_hash,
            "created_at": pair.creation_timestamp, "factory": pair.factory_address,
        },
        "trade_ids": [row.id for row in trades],
        "trades": [{name: getattr(row, name) for name in trade_fields} for row in trades],
        "coverage": proof,
        "receipts": [{name: getattr(row, name) for name in receipt_fields} for row in rows],
    })
    return {**payload, "digest": digest_manifest(payload), "captured_at": utc(now).isoformat()}
