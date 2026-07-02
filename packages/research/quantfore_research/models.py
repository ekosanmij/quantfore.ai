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
    """Point-in-time company fact extracted from filings or vendor fundamentals."""

    __tablename__ = "fundamentals"
    __table_args__ = (
        CheckConstraint(
            "length(trim(metric)) > 0",
            name="ck_fundamentals_metric_nonempty",
        ),
        CheckConstraint("length(trim(unit)) > 0", name="ck_fundamentals_unit_nonempty"),
        Index("ix_fundamentals_security_metric_period", "security_id", "metric", "period_end"),
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
    filed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    form_type: Mapped[Optional[str]] = mapped_column(String(32))
    accession_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )

    security: Mapped["Security"] = relationship(back_populates="fundamentals")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(
        back_populates="fundamentals"
    )


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
    value: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.snapshot_id"),
        nullable=False,
    )
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    security: Mapped["Security"] = relationship(back_populates="features")
    source_snapshot: Mapped["SourceSnapshot"] = relationship(back_populates="features")
    feature_set: Mapped["FeatureSet"] = relationship(back_populates="features")


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
