from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from pipelines.audit_point_in_time_fundamentals import (
    build_audit_document,
    render_markdown,
)
from quantfore_research.db import build_engine, create_schema, make_session_factory
from quantfore_research.models import (
    Fundamental,
    Security,
    SecurityIdentifier,
    SourceSnapshot,
)
from quantfore_research.validation.point_in_time_fundamentals import (
    STANDARD_SECTORS,
    SecReconciliationSample,
    audit_point_in_time_fundamentals,
    derive_sec_reconciliation_samples,
)
from quantfore_research.validation.fundamental_audit_gate import (
    verify_fundamental_audit,
)


HASH = "b" * 64
FILED = datetime(2020, 5, 1, 20, 0, tzinfo=timezone.utc)
AVAILABLE = datetime(2020, 5, 1, 22, 0, tzinfo=timezone.utc)


def make_session():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    return make_session_factory(engine)()


def seed_security(session, *, security_id="security-1", sector="Industrials"):
    if session.get(SourceSnapshot, "vendor-snapshot") is None:
        session.add_all(
            [
                SourceSnapshot(
                    snapshot_id="vendor-snapshot",
                    vendor="Licensed Test Vendor",
                    dataset="pit-fundamentals",
                    retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    license_tag="test",
                    source_hash=HASH,
                    storage_uri="raw/test/vendor-facts.json",
                ),
                SourceSnapshot(
                    snapshot_id="identifier-snapshot",
                    vendor="Licensed Test Vendor",
                    dataset="security-master",
                    license_tag="test",
                    source_hash="c" * 64,
                    storage_uri="raw/test/identifiers.json",
                ),
                SourceSnapshot(
                    snapshot_id="sec-snapshot",
                    vendor="SEC EDGAR",
                    dataset="filing-evidence",
                    license_tag="public",
                    source_hash="d" * 64,
                    storage_uri="raw/test/sec-filing.json",
                ),
            ]
        )
        session.flush()
    security = Security(
        security_id=security_id,
        ticker=security_id.upper(),
        name=f"Issuer {security_id}",
        sector=sector,
    )
    session.add(security)
    session.flush()
    session.add(
        SecurityIdentifier(
            identifier_id=f"identifier-{security_id}",
            security_id=security_id,
            identifier_type="TEST_PERMANENT_ID",
            identifier_value=f"PERM-{security_id}",
            valid_from=date(2000, 1, 1),
            is_permanent=True,
            source_snapshot_id="identifier-snapshot",
            source_hash="c" * 64,
        )
    )
    session.flush()
    return security


def add_fact(
    session,
    concept,
    value,
    *,
    security_id="security-1",
    fundamental_id=None,
    unit="USD",
    revision=1,
    model_available_at=AVAILABLE,
    form_type="10-Q",
    accession=None,
    source_snapshot_id="vendor-snapshot",
    source_hash=HASH,
):
    fact = Fundamental(
        fundamental_id=fundamental_id or f"fact-{security_id}-{concept}-{revision}",
        security_id=security_id,
        fiscal_period_end=date(2020, 3, 31),
        fiscal_year=2020,
        fiscal_quarter=1,
        period_type="QUARTERLY",
        form_type=form_type,
        filing_accession=accession or f"accession-{security_id}-{revision}",
        filed_at=min(FILED, model_available_at),
        accepted_at=min(FILED, model_available_at),
        public_release_at=min(FILED, model_available_at),
        vendor_available_at=min(AVAILABLE, model_available_at),
        model_available_at=model_available_at,
        revision_version=revision,
        concept=f"Vendor{concept}",
        standardized_concept=concept,
        value=Decimal(value),
        unit=unit,
        source_snapshot_id=source_snapshot_id,
        source_hash=source_hash,
    )
    session.add(fact)
    return fact


def test_clean_accounting_facts_pass_structural_audit():
    session = make_session()
    seed_security(session)
    for concept, value in (
        ("total_assets", "100"),
        ("total_liabilities", "60"),
        ("shareholders_equity", "40"),
        ("cash_from_operations", "20"),
        ("capital_expenditure", "5"),
        ("free_cash_flow", "15"),
        ("revenue", "80"),
        ("net_income_common", "8"),
        ("total_debt", "30"),
    ):
        add_fact(session, concept, value)
    session.commit()

    audit = audit_point_in_time_fundamentals(
        session, enforce_reconciliation_gate=False
    )

    assert audit.status == "pass"
    assert audit.hard_failure_count == 0
    assert audit.review_finding_count == 0
    assert len(audit.fact_hash) == 64
    assert len(audit.availability_revision_hash) == 64


