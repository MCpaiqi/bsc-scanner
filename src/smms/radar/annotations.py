"""Conditional manual writes; deletion keeps only a revision tombstone."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from smms.db.models import RadarAnnotation


class AnnotationConflict(ValueError):
    pass


def active_annotation(session, chain, token):
    return session.scalar(select(RadarAnnotation).where(RadarAnnotation.chain == chain,
        RadarAnnotation.token_address == token, RadarAnnotation.deleted.is_(False))
        .execution_options(populate_existing=True))


def save_annotation(session, *, chain, token, expected_revision, favorite=False, note=None, tags=()):
    values = {"favorite": favorite, "note": note, "tags": sorted({tag.strip() for tag in tags if tag.strip()}),
              "updated_at": datetime.now(UTC), "deleted": False}
    identity = (RadarAnnotation.chain == chain, RadarAnnotation.token_address == token)
    if expected_revision == 0:
        # Re-creation increments the tombstone revision: an old editor can never
        # overwrite a deleted/re-created annotation with the same revision.
        row = session.scalar(update(RadarAnnotation).where(*identity, RadarAnnotation.deleted.is_(True))
            .values(**values, revision=RadarAnnotation.revision + 1).returning(RadarAnnotation)
            .execution_options(synchronize_session=False, populate_existing=True))
        if row is not None:
            return row
        row = RadarAnnotation(chain=chain, token_address=token, **values)
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError as exc:
            raise AnnotationConflict("annotation was created by another writer") from exc
        return row
    row = session.scalar(update(RadarAnnotation).where(*identity, RadarAnnotation.deleted.is_(False),
        RadarAnnotation.revision == expected_revision).values(**values, revision=RadarAnnotation.revision + 1)
        .returning(RadarAnnotation).execution_options(synchronize_session=False, populate_existing=True))
    if row is None:
        raise AnnotationConflict("annotation revision changed or was deleted")
    return row


def delete_annotation(session, *, chain, token, expected_revision):
    revision = session.scalar(update(RadarAnnotation).where(RadarAnnotation.chain == chain,
        RadarAnnotation.token_address == token, RadarAnnotation.deleted.is_(False),
        RadarAnnotation.revision == expected_revision).values(deleted=True, revision=RadarAnnotation.revision + 1,
            favorite=False, note=None, tags=[], updated_at=datetime.now(UTC)).returning(RadarAnnotation.revision)
        .execution_options(synchronize_session=False))
    if revision is None:
        raise AnnotationConflict("annotation revision changed or was deleted")
    return revision
