"""SQLAlchemy models for the Quantfore research warehouse."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    event,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class for all research database models."""


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class SourceSnapshot(TimestampMixin, Base):
    """Immutable-ish registry entry for a vendor/dataset retrieval.

    This is the first audit anchor for point-in-time ingestion: every future
    fact, feature, prediction, and validation result should be traceable back to
    one or more source snapshots.
    """

    __tablename__ = "source_snapshots"
    __table_args__ = (
        CheckConstraint("length(trim(vendor)) > 0", name="ck_source_snapshots_vendor_nonempty"),
        CheckConstraint("length(trim(dataset)) > 0", name="ck_source_snapshots_dataset_nonempty"),
        CheckConstraint(
            "length(trim(license_tag)) > 0",
            name="ck_source_snapshots_license_tag_nonempty",
        ),
        CheckConstraint("length(trim(hash)) > 0", name="ck_source_snapshots_hash_nonempty"),
        CheckConstraint(
            "length(trim(storage_uri)) > 0",
            name="ck_source_snapshots_storage_uri_nonempty",
        ),
        UniqueConstraint("storage_uri", name="uq_source_snapshots_storage_uri"),
        Index(
            "ix_source_snapshots_vendor_dataset_retrieved_at",
            "vendor",
            "dataset",
            "retrieved_at",
        ),
        Index("ix_source_snapshots_retrieved_at", "retrieved_at"),
    )

    snapshot_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=new_id,
    )
    vendor: Mapped[str] = mapped_column(String(100), nullable=False)
    dataset: Mapped[str] = mapped_column(String(160), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    license_tag: Mapped[str] = mapped_column(String(100), nullable=False)
    source_hash: Mapped[str] = mapped_column("hash", String(128), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)

    prices: Mapped[list["Price"]] = relationship(back_populates="source_snapshot")
    filings: Mapped[list["Filing"]] = relationship(back_populates="source_snapshot")
    fundamentals: Mapped[list["Fundamental"]] = relationship(
        back_populates="source_snapshot"
    )
    macro_observations: Mapped[list["MacroSeries"]] = relationship(
        back_populates="source_snapshot"
    )
    features: Mapped[list["Feature"]] = relationship(back_populates="source_snapshot")
    feature_sets: Mapped[list["FeatureSet"]] = relationship(back_populates="source_snapshot")
    security_identifiers: Mapped[list["SecurityIdentifier"]] = relationship(
        back_populates="source_snapshot"
    )
    ticker_aliases: Mapped[list["TickerAlias"]] = relationship(
        back_populates="source_snapshot"
    )
    universe_definitions: Mapped[list["UniverseDefinition"]] = relationship(
        back_populates="source_snapshot"
    )
    universe_memberships: Mapped[list["UniverseMembership"]] = relationship(
        back_populates="source_snapshot"
    )
    corporate_actions: Mapped[list["CorporateAction"]] = relationship(
        back_populates="source_snapshot"
    )
    delisting_events: Mapped[list["DelistingEvent"]] = relationship(
        back_populates="source_snapshot"
    )
    security_classifications: Mapped[list["SecurityClassification"]] = relationship(
        back_populates="source_snapshot"
    )

    def __repr__(self) -> str:
        return (
            "SourceSnapshot("
            f"snapshot_id={self.snapshot_id!r}, "
            f"vendor={self.vendor!r}, "
            f"dataset={self.dataset!r}, "
            f"source_hash={self.source_hash!r}"
            ")"
        )


class Security(TimestampMixin, Base):
    """Company or asset tracked by Quantfore research."""

    __tablename__ = "securities"
    __table_args__ = (
        CheckConstraint("length(trim(ticker)) > 0", name="ck_securities_ticker_nonempty"),
        CheckConstraint("length(trim(name)) > 0", name="ck_securities_name_nonempty"),
        Index("ix_securities_ticker", "ticker"),
        Index("ix_securities_sector", "sector"),
    )

    security_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    exchange: Mapped[Optional[str]] = mapped_column(String(64))
    sector: Mapped[Optional[str]] = mapped_column(String(128))
    industry: Mapped[Optional[str]] = mapped_column(String(160))
    cik: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    active_from: Mapped[Optional[date]] = mapped_column(Date)
    active_to: Mapped[Optional[date]] = mapped_column(Date)

    prices: Mapped[list["Price"]] = relationship(back_populates="security")
    filings: Mapped[list["Filing"]] = relationship(back_populates="security")
    fundamentals: Mapped[list["Fundamental"]] = relationship(back_populates="security")
    features: Mapped[list["Feature"]] = relationship(back_populates="security")
    predictions: Mapped[list["ModelPrediction"]] = relationship(back_populates="security")
    identifiers: Mapped[list["SecurityIdentifier"]] = relationship(
        back_populates="security"
    )
    ticker_aliases: Mapped[list["TickerAlias"]] = relationship(
        back_populates="security"
    )
    universe_memberships: Mapped[list["UniverseMembership"]] = relationship(
        back_populates="security"
    )
    corporate_actions: Mapped[list["CorporateAction"]] = relationship(
        back_populates="security", foreign_keys="CorporateAction.security_id"
    )
    delisting_events: Mapped[list["DelistingEvent"]] = relationship(
        back_populates="security", foreign_keys="DelistingEvent.security_id"
    )
    classifications: Mapped[list["SecurityClassification"]] = relationship(
        back_populates="security"
    )


class SecurityClassification(CreatedAtMixin, Base):
    """Append-only point-in-time sector and industry classification."""

    __tablename__ = "security_classifications"
    __table_args__ = (
        CheckConstraint(
            "length(trim(sector)) > 0",
            name="ck_security_classifications_sector_nonempty",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_security_classifications_effective_dates",
        ),
        CheckConstraint(
            "length(trim(source_hash)) > 0",
            name="ck_security_classifications_source_hash_nonempty",
        ),
        UniqueConstraint(
            "security_id",
            "effective_from",
            "source_snapshot_id",
            name="uq_security_classifications_security_effective_source",
        ),
        Index(
            "ix_security_classifications_asof",
            "security_id",
            "effective_from",
            "effective_to",
            "model_available_at",
        ),
    )

    classification_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    sector: Mapped[str] = mapped_column(String(128), nullable=False)
    industry: Mapped[Optional[str]] = mapped_column(String(160))
    classification_system: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date)
    model_available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    security: Mapped["Security"] = relationship(back_populates="classifications")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="security_classifications"
    )