def test_sec_primary_mode_requires_acceptance_bound_source_evidence():
    session = make_session()
    seed_security(session)
    session.get(SourceSnapshot, "vendor-snapshot").vendor = "SEC EDGAR Primary"
    fact = add_fact(session, "revenue", "100")
    fact.vendor_available_at = fact.accepted_at
    fact.public_release_at = None
    session.commit()

    audit = audit_point_in_time_fundamentals(
        session,
        source_snapshot_ids=["vendor-snapshot"],
        enforce_reconciliation_gate=False,
        require_sec_primary_evidence=True,
    )

    assert audit.hard_failure_count == 0
    assert audit.evidence_mode == "sec_primary_source_integrity"

    seed_security(session, security_id="security-2")
    add_fact(session, "revenue", "100", security_id="security-2")
    session.commit()
    failed = audit_point_in_time_fundamentals(
        session,
        source_snapshot_ids=["vendor-snapshot"],
        enforce_reconciliation_gate=False,
        require_sec_primary_evidence=True,
    )
    assert "SEC_AVAILABILITY_BINDING_INVALID" in {
        finding.code for finding in failed.findings
    }


def test_audit_catches_future_use_restatement_order_and_accounting_problems():
    session = make_session()
    seed_security(session)
    first = add_fact(
        session,
        "revenue",
        "0",
        revision=1,
        model_available_at=datetime(2020, 7, 1, tzinfo=timezone.utc),
    )
    add_fact(
        session,
        "revenue",
        "10",
        revision=2,
        model_available_at=datetime(2020, 6, 1, tzinfo=timezone.utc),
        form_type="10-Q/A",
        accession="accession-amendment",
    )
    add_fact(session, "total_assets", "100")
    add_fact(session, "total_liabilities", "90")
    add_fact(session, "shareholders_equity", "40")
    add_fact(session, "cash_from_operations", "20")
    add_fact(session, "capital_expenditure", "5")
    add_fact(session, "free_cash_flow", "99")
    session.commit()

    audit = audit_point_in_time_fundamentals(
        session,
        prediction_timestamp=datetime(2020, 6, 30, tzinfo=timezone.utc),
        candidate_fact_ids=[first.fundamental_id],
        enforce_reconciliation_gate=False,
    )
    codes = {finding.code for finding in audit.findings}

    assert "VALUE_USED_BEFORE_AVAILABLE" in codes
    assert "RESTATEMENT_ORDER_INVALID" in codes
    assert "BALANCE_SHEET_EQUATION_IMPLAUSIBLE" in codes
    assert "CASH_FLOW_RECONCILIATION_IMPLAUSIBLE" in codes


def test_sec_difference_is_recorded_and_incomplete_sample_is_a_hard_gate():
    session = make_session()
    seed_security(session, sector="Industrials")
    fact = add_fact(session, "revenue", "100")
    add_fact(
        session,
        "revenue",
        "90",
        fundamental_id="sec-revenue",
        accession="sec-accession",
        source_snapshot_id="sec-snapshot",
        source_hash="d" * 64,
    )
    session.commit()
    sample = SecReconciliationSample(
        vendor_fundamental_id=fact.fundamental_id,
        security_id="security-1",
        sector="Industrials",
        fiscal_period_end=date(2020, 3, 31),
        standardized_concept="revenue",
        sec_value=Decimal("90"),
        sec_unit="USD",
        sec_filing_accession="sec-accession",
        sec_source_snapshot_id="sec-snapshot",
    )

    audit = audit_point_in_time_fundamentals(
        session,
        source_snapshot_ids=["vendor-snapshot"],
        reconciliation_samples=[sample],
    )
    codes = {finding.code for finding in audit.findings}

    assert audit.status == "fail"
    assert "SEC_SAMPLE_TOO_SMALL" in codes
    assert "SEC_SECTOR_COVERAGE_INCOMPLETE" in codes
    assert "SEC_VALUE_DIFFERENCE" in codes
    difference = next(
        row for row in audit.findings if row.code == "SEC_VALUE_DIFFERENCE"
    )
    assert difference.context["sec_filing_accession"] == "sec-accession"


