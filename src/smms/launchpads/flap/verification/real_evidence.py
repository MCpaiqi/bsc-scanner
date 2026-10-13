"""Read-only verification helpers for reproducible Flap chain evidence.

The helpers in this module never invent contract roles or event layouts.  They
only accept the event register in :mod:`events`, fetch the transaction/log/block
from a provider, and return the raw values needed to independently re-locate
the same evidence later.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from smms.chains.base import ChainLog, ChainProvider
from smms.chains.constants import BSC_CHAIN_ID
from smms.ingestion import DataUnavailable

from .._decoding import address_from_topic, data_words
from .events import KNOWN_FLAP_EVENTS, ON_CHAIN_VERIFIED_FLAP_EVENTS, VerifiedFlapEvent
from .evidence import EvidenceLevel


@dataclass(frozen=True)
class RealChainEvidence:
    """Minimal, replayable evidence for one Flap event log."""

    chain: str
    chain_id: int
    contract_address: str
    tx_hash: str
    block_number: int
    block_timestamp: datetime | None
    log_index: int
    topic0: str
    topics: tuple[str, ...]
    data: str
    event_name: str
    decoded_fields: Mapping[str, Any]
    raw_log: Mapping[str, Any] | None = None
    raw_receipt: Mapping[str, Any] | None = None
    raw_transaction: Mapping[str, Any] | None = None
    evidence_level: EvidenceLevel = EvidenceLevel.VERIFIED

    def as_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "chain_id": self.chain_id,
            "contract_address": self.contract_address,
            "tx_hash": self.tx_hash,
            "block_number": self.block_number,
            "block_timestamp": self.block_timestamp.isoformat()
            if self.block_timestamp is not None
            else None,
            "log_index": self.log_index,
            "topic0": self.topic0,
            "topics": list(self.topics),
            "data": self.data,
            "event_name": self.event_name,
            "decoded_fields": dict(self.decoded_fields),
            "raw_log": dict(self.raw_log) if self.raw_log is not None else None,
            "raw_receipt": dict(self.raw_receipt) if self.raw_receipt is not None else None,
            "raw_transaction": (
                dict(self.raw_transaction) if self.raw_transaction is not None else None
            ),
            "evidence_level": self.evidence_level.value,
        }


def _event(event: str | VerifiedFlapEvent) -> VerifiedFlapEvent:
    if isinstance(event, VerifiedFlapEvent):
        return event
    try:
        return KNOWN_FLAP_EVENTS[event]
    except KeyError as exc:
        raise DataUnavailable(f"unknown Flap event: {event}") from exc


def _word_values(log: ChainLog) -> list[int]:
    return [int(word, 16) for word in data_words(log.data)]


async def verify_contract(
    provider: ChainProvider,
    address: str,
    *,
    expected_chain_id: int = BSC_CHAIN_ID,
    block_number: int | None = None,
) -> dict[str, Any]:
    """Verify chain id and that ``address`` has deployed bytecode.

    Bytecode existence proves only that an address is a contract; it does not
    prove that it is the Flap factory, portal, router, or curve.
    """

    chain_id = getattr(provider, "get_chain_id", None)
    observed_chain_id = await chain_id() if callable(chain_id) else int(provider.chain_id)
    if observed_chain_id != expected_chain_id:
        raise DataUnavailable(
            f"chain id mismatch: expected {expected_chain_id}, received {observed_chain_id}"
        )
    get_code = getattr(provider, "get_code", None)
    if not callable(get_code):
        raise DataUnavailable("provider does not expose eth_getCode")
    bytecode = await get_code(address, block_number=block_number)
    if bytecode.lower() in {"0x", "0x0"}:
        raise DataUnavailable(f"no deployed bytecode at {address}")
    return {
        "chain_id": observed_chain_id,
        "address": address,
        "block_number": block_number,
        "bytecode": bytecode,
        # Bytecode existence is verified, but its Flap role remains unknown
        # until deployment/ABI evidence identifies the contract.
        "bytecode_evidence_level": EvidenceLevel.VERIFIED.value,
        "evidence_level": EvidenceLevel.UNKNOWN.value,
        "role": "UNKNOWN",
    }


def verify_event_topic(event: str | VerifiedFlapEvent, topic0: str) -> bool:
    """Return whether a log topic matches the registered event topic exactly."""

    return _event(event).matches_topic(topic0)


def verify_event_schema(
    event: str | VerifiedFlapEvent,
    *,
    topics: tuple[str, ...] | list[str],
    data: str,
    emitter: str | None = None,
    expected_emitter: str | None = None,
) -> dict[str, Any]:
    """Validate topic, emitter (when supplied), and ABI word selectors."""

    registered = _event(event)
    if not topics or not verify_event_topic(registered, topics[0]):
        raise DataUnavailable(f"{registered.name} topic0 does not match registered topic")
    if expected_emitter is None:
        raise DataUnavailable("expected event emitter is required for Flap verification")
    if (emitter or "").lower() != expected_emitter.lower():
        raise DataUnavailable("event emitter does not match the verified contract")
    words = _word_values(ChainLog(
        address=emitter or "0x0000000000000000000000000000000000000000",
        topics=tuple(topics), data=data, block_number=0, transaction_hash="0x0", log_index=0,
    ))
    expected_topic_count = 1 + len(registered.indexed_fields)
    if len(topics) != expected_topic_count:
        raise DataUnavailable(
            f"{registered.name} expected {expected_topic_count} topics, received {len(topics)}"
        )
    for field, index in registered.indexed_topic_indexes.items():
        if index >= len(topics):
            raise DataUnavailable(f"{registered.name} indexed field {field} topic is missing")
    for field, index in registered.data_indexes.items():
        if index >= len(words):
            raise DataUnavailable(f"{registered.name} field {field} is missing from event data")
    return {
        "event": registered.name,
        "signature": registered.signature,
        "topic0": registered.topic0,
        "indexed_fields": list(registered.indexed_fields),
        "indexed_topic_indexes": dict(registered.indexed_topic_indexes),
        "data_indexes": dict(registered.data_indexes),
        "data_word_count": len(words),
        "evidence_level": EvidenceLevel.INFERRED.value,
    }


def _decode_fields(event: VerifiedFlapEvent, log: ChainLog) -> dict[str, Any]:
    words = _word_values(log)
    decoded: dict[str, Any] = {"raw_words": words}
    for field, index in event.indexed_topic_indexes.items():
        body = log.topics[index][2:] if log.topics[index].lower().startswith("0x") else log.topics[index]
        try:
            value = int(body, 16)
        except ValueError as exc:
            raise DataUnavailable(f"Flap indexed field {field} topic is not hexadecimal") from exc
        decoded[field] = (
            address_from_topic(f"0x{value:064x}")
            if field in {"token", "trader", "creator"}
            else value
        )
    for field, index in event.data_indexes.items():
        value = words[index]
        if field in {"token", "trader", "creator"}:
            decoded[field] = address_from_topic(f"0x{value:064x}")
        else:
            decoded[field] = value
    return decoded


async def verify_real_log(
    provider: ChainProvider,
    log: ChainLog,
    *,
    event: str | VerifiedFlapEvent,
    expected_emitter: str | None = None,
    token_address: str | None = None,
    raw_receipt: Mapping[str, Any] | None = None,
    raw_transaction: Mapping[str, Any] | None = None,
    transaction_verified: bool = False,
) -> RealChainEvidence:
    """Verify one provider-normalized log and recover its block timestamp."""

    registered = _event(event)
    verify_event_schema(
        registered,
        topics=log.topics,
        data=log.data,
        emitter=log.address,
        expected_emitter=expected_emitter,
    )
    decoded = _decode_fields(registered, log)
    if token_address and str(decoded.get("token", "")).lower() != token_address.lower():
        raise DataUnavailable("event token does not match requested token")
    timestamp = log.timestamp
    if timestamp is None:
        get_timestamp = getattr(provider, "get_block_timestamp", None)
        if callable(get_timestamp):
            timestamp = await get_timestamp(log.block_number)
        else:
            block = await provider.get_block(log.block_number, include_transactions=False)
            timestamp = block.timestamp if block else None
    # The event register is documentation-derived until a real receipt has
    # been independently captured and marked ``VERIFIED_ON_CHAIN``. A block
    # timestamp alone never upgrades an unproven ABI/topic to VERIFIED.
    verified_fixture = ON_CHAIN_VERIFIED_FLAP_EVENTS.get(registered.name)
    evidence_level = (
        EvidenceLevel.VERIFIED
        if (
            timestamp is not None
            and transaction_verified
            and verified_fixture is not None
            and verified_fixture.fixture_status == "VERIFIED_ON_CHAIN"
        )
        else EvidenceLevel.UNKNOWN
    )
    return RealChainEvidence(
        chain=str(getattr(provider, "chain", "bsc")),
        chain_id=int(getattr(provider, "chain_id", BSC_CHAIN_ID)),
        contract_address=log.address,
        tx_hash=log.transaction_hash,
        block_number=log.block_number,
        block_timestamp=timestamp,
        log_index=log.log_index,
        topic0=log.topics[0],
        topics=tuple(log.topics),
        data=log.data,
        event_name=registered.name,
        decoded_fields=decoded,
        raw_log=log.raw or {
            "address": log.address,
            "topics": list(log.topics),
            "data": log.data,
            "blockNumber": hex(log.block_number),
            "transactionHash": log.transaction_hash,
            "logIndex": hex(log.log_index),
        },
        raw_receipt=raw_receipt,
        raw_transaction=raw_transaction,
        evidence_level=evidence_level,
    )


async def verify_real_transaction(
    provider: ChainProvider,
    tx_hash: str,
    *,
    event: str | VerifiedFlapEvent | None = None,
    expected_emitter: str | None = None,
    expected_emitters: Mapping[str, str] | None = None,
    token_address: str | None = None,
) -> list[RealChainEvidence]:
    """Fetch a transaction receipt and verify every matching Flap event log."""

    receipt = await provider.get_transaction_receipt(tx_hash)
    if receipt is None:
        raise DataUnavailable(f"transaction receipt is unavailable: {tx_hash}")
    transaction = await provider.get_transaction(tx_hash)
    if transaction is None:
        raise DataUnavailable(f"transaction is unavailable: {tx_hash}")
    if transaction.transaction_hash.lower() != tx_hash.lower():
        raise DataUnavailable("provider returned a transaction for a different hash")
    if receipt.transaction_hash.lower() != tx_hash.lower():
        raise DataUnavailable("receipt transaction hash does not match requested hash")
    if transaction.block_number is not None and transaction.block_number != receipt.block_number:
        raise DataUnavailable("transaction block does not match receipt block")
    if receipt.status is not None and receipt.status != 1:
        raise DataUnavailable("failed transaction cannot be used as Flap evidence")
    for log in receipt.logs:
        if log.transaction_hash.lower() != tx_hash.lower():
            raise DataUnavailable("receipt contains a log for a different transaction")
        if log.block_number != receipt.block_number:
            raise DataUnavailable("receipt contains a log for a different block")
    events = [_event(event)] if event is not None else list(KNOWN_FLAP_EVENTS.values())
    results: list[RealChainEvidence] = []
    for log in receipt.logs:
        matched = next(
            (
                candidate
                for candidate in events
                if log.topics and candidate.matches_topic(log.topics[0])
            ),
            None,
        )
        if matched is None:
            continue
        emitter = (
            expected_emitters.get(matched.name)
            if expected_emitters is not None
            else expected_emitter
        )
        if emitter is None:
            raise DataUnavailable(f"expected emitter is not configured for {matched.name}")
        results.append(await verify_real_log(
            provider,
            log,
            event=matched,
            expected_emitter=emitter,
            token_address=token_address,
            raw_receipt=receipt.raw or {
                "transactionHash": receipt.transaction_hash,
                "blockNumber": hex(receipt.block_number),
                "status": hex(receipt.status) if receipt.status is not None else None,
            },
            raw_transaction=transaction.raw or {
                "hash": transaction.transaction_hash,
                "blockNumber": (
                    hex(transaction.block_number)
                    if transaction.block_number is not None
                    else None
                ),
                "from": transaction.from_address,
                "to": transaction.to_address,
            },
            transaction_verified=True,
        ))
    if not results:
        raise DataUnavailable("transaction contains no registered Flap event logs")
    return results


__all__ = [
    "RealChainEvidence",
    "verify_contract",
    "verify_event_schema",
    "verify_event_topic",
    "verify_real_log",
    "verify_real_transaction",
]