def _reject_security_classification_change(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("security classifications are append-only")


event.listen(
    SecurityClassification, "before_update", _reject_security_classification_change
)
event.listen(
    SecurityClassification, "before_delete", _reject_security_classification_change
)


class SecurityIdentifier(CreatedAtMixin, Base):
    """Dated vendor or regulatory identifier for one permanent security."""

    __tablename__ = "security_identifiers"
    __table_args__ = (
        CheckConstraint(
            "length(trim(identifier_type)) > 0",
            name="ck_security_identifiers_type_nonempty",
        ),
        CheckConstraint(
            "length(trim(identifier_value)) > 0",
            name="ck_security_identifiers_value_nonempty",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_security_identifiers_valid_dates",
        ),
        CheckConstraint(
            "length(trim(source_hash)) > 0",
            name="ck_security_identifiers_source_hash_nonempty",
        ),
        UniqueConstraint(
            "security_id",
            "identifier_type",
            "identifier_value",
            "valid_from",
            "source_snapshot_id",
            name="uq_security_identifiers_identity_period_source",
        ),
        Index(
            "ix_security_identifiers_lookup",
            "identifier_type",
            "identifier_value",
            "valid_from",
            "valid_to",
        ),
    )

    identifier_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    identifier_type: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(160), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    is_permanent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    security: Mapped["Security"] = relationship(back_populates="identifiers")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="security_identifiers"
    )


class TickerAlias(CreatedAtMixin, Base):
    """A ticker's effective period without treating a rename as a new security."""

    __tablename__ = "ticker_aliases"
    __table_args__ = (
        CheckConstraint("length(trim(ticker)) > 0", name="ck_ticker_aliases_nonempty"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_ticker_aliases_effective_dates",
        ),
        CheckConstraint(
            "length(trim(source_hash)) > 0",
            name="ck_ticker_aliases_source_hash_nonempty",
        ),
        UniqueConstraint(
            "security_id",
            "ticker",
            "effective_from",
            "source_snapshot_id",
            name="uq_ticker_aliases_security_ticker_period_source",
        ),
        Index(
            "ix_ticker_aliases_lookup",
            "ticker",
            "effective_from",
            "effective_to",
        ),
    )

    ticker_alias_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[Optional[str]] = mapped_column(String(64))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date)
    announced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    security: Mapped["Security"] = relationship(back_populates="ticker_aliases")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="ticker_aliases"
    )


class UniverseDefinition(CreatedAtMixin, Base):
    """Versioned definition of a historical research universe."""

    __tablename__ = "universe_definitions"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_universe_definitions_name_nonempty"),
        CheckConstraint(
            "length(trim(version)) > 0",
            name="ck_universe_definitions_version_nonempty",
        ),
        CheckConstraint(
            "window_end >= window_start",
            name="ck_universe_definitions_window_dates",
        ),
        CheckConstraint(
            "length(trim(source_hash)) > 0",
            name="ck_universe_definitions_source_hash_nonempty",
        ),
        UniqueConstraint("name", "version", name="uq_universe_definitions_name_version"),
        Index("ix_universe_definitions_window", "window_start", "window_end"),
    )

    universe_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    benchmark_security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    benchmark_excluded_from_rankings: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    audit_contract_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    benchmark_security: Mapped["Security"] = relationship(
        foreign_keys=[benchmark_security_id]
    )
    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="universe_definitions"
    )
    memberships: Mapped[list["UniverseMembership"]] = relationship(
        back_populates="universe"
    )


class UniverseMembership(CreatedAtMixin, Base):
    """Inclusive effective period for a security in a versioned universe."""

    __tablename__ = "universe_memberships"
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_universe_memberships_effective_dates",
        ),
        CheckConstraint(
            "length(trim(source_hash)) > 0",
            name="ck_universe_memberships_source_hash_nonempty",
        ),
        UniqueConstraint(
            "universe_id",
            "security_id",
            "effective_from",
            "source_snapshot_id",
            name="uq_universe_memberships_security_period_source",
        ),
        Index(
            "ix_universe_memberships_asof",
            "universe_id",
            "effective_from",
            "effective_to",
        ),
        Index("ix_universe_memberships_security", "security_id"),
    )

    membership_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    universe_id: Mapped[str] = mapped_column(
        ForeignKey("universe_definitions.universe_id"), nullable=False
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date)
    announced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    universe: Mapped["UniverseDefinition"] = relationship(back_populates="memberships")
    security: Mapped["Security"] = relationship(back_populates="universe_memberships")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="universe_memberships"
    )


class CorporateAction(CreatedAtMixin, Base):
    """Dated split, dividend, merger, rename, or other corporate action."""

    __tablename__ = "corporate_actions"
    __table_args__ = (
        CheckConstraint(
            "length(trim(action_type)) > 0",
            name="ck_corporate_actions_type_nonempty",
        ),
        CheckConstraint(
            "length(trim(source_hash)) > 0",
            name="ck_corporate_actions_source_hash_nonempty",
        ),
        CheckConstraint(
            "cash_amount IS NULL OR cash_amount >= 0",
            name="ck_corporate_actions_cash_amount_nonnegative",
        ),
        CheckConstraint(
            "ratio_from IS NULL OR ratio_from > 0",
            name="ck_corporate_actions_ratio_from_positive",
        ),
        CheckConstraint(
            "ratio_to IS NULL OR ratio_to > 0",
            name="ck_corporate_actions_ratio_to_positive",
        ),
        UniqueConstraint(
            "security_id",
            "action_type",
            "effective_date",
            "source_snapshot_id",
            name="uq_corporate_actions_security_type_date_source",
        ),
        Index("ix_corporate_actions_security_date", "security_id", "effective_date"),
    )

    corporate_action_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    announced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cash_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8))
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    ratio_from: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8))
    ratio_to: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8))
    related_security_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("securities.security_id")
    )
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    security: Mapped["Security"] = relationship(
        back_populates="corporate_actions", foreign_keys=[security_id]
    )
    related_security: Mapped[Optional["Security"]] = relationship(
        foreign_keys=[related_security_id]
    )
    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="corporate_actions"
    )


