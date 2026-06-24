"""Helpers for recording source data snapshots."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from quantfore_research.models import SourceSnapshot, utc_now


def sha256_text(value: str) -> str:
    """Return a stable SHA-256 hex digest for text metadata or small payloads."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_text(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} is required")
    return cleaned


def _normalized_retrieved_at(value: Optional[datetime]) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def record_source_snapshot(
    session: Session,
    *,
    vendor: str,
    dataset: str,
    license_tag: str,
    source_hash: str,
    storage_uri: str,
    retrieved_at: Optional[datetime] = None,
) -> SourceSnapshot:
    """Insert a source snapshot record and flush it to the database."""

    snapshot = SourceSnapshot(
        vendor=_required_text("vendor", vendor),
        dataset=_required_text("dataset", dataset),
        retrieved_at=_normalized_retrieved_at(retrieved_at),
        license_tag=_required_text("license_tag", license_tag),
        source_hash=_required_text("source_hash", source_hash),
        storage_uri=_required_text("storage_uri", storage_uri),
    )
    session.add(snapshot)
    session.flush()
    return snapshot
