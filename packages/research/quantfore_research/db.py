"""Database helpers for research scripts and tests."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from quantfore_research.config import Settings
from quantfore_research.models import Base


def build_engine(
    settings: Optional[Settings] = None,
    database_url: Optional[str] = None,
) -> Engine:
    """Create a SQLAlchemy engine from settings or an explicit URL."""

    resolved_settings = settings or Settings.from_env()
    resolved_url = database_url or resolved_settings.database_url
    connect_args = {}

    is_sqlite = resolved_url.startswith("sqlite")

    if is_sqlite:
        connect_args["check_same_thread"] = False

    engine = create_engine(
        resolved_url,
        echo=resolved_settings.echo_sql,
        future=True,
        connect_args=connect_args,
    )

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _configure_sqlite_connection(dbapi_connection, connection_record):
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # Rebuild databases are derived, hash-verified artifacts: any
            # interrupted build is discarded and reproduced from frozen raw
            # bytes, so relaxed durability trades nothing while removing the
            # per-transaction fsync stalls that dominate bulk ingestion.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA cache_size=-262144")
            cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    """Create all currently known research tables."""

    Base.metadata.create_all(bind=engine)
    _upgrade_sqlite_price_adjustment_columns(engine)
    _upgrade_sqlite_fundamental_columns(engine)
    _upgrade_sqlite_feature_lineage_columns(engine)


def _upgrade_sqlite_price_adjustment_columns(engine: Engine) -> None:
    """Add Sprint 6 adjusted-price fields to pre-migration local databases.

    The prototype currently has no migration framework and its documented
    default is SQLite. Keep this compatibility shim deliberately additive;
    production databases should use a reviewed schema migration.
    """

    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "prices" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("prices")}
    columns = {
        "adj_open": "NUMERIC(18, 6)",
        "adj_high": "NUMERIC(18, 6)",
        "adj_low": "NUMERIC(18, 6)",
        "adj_volume": "NUMERIC(24, 6)",
    }
    missing = [name for name in columns if name not in existing]
    if not missing:
        return
    with engine.begin() as connection:
        for name in missing:
            connection.execute(
                text(f"ALTER TABLE prices ADD COLUMN {name} {columns[name]}")
            )


def _upgrade_sqlite_fundamental_columns(engine: Engine) -> None:
    """Add and backfill Sprint 8 point-in-time fields in local SQLite stores.

    SQLite cannot add the v1 constraints to an existing table without a
    destructive rebuild.  The compatibility migration is therefore additive;
    fresh databases receive the full constraints from SQLAlchemy metadata.
    """

    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "fundamentals" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("fundamentals")}
    columns = {
        "fiscal_period_end": "DATE",
        "fiscal_year": "INTEGER",
        "fiscal_quarter": "INTEGER",
        "period_type": "VARCHAR(16)",
        "filing_accession": "VARCHAR(64)",
        "accepted_at": "DATETIME",
        "public_release_at": "DATETIME",
        "vendor_available_at": "DATETIME",
        "model_available_at": "DATETIME",
        "revision_version": "INTEGER",
        "concept": "VARCHAR(255)",
        "standardized_concept": "VARCHAR(160)",
        "source_hash": "VARCHAR(128)",
    }
    missing = [name for name in columns if name not in existing]
    if not missing:
        return

    with engine.begin() as connection:
        for name in missing:
            connection.execute(
                text(f"ALTER TABLE fundamentals ADD COLUMN {name} {columns[name]}")
            )
        connection.execute(
            text(
                """
                UPDATE fundamentals
                SET fiscal_period_end = COALESCE(fiscal_period_end, period_end),
                    fiscal_year = COALESCE(
                        fiscal_year,
                        CAST(strftime('%Y', period_end) AS INTEGER)
                    ),
                    fiscal_quarter = COALESCE(
                        fiscal_quarter,
                        CASE
                            WHEN instr(upper(COALESCE(fiscal_period, '')), 'Q') > 0
                            THEN CAST(substr(
                                upper(fiscal_period),
                                instr(upper(fiscal_period), 'Q') + 1,
                                1
                            ) AS INTEGER)
                        END
                    ),
                    period_type = COALESCE(
                        period_type,
                        CASE
                            WHEN upper(COALESCE(form_type, '')) LIKE '10-Q%'
                              OR instr(upper(COALESCE(fiscal_period, '')), 'Q') > 0
                            THEN 'QUARTERLY'
                            ELSE 'ANNUAL'
                        END
                    ),
                    filing_accession = COALESCE(filing_accession, accession_no),
                    vendor_available_at = COALESCE(
                        vendor_available_at, available_at, filed_at
                    ),
                    model_available_at = COALESCE(
                        model_available_at, available_at, filed_at
                    ),
                    revision_version = COALESCE(revision_version, 1),
                    concept = COALESCE(concept, metric),
                    standardized_concept = COALESCE(standardized_concept, metric),
                    source_hash = COALESCE(
                        source_hash,
                        (SELECT hash FROM source_snapshots
                         WHERE source_snapshots.snapshot_id =
                               fundamentals.source_snapshot_id)
                    )
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_fundamentals_security_concept_period "
                "ON fundamentals "
                "(security_id, standardized_concept, fiscal_period_end)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_fundamentals_as_known "
                "ON fundamentals "
                "(security_id, model_available_at, revision_version)"
            )
        )