class DelistingEvent(CreatedAtMixin, Base):
    """A delisting and its terminal return, retained after the security disappears."""

    __tablename__ = "delisting_events"
    __table_args__ = (
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_delisting_events_reason_nonempty",
        ),
        CheckConstraint(
            "length(trim(source_hash)) > 0",
            name="ck_delisting_events_source_hash_nonempty",
        ),
        UniqueConstraint(
            "security_id",
            "delisting_date",
            "source_snapshot_id",
            name="uq_delisting_events_security_date_source",
        ),
        Index("ix_delisting_events_security_date", "security_id", "delisting_date"),
    )

    delisting_event_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    delisting_date: Mapped[date] = mapped_column(Date, nullable=False)
    announced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delisting_return: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 8))
    return_available_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    successor_security_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("securities.security_id")
    )
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    security: Mapped["Security"] = relationship(
        back_populates="delisting_events", foreign_keys=[security_id]
    )
    successor_security: Mapped[Optional["Security"]] = relationship(
        foreign_keys=[successor_security_id]
    )
    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="delisting_events"
    )


class Price(TimestampMixin, Base):
    """Daily OHLCV price observation tied to a source snapshot."""

    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "date",
            "source_snapshot_id",
            name="uq_prices_security_date_source_snapshot",
        ),
        Index("ix_prices_security_date", "security_id", "date"),
    )

    price_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"),
        nullable=False,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    high: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    low: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    close: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    adj_open: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    adj_high: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    adj_low: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    adj_close: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    adj_volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 6))
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )

    security: Mapped["Security"] = relationship(back_populates="prices")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(back_populates="prices")


class Filing(TimestampMixin, Base):
    """SEC filing metadata and raw document location."""

    __tablename__ = "filings"
    __table_args__ = (
        CheckConstraint("length(trim(form_type)) > 0", name="ck_filings_form_type_nonempty"),
        CheckConstraint(
            "length(trim(accession_no)) > 0",
            name="ck_filings_accession_no_nonempty",
        ),
        CheckConstraint("length(trim(storage_uri)) > 0", name="ck_filings_storage_uri_nonempty"),
        CheckConstraint("length(trim(source_url)) > 0", name="ck_filings_source_url_nonempty"),
        UniqueConstraint("accession_no", name="uq_filings_accession_no"),
        UniqueConstraint("storage_uri", name="uq_filings_storage_uri"),
        Index("ix_filings_security_filed_at", "security_id", "filed_at"),
    )

    filing_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"),
        nullable=False,
    )
    form_type: Mapped[str] = mapped_column(String(32), nullable=False)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    accession_no: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )

    security: Mapped["Security"] = relationship(back_populates="filings")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(back_populates="filings")


class Fundamental(TimestampMixin, Base):
    """Append-only point-in-time company fact and its exact source revision.

    ``metric``, ``period_end``, ``available_at`` and ``accession_no`` are kept
    as compatibility fields for the pre-Sprint-8 prototype.  New ingestion
    must populate the explicit point-in-time fields; the init hook below keeps
    both representations identical while old callers are migrated.
    """

    __tablename__ = "fundamentals"
    __table_args__ = (
        CheckConstraint(
            "length(trim(metric)) > 0",
            name="ck_fundamentals_metric_nonempty",
        ),
        CheckConstraint(
            "length(trim(concept)) > 0",
            name="ck_fundamentals_concept_nonempty",
        ),
        CheckConstraint(
            "length(trim(standardized_concept)) > 0",
            name="ck_fundamentals_standardized_concept_nonempty",
        ),
        CheckConstraint("length(trim(unit)) > 0", name="ck_fundamentals_unit_nonempty"),
        CheckConstraint(
            "length(trim(filing_accession)) > 0",
            name="ck_fundamentals_filing_accession_nonempty",
        ),
        CheckConstraint(
            "length(trim(form_type)) > 0",
            name="ck_fundamentals_form_type_nonempty",
        ),
        CheckConstraint(
            "length(trim(source_hash)) > 0",
            name="ck_fundamentals_source_hash_nonempty",
        ),
        CheckConstraint(
            "period_type IN ('ANNUAL', 'QUARTERLY', 'TTM')",
            name="ck_fundamentals_period_type",
        ),
        CheckConstraint(
            "revision_version >= 1",
            name="ck_fundamentals_revision_positive",
        ),
        CheckConstraint(
            "fiscal_quarter IS NULL OR fiscal_quarter BETWEEN 1 AND 4",
            name="ck_fundamentals_fiscal_quarter",
        ),
        CheckConstraint(
            "period_type != 'QUARTERLY' OR fiscal_quarter IS NOT NULL",
            name="ck_fundamentals_quarterly_has_quarter",
        ),
        CheckConstraint(
            "period_type != 'ANNUAL' OR fiscal_quarter IS NULL",
            name="ck_fundamentals_annual_has_no_quarter",
        ),
        CheckConstraint(
            "model_available_at >= filed_at",
            name="ck_fundamentals_model_after_filing",
        ),
        CheckConstraint(
            "accepted_at IS NULL OR model_available_at >= accepted_at",
            name="ck_fundamentals_model_after_acceptance",
        ),
        CheckConstraint(
            "model_available_at >= vendor_available_at",
            name="ck_fundamentals_model_after_vendor",
        ),
        CheckConstraint(
            "public_release_at IS NULL OR model_available_at >= public_release_at",
            name="ck_fundamentals_model_after_public_release",
        ),
        UniqueConstraint(
            "security_id",
            "fiscal_period_end",
            "period_type",
            "concept",
            "unit",
            "revision_version",
            "filing_accession",
            "source_snapshot_id",
            name="uq_fundamentals_fact_revision_source",
        ),
        Index("ix_fundamentals_security_metric_period", "security_id", "metric", "period_end"),
        Index(
            "ix_fundamentals_security_concept_period",
            "security_id",
            "standardized_concept",
            "fiscal_period_end",
        ),
        Index(
            "ix_fundamentals_as_known",
            "security_id",
            "model_available_at",
            "revision_version",
        ),
        Index("ix_fundamentals_source_snapshot", "source_snapshot_id"),
    )

    fundamental_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=new_id,
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"),
        nullable=False,
    )
    fiscal_period: Mapped[Optional[str]] = mapped_column(String(32))
    metric: Mapped[str] = mapped_column(String(160), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(80), nullable=False)
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    form_type: Mapped[str] = mapped_column(String(32), nullable=False)
    accession_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    fiscal_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(nullable=False)
    fiscal_quarter: Mapped[Optional[int]] = mapped_column()
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    filing_accession: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    public_release_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    vendor_available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    model_available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revision_version: Mapped[int] = mapped_column(nullable=False, default=1)
    concept: Mapped[str] = mapped_column(String(255), nullable=False)
    standardized_concept: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    security: Mapped["Security"] = relationship(back_populates="fundamentals")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="fundamentals"
    )