def test_sec_samples_can_be_derived_from_independently_stored_companyfacts():
    session = make_session()
    seed_security(session, sector="Industrials")
    vendor = add_fact(session, "revenue", "100")
    add_fact(
        session,
        "revenue",
        "99",
        fundamental_id="sec-revenue",
        accession="sec-accession",
        source_snapshot_id="sec-snapshot",
        source_hash="d" * 64,
    )
    session.commit()

    samples = derive_sec_reconciliation_samples(
        session, vendor_source_snapshot_ids=["vendor-snapshot"]
    )

    assert len(samples) == 1
    assert samples[0].vendor_fundamental_id == vendor.fundamental_id
    assert samples[0].sec_source_snapshot_id == "sec-snapshot"
    assert samples[0].sec_value == Decimal("99.000000")


def test_thirty_issuer_periods_across_all_sectors_pass_reconciliation_gate():
    session = make_session()
    sectors = sorted(STANDARD_SECTORS)
    for index in range(30):
        security_id = f"security-{index:02d}"
        seed_security(session, security_id=security_id, sector=sectors[index % 11])
        add_fact(
            session,
            "revenue",
            str(100 + index),
            security_id=security_id,
            fundamental_id=f"vendor-{index:02d}",
        )
        add_fact(
            session,
            "revenue",
            str(100 + index),
            security_id=security_id,
            fundamental_id=f"sec-{index:02d}",
            source_snapshot_id="sec-snapshot",
            source_hash="d" * 64,
        )
    session.commit()

    samples = derive_sec_reconciliation_samples(
        session, vendor_source_snapshot_ids=["vendor-snapshot"]
    )
    audit = audit_point_in_time_fundamentals(
        session,
        source_snapshot_ids=["vendor-snapshot"],
        reconciliation_samples=samples,
    )

    assert len(samples) == 30
    assert audit.reconciliation_issuer_period_count == 30
    assert set(audit.reconciliation_sectors) == STANDARD_SECTORS
    assert audit.status == "pass"


def test_audit_report_keeps_claims_false_and_renders_unresolved_differences():
    session = make_session()
    seed_security(session)
    add_fact(session, "revenue", "100")
    session.commit()
    audit = audit_point_in_time_fundamentals(
        session, enforce_reconciliation_gate=False
    )
    document = build_audit_document(
        audit,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        code_revision="test-revision",
    )
    markdown = render_markdown(document)

    assert document["claims_eligible"] is False
    assert document["decision"] == "pass"
    assert "Review findings and unresolved differences" in markdown
    assert "`false`" in markdown


def test_feature_gate_binds_passing_audit_to_exact_warehouse_content(tmp_path):
    session = make_session()
    sectors = sorted(STANDARD_SECTORS)
    for index in range(30):
        security_id = f"gate-security-{index:02d}"
        seed_security(session, security_id=security_id, sector=sectors[index % 11])
        add_fact(
            session,
            "revenue",
            str(100 + index),
            security_id=security_id,
            fundamental_id=f"gate-vendor-{index:02d}",
        )
        add_fact(
            session,
            "revenue",
            str(100 + index),
            security_id=security_id,
            fundamental_id=f"gate-sec-{index:02d}",
            source_snapshot_id="sec-snapshot",
            source_hash="d" * 64,
        )
    session.commit()
    samples = derive_sec_reconciliation_samples(
        session, vendor_source_snapshot_ids=["vendor-snapshot"]
    )
    audit = audit_point_in_time_fundamentals(
        session,
        source_snapshot_ids=["vendor-snapshot"],
        reconciliation_samples=samples,
    )
    document = build_audit_document(
        audit,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        code_revision="test-revision",
        source_snapshot_hashes={"vendor-snapshot": HASH},
    )
    body = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    path = tmp_path / "audit.json"
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()

    binding = verify_fundamental_audit(
        session,
        audit_path=path,
        expected_audit_sha256=digest,
        source_snapshot_ids=["vendor-snapshot"],
    )

    assert binding.fact_hash == audit.fact_hash
    assert binding.availability_revision_hash == audit.availability_revision_hash
    assert binding.source_snapshot_hashes == {"vendor-snapshot": HASH}
    with pytest.raises(ValueError, match="SHA-256"):
        verify_fundamental_audit(
            session,
            audit_path=path,
            expected_audit_sha256="0" * 64,
            source_snapshot_ids=["vendor-snapshot"],
        )
