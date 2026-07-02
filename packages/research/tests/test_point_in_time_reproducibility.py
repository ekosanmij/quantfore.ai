import json
import subprocess
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

import pipelines.close_point_in_time_sprint as closure_pipeline
from pipelines.close_point_in_time_sprint import (
    DirtyWorktreeError,
    RebuildArtifacts,
    build_closure_document,
    require_clean_git_worktree,
)
from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.models import (
    Security,
    SourceSnapshot,
    UniverseDefinition,
    UniverseMembership,
)
from quantfore_research.validation.reproducibility import (
    build_rebuild_fingerprint,
    canonical_json_bytes,
    compare_rebuild_fingerprints,
    universe_membership_hash,
)


HASH = "a" * 64
TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _seed_memberships(database_url, *, reverse=False, changed=False):
    engine = build_engine(database_url=database_url)
    create_schema(engine)
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        snapshot = SourceSnapshot(
            snapshot_id="snapshot",
            vendor="Licensed Test Vendor",
            dataset="memberships",
            retrieved_at=TIMESTAMP,
            license_tag="test",
            source_hash=HASH,
            storage_uri="raw/test/memberships.json",
        )
        benchmark = Security(
            security_id="security-spy", ticker="SPY", name="SPY"
        )
        securities = [
            Security(security_id="security-a", ticker="AAA", name="AAA"),
            Security(security_id="security-b", ticker="BBB", name="BBB"),
        ]
        session.add_all([snapshot, benchmark, *securities])
        session.flush()
        session.add(
            UniverseDefinition(
                universe_id="sp500-pit-v1",
                name="Historical S&P 500",
                version="v1",
                description="test",
                window_start=date(2020, 1, 1),
                window_end=date(2024, 12, 31),
                benchmark_security_id=benchmark.security_id,
                benchmark_excluded_from_rankings=True,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=HASH,
            )
        )
        rows = [
            UniverseMembership(
                membership_id="membership-a",
                universe_id="sp500-pit-v1",
                security_id="security-a",
                effective_from=date(2020, 1, 1),
                effective_to=(date(2023, 12, 30) if changed else date(2023, 12, 31)),
                announced_at=datetime(2019, 12, 1, tzinfo=timezone.utc),
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=HASH,
            ),
            UniverseMembership(
                membership_id="membership-b",
                universe_id="sp500-pit-v1",
                security_id="security-b",
                effective_from=date(2021, 1, 1),
                effective_to=None,
                announced_at=datetime(2020, 12, 1, tzinfo=timezone.utc),
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=HASH,
            ),
        ]
        session.add_all(list(reversed(rows)) if reverse else rows)
    return engine, factory


def _fingerprint(factory):
    lineage = {
        "prediction_count": 10,
        "outcome_count": 9,
        "prediction_ids": ["prediction"],
    }
    report = {
        "manifest": lineage,
        "cohorts": [
            {"prediction_date": "2024-01-31", "expected_count": 2},
            {"prediction_date": "2024-02-29", "expected_count": 1},
        ],
        "metrics": {
            "mean_rank_ic": -0.1,
            "median_rank_ic": -0.2,
            "top_minus_bottom_spread": -0.03,
        },
    }
    audit = {"decision": "review", "claims_eligible": False}
    with factory() as session:
        return build_rebuild_fingerprint(
            session,
            universe_id="sp500-pit-v1",
            audit_document=audit,
            backtest_report=report,
            backtest_lineage=lineage,
        )


def _artifacts(fingerprint):
    return RebuildArtifacts(
        fingerprint=fingerprint,
        audit_document={},
        audit_json=b"audit",
        audit_markdown=b"audit-md",
        backtest_report={},
        backtest_json=b"report",
        backtest_markdown=b"report-md",
        backtest_lineage={},
        lineage_json=b"lineage",
    )