def _infer_fundamental_period_type(form_type: Optional[str]) -> str:
    normalized = (form_type or "").upper()
    if normalized.startswith("10-K") or normalized in {"20-F", "40-F"}:
        return "ANNUAL"
    return "QUARTERLY"


def _fundamental_init(target, args, kwargs) -> None:
    """Bridge legacy constructor names while callers adopt the v1 contract."""

    del target, args
    concept = kwargs.get("concept") or kwargs.get("metric")
    if concept is not None:
        kwargs.setdefault("concept", concept)
        kwargs.setdefault("metric", concept)
        kwargs.setdefault("standardized_concept", concept)

    period_end = kwargs.get("fiscal_period_end") or kwargs.get("period_end")
    if period_end is not None:
        kwargs.setdefault("fiscal_period_end", period_end)
        kwargs.setdefault("period_end", period_end)
        kwargs.setdefault("fiscal_year", period_end.year)

    accession = kwargs.get("filing_accession") or kwargs.get("accession_no")
    if accession is not None:
        kwargs.setdefault("filing_accession", accession)
        kwargs.setdefault("accession_no", accession)

    model_available_at = kwargs.get("model_available_at") or kwargs.get("available_at")
    if model_available_at is not None:
        kwargs.setdefault("model_available_at", model_available_at)
        kwargs.setdefault("available_at", model_available_at)
        kwargs.setdefault("vendor_available_at", model_available_at)

    period_type = kwargs.setdefault(
        "period_type", _infer_fundamental_period_type(kwargs.get("form_type"))
    )
    fiscal_period = str(kwargs.get("fiscal_period") or "").upper()
    if period_type == "QUARTERLY" and kwargs.get("fiscal_quarter") is None:
        for quarter in range(1, 5):
            if f"Q{quarter}" in fiscal_period:
                kwargs["fiscal_quarter"] = quarter
                break
        else:
            # Legacy rows did not record a numeric quarter.  Keep them valid
            # without pretending the textual fiscal label was more precise.
            kwargs["period_type"] = "ANNUAL"


def _prepare_fundamental_insert(mapper, connection, target) -> None:
    """Require the copied source hash to match the immutable snapshot."""

    del mapper
    mirrors = (
        ("metric", target.metric, "concept", target.concept),
        ("period_end", target.period_end, "fiscal_period_end", target.fiscal_period_end),
        ("accession_no", target.accession_no, "filing_accession", target.filing_accession),
        ("available_at", target.available_at, "model_available_at", target.model_available_at),
    )
    for legacy_name, legacy_value, canonical_name, canonical_value in mirrors:
        if legacy_value != canonical_value:
            raise ValueError(
                f"fundamental {legacy_name} must mirror {canonical_name}"
            )
    snapshot_hash = connection.execute(
        select(SourceSnapshot.source_hash).where(
            SourceSnapshot.snapshot_id == target.source_snapshot_id
        )
    ).scalar_one_or_none()
    if snapshot_hash is None:
        raise ValueError("fundamental source_snapshot_id does not exist")
    if target.source_hash is None:
        target.source_hash = snapshot_hash
    elif target.source_hash != snapshot_hash:
        raise ValueError("fundamental source_hash does not match source snapshot")


def _reject_fundamental_update(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("fundamentals are append-only and cannot be updated")


def _reject_fundamental_delete(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("fundamentals are append-only and cannot be deleted")


event.listen(Fundamental, "init", _fundamental_init, raw=False)
event.listen(Fundamental, "before_insert", _prepare_fundamental_insert)
event.listen(Fundamental, "before_update", _reject_fundamental_update)
event.listen(Fundamental, "before_delete", _reject_fundamental_delete)


class MacroSeries(TimestampMixin, Base):
    """Macro observation such as rates, inflation, oil, VIX, or unemployment."""

    __tablename__ = "macro_series"
    __table_args__ = (
        CheckConstraint("length(trim(series_id)) > 0", name="ck_macro_series_id_nonempty"),
        UniqueConstraint(
            "series_id",
            "observation_date",
            "source_snapshot_id",
            name="uq_macro_series_observation_source_snapshot",
        ),
        Index("ix_macro_series_id_observation_date", "series_id", "observation_date"),
    )

    macro_observation_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=new_id,
    )
    series_id: Mapped[str] = mapped_column(String(80), nullable=False)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )

    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="macro_observations"
    )


class FeatureSet(CreatedAtMixin, Base):
    """Auditable registry entry for one feature calculation run."""

    __tablename__ = "feature_sets"
    __table_args__ = (
        CheckConstraint(
            "length(trim(feature_set_id)) > 0",
            name="ck_feature_sets_id_nonempty",
        ),
        CheckConstraint("length(trim(name)) > 0", name="ck_feature_sets_name_nonempty"),
        CheckConstraint(
            "length(trim(version)) > 0",
            name="ck_feature_sets_version_nonempty",
        ),
        Index("ix_feature_sets_name_version_asof_date", "name", "version", "asof_date"),
        Index("ix_feature_sets_source_snapshot_id", "source_snapshot_id"),
    )

    feature_set_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    asof_date: Mapped[date] = mapped_column(Date, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )
    code_commit: Mapped[Optional[str]] = mapped_column(String(64))

    source_snapshot: Mapped["SourceSnapshot"] = relationship(back_populates="feature_sets")
    features: Mapped[list["Feature"]] = relationship(back_populates="feature_set")
    predictions: Mapped[list["ModelPrediction"]] = relationship(back_populates="feature_set")


