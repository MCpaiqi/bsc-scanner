"""Candidate lifecycle and explainable ranking; never trade recommendations."""

from enum import StrEnum


class CandidateStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    PREQUALIFIED = "PREQUALIFIED"
    WATCH = "WATCH"
    INTERESTING = "INTERESTING"
    HIGH_SIGNAL = "HIGH_SIGNAL"
    HIGH_RISK = "HIGH_RISK"
    REJECTED = "REJECTED"
    STALE = "STALE"


S = CandidateStatus
ACTIVE = frozenset({S.PREQUALIFIED, S.WATCH, S.INTERESTING, S.HIGH_SIGNAL, S.HIGH_RISK})
TRANSITIONS = {
    S.DISCOVERED: {S.PREQUALIFIED, S.REJECTED, S.STALE},
    **{state: (ACTIVE - {state}) | {S.REJECTED, S.STALE} for state in ACTIVE},
    S.REJECTED: {S.DISCOVERED},
    S.STALE: {S.DISCOVERED, S.REJECTED},
}


def check_transition(previous: CandidateStatus, new: CandidateStatus) -> None:
    if not isinstance(new, CandidateStatus):
        raise TypeError("status transitions require CandidateStatus")
    if new not in TRANSITIONS[previous]:
        raise ValueError(f"illegal candidate transition: {previous} -> {new}")