def test_membership_hash_and_complete_fingerprint_reproduce_across_clean_databases(
    tmp_path,
):
    first_engine, first_factory = _seed_memberships(
        f"sqlite+pysqlite:///{tmp_path / 'first.db'}"
    )
    second_engine, second_factory = _seed_memberships(
        f"sqlite+pysqlite:///{tmp_path / 'second.db'}", reverse=True
    )
    with first_factory() as session:
        first_hash = universe_membership_hash(
            session, universe_id="sp500-pit-v1"
        )
    with second_factory() as session:
        second_hash = universe_membership_hash(
            session, universe_id="sp500-pit-v1"
        )
    assert first_hash == second_hash

    first = _fingerprint(first_factory)
    second = _fingerprint(second_factory)
    comparison = compare_rebuild_fingerprints(first, second)
    assert comparison["all_matched"] is True
    assert all(row["matched"] for row in comparison["checks"].values())
    assert first.security_count_by_month == {
        "2024-01-31": 2,
        "2024-02-29": 1,
    }
    assert len(first.canonical_report_sha256) == 64
    first_engine.dispose()
    second_engine.dispose()


def test_membership_change_and_count_change_fail_reproducibility(tmp_path):
    first_engine, first_factory = _seed_memberships(
        f"sqlite+pysqlite:///{tmp_path / 'first.db'}"
    )
    changed_engine, changed_factory = _seed_memberships(
        f"sqlite+pysqlite:///{tmp_path / 'changed.db'}", changed=True
    )
    first = _fingerprint(first_factory)
    changed = _fingerprint(changed_factory)
    assert first.universe_membership_hash != changed.universe_membership_hash
    changed = replace(changed, prediction_count=11)
    comparison = compare_rebuild_fingerprints(first, changed)
    assert comparison["all_matched"] is False
    assert comparison["checks"]["universe_membership_hash"]["matched"] is False
    assert comparison["checks"]["prediction_count"]["matched"] is False
    first_engine.dispose()
    changed_engine.dispose()


def test_closure_document_records_every_required_match(tmp_path):
    engine, factory = _seed_memberships(
        f"sqlite+pysqlite:///{tmp_path / 'closure.db'}"
    )
    fingerprint = _fingerprint(factory)
    artifact = _artifacts(fingerprint)
    document = build_closure_document(
        first=artifact,
        second=artifact,
        git_commit="b" * 40,
        bundle_manifest_sha256="c" * 64,
        universe_id="sp500-pit-v1",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        experiment_id="pit-closure",
        evidence_timestamp=TIMESTAMP,
    )
    assert document["closure_decision"] == "pass"
    assert document["claims_eligible"] is False
    assert document["reproducibility"]["all_matched"] is True
    assert json.loads(canonical_json_bytes(document))[
        "clean_worktree_verified"
    ] is True

    bad = _artifacts(replace(fingerprint, outcome_count=8))
    with pytest.raises(ValueError, match="outcome_count"):
        build_closure_document(
            first=artifact,
            second=bad,
            git_commit="b" * 40,
            bundle_manifest_sha256="c" * 64,
            universe_id="sp500-pit-v1",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            experiment_id="pit-closure",
            evidence_timestamp=TIMESTAMP,
        )
    engine.dispose()


def test_git_guard_accepts_clean_commit_and_refuses_tracked_or_untracked_changes(
    tmp_path,
):
    _git(tmp_path, "init", "-q")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )
    commit = require_clean_git_worktree(tmp_path)
    assert len(commit) == 40

    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(DirtyWorktreeError, match="dirty Git worktree"):
        require_clean_git_worktree(tmp_path)
    tracked.write_text("clean\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(DirtyWorktreeError, match="untracked.txt"):
        require_clean_git_worktree(tmp_path)


def test_closure_cli_checks_dirty_worktree_before_reading_vendor_data(
    tmp_path, monkeypatch, capsys
):
    _git(tmp_path, "init", "-q")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )
    tracked.write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr(
        closure_pipeline,
        "require_clean_git_worktree",
        lambda: require_clean_git_worktree(tmp_path),
    )
    output = tmp_path / "must-not-exist.json"
    result = closure_pipeline.main(
        [
            str(tmp_path / "missing-vendor-bundle"),
            "--expected-manifest-hash",
            "0" * 64,
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-12-31",
            "--experiment-id",
            "pit-closure",
            "--evidence-timestamp",
            "2026-01-01T00:00:00Z",
            "--closure-json-output",
            str(output),
        ]
    )
    assert result == 2
    assert "dirty Git worktree" in capsys.readouterr().err
    assert not output.exists()