class Feature(TimestampMixin, Base):
    """Calculated point-in-time model input for a security."""

    __tablename__ = "features"
    __table_args__ = (
        CheckConstraint("length(trim(feature_name)) > 0", name="ck_features_name_nonempty"),
        CheckConstraint("length(trim(version)) > 0", name="ck_features_version_nonempty"),
        CheckConstraint(
            "length(trim(feature_set_id)) > 0",
            name="ck_features_feature_set_id_nonempty",
        ),
        CheckConstraint(
            "length(trim(source_hash)) > 0",
            name="ck_features_source_hash_nonempty",
        ),
        CheckConstraint(
            "length(trim(family)) > 0",
            name="ck_features_family_nonempty",
        ),
        CheckConstraint(
            "length(trim(formula_version)) > 0",
            name="ck_features_formula_version_nonempty",
        ),
        CheckConstraint(
            "direction IN ('HIGHER', 'LOWER')",
            name="ck_features_direction",
        ),
        CheckConstraint(
            "applicability_status IN ('APPLICABLE', 'MISSING', 'NOT_APPLICABLE')",
            name="ck_features_applicability_status",
        ),
        CheckConstraint(
            "applicability_status != 'APPLICABLE' OR value IS NOT NULL",
            name="ck_features_applicable_has_value",
        ),
        CheckConstraint(
            "applicability_status = 'APPLICABLE' OR value IS NULL",
            name="ck_features_unavailable_has_no_value",
        ),
        CheckConstraint(
            "applicability_status = 'APPLICABLE' OR "
            "(missing_reason IS NOT NULL AND length(trim(missing_reason)) > 0)",
            name="ck_features_unavailable_has_reason",
        ),
        CheckConstraint(
            "applicability_status != 'APPLICABLE' OR raw_value IS NOT NULL",
            name="ck_features_applicable_has_raw_value",
        ),
        UniqueConstraint(
            "feature_set_id",
            "security_id",
            "asof_date",
            "feature_name",
            "version",
            name="uq_features_set_security_asof_name_version",
        ),
        Index("ix_features_security_asof_date", "security_id", "asof_date"),
        Index("ix_features_available_at", "available_at"),
        Index("ix_features_source_snapshot_id", "source_snapshot_id"),
        Index("ix_features_family_status", "family", "applicability_status"),
    )

    feature_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    feature_set_id: Mapped[str] = mapped_column(
        ForeignKey("feature_sets.feature_set_id"),
        nullable=False,
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"),
        nullable=False,
    )
    asof_date: Mapped[date] = mapped_column(Date, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_name: Mapped[str] = mapped_column(String(160), nullable=False)
    value: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 10))
    raw_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 12))
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(64), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    applicability_status: Mapped[str] = mapped_column(String(24), nullable=False)
    missing_reason: Mapped[Optional[str]] = mapped_column(String(80))
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    security: Mapped["Security"] = relationship(back_populates="features")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(back_populates="features")
    feature_set: Mapped["FeatureSet"] = relationship(back_populates="features")


def _feature_init(target, args, kwargs) -> None:
    """Supply Sprint 8 metadata for legacy feature constructors."""

    del target, args
    value = kwargs.get("value")
    version = kwargs.get("version") or "legacy"
    kwargs.setdefault("raw_value", value)
    kwargs.setdefault("family", "legacy")
    kwargs.setdefault("formula_version", version)
    kwargs.setdefault("formula", f"legacy:{kwargs.get('feature_name', 'feature')}")
    kwargs.setdefault("direction", "HIGHER")
    kwargs.setdefault(
        "applicability_status", "APPLICABLE" if value is not None else "MISSING"
    )
    if value is None:
        kwargs.setdefault("missing_reason", "SOURCE_MISSING")
    kwargs.setdefault("inputs_json", {})


event.listen(Feature, "init", _feature_init, raw=False)


def _reject_feature_update(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("features are append-only and cannot be updated")


def _reject_feature_delete(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("features are append-only and cannot be deleted")


event.listen(Feature, "before_update", _reject_feature_update)
event.listen(Feature, "before_delete", _reject_feature_delete)
event.listen(FeatureSet, "before_update", _reject_feature_update)
event.listen(FeatureSet, "before_delete", _reject_feature_delete)


class NormalizationRun(CreatedAtMixin, Base):
    """Frozen cohort-level cross-sectional normalization run."""

    __tablename__ = "normalization_runs"
    __table_args__ = (
        CheckConstraint(
            "length(trim(version)) > 0", name="ck_normalization_runs_version_nonempty"
        ),
        CheckConstraint(
            "length(trim(input_hash)) > 0",
            name="ck_normalization_runs_input_hash_nonempty",
        ),
        UniqueConstraint(
            "universe_id",
            "asof_date",
            "version",
            name="uq_normalization_runs_universe_asof_version",
        ),
        Index("ix_normalization_runs_asof", "universe_id", "asof_date"),
    )

    normalization_run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    universe_id: Mapped[str] = mapped_column(
        ForeignKey("universe_definitions.universe_id"), nullable=False
    )
    asof_date: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_feature_set_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    code_commit: Mapped[Optional[str]] = mapped_column(String(64))


class NormalizedFeature(CreatedAtMixin, Base):
    """One raw component's cross-sectional values and score contribution."""

    __tablename__ = "normalized_features"
    __table_args__ = (
        CheckConstraint(
            "normalization_scope IN ('SECTOR', 'UNIVERSE', 'NONE')",
            name="ck_normalized_features_scope",
        ),
        CheckConstraint(
            "applicability_status IN ('APPLICABLE', 'MISSING', 'NOT_APPLICABLE')",
            name="ck_normalized_features_status",
        ),
        CheckConstraint(
            "group_count >= 0", name="ck_normalized_features_group_count"
        ),
        UniqueConstraint(
            "normalization_run_id",
            "feature_id",
            name="uq_normalized_features_run_feature",
        ),
        Index(
            "ix_normalized_features_run_security",
            "normalization_run_id",
            "security_id",
        ),
        Index(
            "ix_normalized_features_run_name",
            "normalization_run_id",
            "feature_name",
        ),
    )

    normalized_feature_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    normalization_run_id: Mapped[str] = mapped_column(
        ForeignKey("normalization_runs.normalization_run_id"), nullable=False
    )
    feature_id: Mapped[str] = mapped_column(
        ForeignKey("features.feature_id"), nullable=False
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    feature_name: Mapped[str] = mapped_column(String(160), nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 12))
    winsorized_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 12))
    standardized_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 12))
    directed_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 12))
    contribution: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 12))
    applicability_status: Mapped[str] = mapped_column(String(24), nullable=False)
    missing_reason: Mapped[Optional[str]] = mapped_column(String(80))
    normalization_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    group_label: Mapped[Optional[str]] = mapped_column(String(128))
    group_count: Mapped[int] = mapped_column(nullable=False)
    group_mean: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 12))
    group_std: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 12))
    winsor_lower: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 12))
    winsor_upper: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 12))


