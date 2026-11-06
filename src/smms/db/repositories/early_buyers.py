"""Compatibility facades; publication appends immutable research revisions."""

from smms.research.publication import publish_snapshot


class EarlyBuyerRepository:
    def __init__(self, session):
        self.session = session

    def upsert(self, record):
        return publish_snapshot(self.session, record)


class WalletTokenPositionRepository(EarlyBuyerRepository):
    pass