def _upgrade_sqlite_feature_lineage_columns(engine: Engine) -> None:
    """Add Sprint 8 formula and input lineage to existing SQLite stores."""

    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "features" not in inspector.get_table_names():
        return
    inspected_columns = inspector.get_columns("features")
    existing = {column["name"] for column in inspected_columns}
    value_is_not_nullable = not next(
        column["nullable"] for column in inspected_columns if column["name"] == "value"
    )
    columns = {
        "raw_value": "NUMERIC(24, 12)",
        "family": "VARCHAR(32)",
        "formula_version": "VARCHAR(64)",
        "formula": "TEXT",
        "direction": "VARCHAR(8)",
        "applicability_status": "VARCHAR(24)",
        "missing_reason": "VARCHAR(80)",
        "inputs_json": "JSON",
    }
    missing = [name for name in columns if name not in existing]
    if not missing and not value_is_not_nullable:
        return
    with engine.begin() as connection:
        for name in missing:
            connection.execute(
                text(f"ALTER TABLE features ADD COLUMN {name} {columns[name]}")
            )
        connection.execute(
            text(
                """
                UPDATE features
                SET raw_value = COALESCE(raw_value, value),
                    family = COALESCE(family, 'legacy'),
                    formula_version = COALESCE(formula_version, version),
                    formula = COALESCE(formula, 'legacy:' || feature_name),
                    direction = COALESCE(direction, 'HIGHER'),
                    applicability_status = COALESCE(
                        applicability_status,
                        CASE WHEN value IS NULL THEN 'MISSING' ELSE 'APPLICABLE' END
                    ),
                    missing_reason = CASE
                        WHEN value IS NULL
                        THEN COALESCE(missing_reason, 'SOURCE_MISSING')
                        ELSE missing_reason
                    END,
                    inputs_json = COALESCE(inputs_json, '{}')
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_features_family_status "
                "ON features (family, applicability_status)"
            )
        )
        if value_is_not_nullable:
            connection.execute(
                text(
                    """
                    CREATE TABLE features_sprint8 (
                        feature_id VARCHAR(36) NOT NULL PRIMARY KEY,
                        feature_set_id VARCHAR(100) NOT NULL
                            REFERENCES feature_sets(feature_set_id),
                        security_id VARCHAR(36) NOT NULL
                            REFERENCES securities(security_id),
                        asof_date DATE NOT NULL,
                        available_at DATETIME NOT NULL,
                        feature_name VARCHAR(160) NOT NULL,
                        value NUMERIC(20, 10),
                        raw_value NUMERIC(24, 12),
                        version VARCHAR(64) NOT NULL,
                        family VARCHAR(32) NOT NULL,
                        formula_version VARCHAR(64) NOT NULL,
                        formula TEXT NOT NULL,
                        direction VARCHAR(8) NOT NULL,
                        applicability_status VARCHAR(24) NOT NULL,
                        missing_reason VARCHAR(80),
                        inputs_json JSON NOT NULL,
                        source_snapshot_id VARCHAR(36) NOT NULL
                            REFERENCES source_snapshots(snapshot_id),
                        source_hash VARCHAR(128) NOT NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        CONSTRAINT uq_features_set_security_asof_name_version
                            UNIQUE (
                                feature_set_id, security_id, asof_date,
                                feature_name, version
                            ),
                        CONSTRAINT ck_features_name_nonempty
                            CHECK (length(trim(feature_name)) > 0),
                        CONSTRAINT ck_features_version_nonempty
                            CHECK (length(trim(version)) > 0),
                        CONSTRAINT ck_features_feature_set_id_nonempty
                            CHECK (length(trim(feature_set_id)) > 0),
                        CONSTRAINT ck_features_source_hash_nonempty
                            CHECK (length(trim(source_hash)) > 0),
                        CONSTRAINT ck_features_family_nonempty
                            CHECK (length(trim(family)) > 0),
                        CONSTRAINT ck_features_formula_version_nonempty
                            CHECK (length(trim(formula_version)) > 0),
                        CONSTRAINT ck_features_direction
                            CHECK (direction IN ('HIGHER', 'LOWER')),
                        CONSTRAINT ck_features_applicability_status
                            CHECK (applicability_status IN (
                                'APPLICABLE', 'MISSING', 'NOT_APPLICABLE'
                            )),
                        CONSTRAINT ck_features_applicable_has_value
                            CHECK (
                                applicability_status != 'APPLICABLE'
                                OR value IS NOT NULL
                            ),
                        CONSTRAINT ck_features_unavailable_has_no_value
                            CHECK (
                                applicability_status = 'APPLICABLE'
                                OR value IS NULL
                            ),
                        CONSTRAINT ck_features_unavailable_has_reason
                            CHECK (
                                applicability_status = 'APPLICABLE'
                                OR (
                                    missing_reason IS NOT NULL
                                    AND length(trim(missing_reason)) > 0
                                )
                            ),
                        CONSTRAINT ck_features_applicable_has_raw_value
                            CHECK (
                                applicability_status != 'APPLICABLE'
                                OR raw_value IS NOT NULL
                            )
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO features_sprint8 (
                        feature_id, feature_set_id, security_id, asof_date,
                        available_at, feature_name, value, raw_value, version,
                        family, formula_version, formula, direction,
                        applicability_status, missing_reason, inputs_json,
                        source_snapshot_id, source_hash, created_at, updated_at
                    )
                    SELECT
                        feature_id, feature_set_id, security_id, asof_date,
                        available_at, feature_name, value, raw_value, version,
                        family, formula_version, formula, direction,
                        applicability_status, missing_reason, inputs_json,
                        source_snapshot_id, source_hash, created_at, updated_at
                    FROM features
                    """
                )
            )
            connection.execute(text("DROP TABLE features"))
            connection.execute(text("ALTER TABLE features_sprint8 RENAME TO features"))
            for statement in (
                "CREATE INDEX ix_features_security_asof_date "
                "ON features (security_id, asof_date)",
                "CREATE INDEX ix_features_available_at ON features (available_at)",
                "CREATE INDEX ix_features_source_snapshot_id "
                "ON features (source_snapshot_id)",
                "CREATE INDEX ix_features_family_status "
                "ON features (family, applicability_status)",
            ):
                connection.execute(text(statement))


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory with predictable commit behaviour."""

    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Provide a transactional scope around a set of database operations."""

    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