class MultiFactorScore(CreatedAtMixin, Base):
    """Eligibility, family scores, missingness and final 0-100 cohort score."""

    __tablename__ = "multifactor_scores"
    __table_args__ = (
        CheckConstraint(
            "final_score IS NULL OR (final_score >= 0 AND final_score <= 100)",
            name="ck_multifactor_scores_final_range",
        ),
        CheckConstraint(
            "component_coverage >= 0 AND component_coverage <= 1",
            name="ck_multifactor_scores_coverage_range",
        ),
        CheckConstraint(
            "available_family_count >= 0 AND available_family_count <= 5",
            name="ck_multifactor_scores_family_count",
        ),
        UniqueConstraint(
            "normalization_run_id",
            "security_id",
            name="uq_multifactor_scores_run_security",
        ),
        Index(
            "ix_multifactor_scores_run_score",
            "normalization_run_id",
            "final_score",
        ),
    )

    multifactor_score_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    normalization_run_id: Mapped[str] = mapped_column(
        ForeignKey("normalization_runs.normalization_run_id"), nullable=False
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    asof_date: Mapped[date] = mapped_column(Date, nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    final_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    composite_z: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 12))
    applicable_component_count: Mapped[int] = mapped_column(nullable=False)
    valid_component_count: Mapped[int] = mapped_column(nullable=False)
    component_coverage: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    available_family_count: Mapped[int] = mapped_column(nullable=False)
    family_z_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    family_scores_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    family_available_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    renormalized_weights_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    missingness_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class MultiFactorPredictionLink(CreatedAtMixin, Base):
    """Exact immutable prediction produced from one stored multi-factor score."""

    __tablename__ = "multifactor_prediction_links"
    __table_args__ = (
        UniqueConstraint(
            "multifactor_score_id",
            "horizon",
            name="uq_multifactor_prediction_links_score_horizon",
        ),
        UniqueConstraint(
            "prediction_id", name="uq_multifactor_prediction_links_prediction"
        ),
        Index(
            "ix_multifactor_prediction_links_score", "multifactor_score_id"
        ),
    )

    link_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    multifactor_score_id: Mapped[str] = mapped_column(
        ForeignKey("multifactor_scores.multifactor_score_id"), nullable=False
    )
    prediction_id: Mapped[str] = mapped_column(
        ForeignKey("model_predictions.prediction_id"), nullable=False
    )
    horizon: Mapped[str] = mapped_column(String(32), nullable=False)


def _reject_normalization_artifact_change(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("normalization artifacts are append-only")


for _normalization_model in (
    NormalizationRun,
    NormalizedFeature,
    MultiFactorScore,
    MultiFactorPredictionLink,
):
    event.listen(
        _normalization_model, "before_update", _reject_normalization_artifact_change
    )
    event.listen(
        _normalization_model, "before_delete", _reject_normalization_artifact_change
    )


class ModelPrediction(CreatedAtMixin, Base):
    """Append-only record of what a model believed before outcomes were known."""

    __tablename__ = "model_predictions"
    __table_args__ = (
        CheckConstraint(
            "length(trim(model_version)) > 0",
            name="ck_model_predictions_model_version_nonempty",
        ),
        CheckConstraint(
            "length(trim(action_label)) > 0",
            name="ck_model_predictions_action_label_nonempty",
        ),
        CheckConstraint("length(trim(horizon)) > 0", name="ck_model_predictions_horizon_nonempty"),
        CheckConstraint(
            "length(trim(feature_set_id)) > 0",
            name="ck_model_predictions_feature_set_id_nonempty",
        ),
        CheckConstraint(
            "length(trim(immutable_hash)) > 0",
            name="ck_model_predictions_immutable_hash_nonempty",
        ),
        UniqueConstraint(
            "model_version",
            "security_id",
            "asof_date",
            "horizon",
            name="uq_model_predictions_model_security_asof_horizon",
        ),
        UniqueConstraint("immutable_hash", name="uq_model_predictions_immutable_hash"),
        Index("ix_model_predictions_security_asof_date", "security_id", "asof_date"),
        Index("ix_model_predictions_feature_set_id", "feature_set_id"),
        Index("ix_model_predictions_model_version", "model_version"),
    )

    prediction_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"),
        nullable=False,
    )
    feature_set_id: Mapped[str] = mapped_column(
        ForeignKey("feature_sets.feature_set_id"),
        nullable=False,
    )
    asof_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon: Mapped[str] = mapped_column(String(32), nullable=False, default="126d")
    score: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    action_label: Mapped[str] = mapped_column(String(80), nullable=False)
    immutable_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    security: Mapped["Security"] = relationship(back_populates="predictions")
    feature_set: Mapped["FeatureSet"] = relationship(back_populates="predictions")
    outcome: Mapped[Optional["ModelOutcome"]] = relationship(
        back_populates="prediction",
        uselist=False,
    )
    score_drivers: Mapped[list["ScoreDriver"]] = relationship(back_populates="prediction")


def _reject_model_prediction_update(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("model_predictions are append-only and cannot be updated")


def _reject_model_prediction_delete(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("model_predictions are append-only and cannot be deleted")


event.listen(ModelPrediction, "before_update", _reject_model_prediction_update)
event.listen(ModelPrediction, "before_delete", _reject_model_prediction_delete)


class ScoreDriver(CreatedAtMixin, Base):
    """Explainable driver row for a stored model prediction."""

    __tablename__ = "score_drivers"
    __table_args__ = (
        CheckConstraint(
            "length(trim(driver_name)) > 0",
            name="ck_score_drivers_driver_name_nonempty",
        ),
        CheckConstraint(
            "length(trim(evidence_uri)) > 0",
            name="ck_score_drivers_evidence_uri_nonempty",
        ),
        UniqueConstraint(
            "prediction_id",
            "driver_name",
            name="uq_score_drivers_prediction_driver_name",
        ),
        Index("ix_score_drivers_prediction_id", "prediction_id"),
        Index("ix_score_drivers_driver_name", "driver_name"),
    )

    driver_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prediction_id: Mapped[str] = mapped_column(
        ForeignKey("model_predictions.prediction_id"),
        nullable=False,
    )
    driver_name: Mapped[str] = mapped_column(String(160), nullable=False)
    contribution: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    evidence_uri: Mapped[str] = mapped_column(String(1024), nullable=False)

    prediction: Mapped["ModelPrediction"] = relationship(back_populates="score_drivers")


event.listen(ScoreDriver, "before_update", _reject_model_prediction_update)
event.listen(ScoreDriver, "before_delete", _reject_model_prediction_delete)


class ModelOutcome(CreatedAtMixin, Base):
    """Append-only realized result for a stored model prediction."""

    __tablename__ = "model_outcomes"
    __table_args__ = (
        CheckConstraint(
            "length(trim(prediction_id)) > 0",
            name="ck_model_outcomes_prediction_id_nonempty",
        ),
        CheckConstraint(
            "length(trim(benchmark_security_id)) > 0",
            name="ck_model_outcomes_benchmark_security_id_nonempty",
        ),
        CheckConstraint(
            "length(trim(security_price_snapshot_id)) > 0",
            name="ck_model_outcomes_security_snapshot_id_nonempty",
        ),
        CheckConstraint(
            "length(trim(benchmark_price_snapshot_id)) > 0",
            name="ck_model_outcomes_benchmark_snapshot_id_nonempty",
        ),
        CheckConstraint(
            "length(trim(immutable_hash)) > 0",
            name="ck_model_outcomes_immutable_hash_nonempty",
        ),
        UniqueConstraint("prediction_id", name="uq_model_outcomes_prediction_id"),
        Index("ix_model_outcomes_prediction_id", "prediction_id"),
        Index("ix_model_outcomes_benchmark_security_id", "benchmark_security_id"),
        Index("ix_model_outcomes_exit_date", "exit_date"),
        Index("ix_model_outcomes_evaluated_at", "evaluated_at"),
    )

    outcome_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prediction_id: Mapped[str] = mapped_column(
        ForeignKey("model_predictions.prediction_id"),
        nullable=False,
    )
    benchmark_security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"),
        nullable=False,
    )
    security_price_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )
    benchmark_price_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    exit_date: Mapped[date] = mapped_column(Date, nullable=False)
    security_entry_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
    )
    security_exit_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
    )
    benchmark_entry_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
    )
    benchmark_exit_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
    )
    realised_return: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False)
    benchmark_return: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False)
    excess_return: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False)
    max_drawdown: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    immutable_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    prediction: Mapped["ModelPrediction"] = relationship(back_populates="outcome")
    benchmark_security: Mapped["Security"] = relationship(
        foreign_keys=[benchmark_security_id]
    )
    security_price_snapshot: Mapped["SourceSnapshot"] = relationship(
        foreign_keys=[security_price_snapshot_id]
    )
    benchmark_price_snapshot: Mapped["SourceSnapshot"] = relationship(
        foreign_keys=[benchmark_price_snapshot_id]
    )


def _reject_model_outcome_update(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("model_outcomes are append-only and cannot be updated")


def _reject_model_outcome_delete(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("model_outcomes are append-only and cannot be deleted")


event.listen(ModelOutcome, "before_update", _reject_model_outcome_update)
event.listen(ModelOutcome, "before_delete", _reject_model_outcome_delete)


class ShadowPredictionBatch(Base):
    """Sealed monthly shadow cohort bound to one executable model lock."""

    __tablename__ = "shadow_prediction_batches"
    __table_args__ = (
        CheckConstraint(
            "length(trim(model_version)) > 0",
            name="ck_shadow_batches_model_version_nonempty",
        ),
        CheckConstraint(
            "length(trim(executable_lock_uri)) > 0",
            name="ck_shadow_batches_lock_uri_nonempty",
        ),
        CheckConstraint(
            "length(trim(executable_lock_hash)) > 0",
            name="ck_shadow_batches_lock_hash_nonempty",
        ),
        CheckConstraint(
            "length(trim(code_commit)) > 0",
            name="ck_shadow_batches_code_commit_nonempty",
        ),
        CheckConstraint(
            "length(trim(execution_commit)) > 0",
            name="ck_shadow_batches_execution_commit_nonempty",
        ),
        CheckConstraint(
            "expected_member_count >= 0 AND scored_count >= 0 "
            "AND excluded_count >= 0",
            name="ck_shadow_batches_counts_nonnegative",
        ),
        CheckConstraint(
            "expected_member_count = scored_count + excluded_count",
            name="ck_shadow_batches_counts_reconcile",
        ),
        CheckConstraint(
            "product_label_policy = 'WITHHELD_RESEARCH_ONLY'",
            name="ck_shadow_batches_product_policy",
        ),
        CheckConstraint(
            "claims_eligible = false",
            name="ck_shadow_batches_claims_ineligible",
        ),
        CheckConstraint(
            "length(trim(batch_hash)) > 0",
            name="ck_shadow_batches_hash_nonempty",
        ),
        UniqueConstraint(
            "model_version",
            "prediction_date",
            name="uq_shadow_batches_model_prediction_date",
        ),
        UniqueConstraint("batch_hash", name="uq_shadow_batches_hash"),
        Index(
            "ix_shadow_batches_universe_prediction_date",
            "universe_id",
            "prediction_date",
        ),
        Index("ix_shadow_batches_recorded_at", "recorded_at"),
    )

    batch_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    universe_id: Mapped[str] = mapped_column(
        ForeignKey("universe_definitions.universe_id"), nullable=False
    )
    normalization_run_id: Mapped[str] = mapped_column(
        ForeignKey("normalization_runs.normalization_run_id"), nullable=False
    )
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    prediction_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    executable_lock_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    executable_lock_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    code_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    input_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expected_member_count: Mapped[int] = mapped_column(nullable=False)
    scored_count: Mapped[int] = mapped_column(nullable=False)
    excluded_count: Mapped[int] = mapped_column(nullable=False)
    claims_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome_evaluation_authorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    product_label_policy: Mapped[str] = mapped_column(
        String(64), nullable=False, default="WITHHELD_RESEARCH_ONLY"
    )
    batch_hash: Mapped[str] = mapped_column(String(128), nullable=False)


class ShadowPredictionRecord(Base):
    """One immutable scored or excluded member in a shadow batch."""

    __tablename__ = "shadow_prediction_records"
    __table_args__ = (
        CheckConstraint(
            "length(trim(ticker)) > 0",
            name="ck_shadow_records_ticker_nonempty",
        ),
        CheckConstraint(
            "length(trim(classification_branch)) > 0",
            name="ck_shadow_records_branch_nonempty",
        ),
        CheckConstraint(
            "disposition IN ('SCORED', 'EXCLUDED')",
            name="ck_shadow_records_disposition",
        ),
        CheckConstraint(
            "research_score IS NULL OR "
            "(research_score >= 0 AND research_score <= 100)",
            name="ck_shadow_records_score_range",
        ),
        CheckConstraint(
            "research_confidence IS NULL OR "
            "(research_confidence >= 0 AND research_confidence <= 1)",
            name="ck_shadow_records_confidence_range",
        ),
        CheckConstraint(
            "(disposition = 'SCORED' AND research_score IS NOT NULL "
            "AND research_label IS NOT NULL) OR "
            "(disposition = 'EXCLUDED' AND research_score IS NULL "
            "AND research_confidence IS NULL AND research_label IS NULL)",
            name="ck_shadow_records_disposition_fields",
        ),
        CheckConstraint(
            "product_label IS NULL",
            name="ck_shadow_records_product_label_withheld",
        ),
        CheckConstraint(
            "product_label_status = 'WITHHELD_RESEARCH_ONLY'",
            name="ck_shadow_records_product_label_status",
        ),
        CheckConstraint(
            "length(trim(record_hash)) > 0",
            name="ck_shadow_records_hash_nonempty",
        ),
        UniqueConstraint(
            "batch_id",
            "security_id",
            name="uq_shadow_records_batch_security",
        ),
        UniqueConstraint("record_hash", name="uq_shadow_records_hash"),
        Index("ix_shadow_records_batch_disposition", "batch_id", "disposition"),
        Index("ix_shadow_records_security_id", "security_id"),
    )

    shadow_prediction_id: Mapped[str] = mapped_column(
        String(100), primary_key=True
    )
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("shadow_prediction_batches.batch_id"), nullable=False
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.security_id"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    classification_branch: Mapped[str] = mapped_column(String(80), nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    research_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6))
    research_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    research_label: Mapped[Optional[str]] = mapped_column(String(80))
    product_label: Mapped[Optional[str]] = mapped_column(String(80))
    product_label_status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="WITHHELD_RESEARCH_ONLY"
    )
    exclusions_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    drivers_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    prediction_ids_json: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    input_lineage_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_hash: Mapped[str] = mapped_column(String(128), nullable=False)


class ShadowOutcomeRecord(Base):
    """Append-only link added only after a shadow horizon outcome matures."""

    __tablename__ = "shadow_outcome_records"
    __table_args__ = (
        CheckConstraint(
            "length(trim(horizon)) > 0",
            name="ck_shadow_outcomes_horizon_nonempty",
        ),
        CheckConstraint(
            "length(trim(immutable_hash)) > 0",
            name="ck_shadow_outcomes_hash_nonempty",
        ),
        UniqueConstraint(
            "shadow_prediction_id",
            "horizon",
            name="uq_shadow_outcomes_record_horizon",
        ),
        UniqueConstraint("prediction_id", name="uq_shadow_outcomes_prediction"),
        UniqueConstraint("outcome_id", name="uq_shadow_outcomes_outcome"),
        UniqueConstraint("immutable_hash", name="uq_shadow_outcomes_hash"),
        Index("ix_shadow_outcomes_recorded_at", "recorded_at"),
    )

    shadow_outcome_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    shadow_prediction_id: Mapped[str] = mapped_column(
        ForeignKey("shadow_prediction_records.shadow_prediction_id"),
        nullable=False,
    )
    prediction_id: Mapped[str] = mapped_column(
        ForeignKey("model_predictions.prediction_id"), nullable=False
    )
    outcome_id: Mapped[str] = mapped_column(
        ForeignKey("model_outcomes.outcome_id"), nullable=False
    )
    horizon: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    immutable_hash: Mapped[str] = mapped_column(String(128), nullable=False)


def _reject_shadow_ledger_change(mapper, connection, target) -> None:
    del mapper, connection, target
    raise RuntimeError("shadow ledger artifacts are append-only")


for _shadow_model in (
    ShadowPredictionBatch,
    ShadowPredictionRecord,
    ShadowOutcomeRecord,
):
    event.listen(_shadow_model, "before_update", _reject_shadow_ledger_change)
    event.listen(_shadow_model, "before_delete", _reject_shadow_ledger_change)


class ExperimentRegistry(TimestampMixin, Base):
    """Registry entry for a model, feature, or backtest experiment."""

    __tablename__ = "experiment_registry"
    __table_args__ = (
        CheckConstraint(
            "length(trim(experiment_id)) > 0",
            name="ck_experiment_registry_experiment_id_nonempty",
        ),
        CheckConstraint(
            "length(trim(hypothesis_id)) > 0",
            name="ck_experiment_registry_hypothesis_id_nonempty",
        ),
        CheckConstraint(
            "length(trim(data_snapshot_hash)) > 0",
            name="ck_experiment_registry_data_snapshot_hash_nonempty",
        ),
        Index("ix_experiment_registry_hypothesis_id", "hypothesis_id"),
        Index("ix_experiment_registry_started_at", "started_at"),
    )

    experiment_id: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
        default=lambda: f"exp_{uuid.uuid4().hex}",
    )
    hypothesis_id: Mapped[str] = mapped_column(String(160), nullable=False)
    data_snapshot_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    code_commit: Mapped[Optional[str]] = mapped_column(String(64))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    result_uri: Mapped[Optional[str]] = mapped_column(String(1024))
    notes: Mapped[Optional[str]] = mapped_column(Text)
