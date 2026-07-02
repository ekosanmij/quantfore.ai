"""Audit the frozen Tiingo trial panel against real XNYS sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import (
        get_code_revision,
        open_research_database,
        repository_relative_path,
    )
    from ingest_market_prices import DEFAULT_UNIVERSE_FILE, read_universe
except ModuleNotFoundError:  # Imported through the pipelines package.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        get_code_revision,
        open_research_database,
        repository_relative_path,
    )
    from pipelines.ingest_market_prices import (  # type: ignore
        DEFAULT_UNIVERSE_FILE,
        read_universe,
    )

from sqlalchemy import or_, select

from quantfore_research.ingest.market_prices import TIINGO_VENDOR
from quantfore_research.models import Price, Security, SourceSnapshot
from quantfore_research.validation.price_quality import (
    DEFAULT_CALENDAR,
    PriceObservation,
    PricePanelAudit,
    PriceQualityConfig,
    audit_price_panel,
)


DEFAULT_START_DATE = date(2020, 1, 1)
DEFAULT_END_DATE = date(2025, 12, 31)
DEFAULT_OUTPUT = Path(
    "reports/data-audits/us-equity-trial-v0-price-quality.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_trial_panel(
    *,
    session_factory,
    universe,
    start_date: date,
    end_date: date,
    vendor: str,
) -> tuple[dict[str, list[PriceObservation]], list[dict[str, object]]]:
    """Load the exact vendor/date-range snapshot family and route by CIK."""

    panel = {entry.ticker: [] for entry in universe}
    expected_by_ticker = {entry.ticker: entry for entry in universe}
    expected_by_cik = {entry.cik: entry for entry in universe}
    if len(expected_by_cik) != len(universe):
        raise ValueError("universe contains duplicate CIK values")
    date_fragment = f"_{start_date.isoformat()}_{end_date.isoformat()}_page_"

    with session_factory() as session:
        statement = (
            select(Security, Price, SourceSnapshot)
            .join(Price, Price.security_id == Security.security_id)
            .join(
                SourceSnapshot,
                SourceSnapshot.snapshot_id == Price.source_snapshot_id,
            )
            .where(SourceSnapshot.vendor == vendor)
            .where(SourceSnapshot.dataset.contains(date_fragment, autoescape=True))
            .where(
                or_(
                    Security.ticker.in_(expected_by_ticker),
                    Security.cik.in_(expected_by_cik),
                )
            )
            .order_by(Security.ticker, Price.date, SourceSnapshot.retrieved_at)
        )
        records = session.execute(statement).all()

    snapshots: dict[str, dict[str, object]] = {}
    for security, price, snapshot in records:
        expected = expected_by_cik.get(security.cik or "")
        if expected is None:
            expected = expected_by_ticker.get(security.ticker)
        panel_ticker = expected.ticker if expected is not None else security.ticker
        panel.setdefault(panel_ticker, []).append(
            PriceObservation(
                ticker=security.ticker,
                cik=security.cik,
                date=price.date,
                open=price.open,
                high=price.high,
                low=price.low,
                close=price.close,
                adj_open=price.adj_open,
                adj_high=price.adj_high,
                adj_low=price.adj_low,
                adj_close=price.adj_close,
                volume=price.volume,
                adj_volume=price.adj_volume,
                source_snapshot_id=snapshot.snapshot_id,
                retrieved_at=snapshot.retrieved_at,
            )
        )
        snapshots[snapshot.snapshot_id] = {
            "snapshot_id": snapshot.snapshot_id,
            "vendor": snapshot.vendor,
            "dataset": snapshot.dataset,
            "retrieved_at": _utc_iso(snapshot.retrieved_at),
            "license_tag": snapshot.license_tag,
            "sha256": snapshot.source_hash,
            "storage_uri": snapshot.storage_uri,
        }
    return panel, [snapshots[key] for key in sorted(snapshots)]


def build_audit_document(
    *,
    audit: PricePanelAudit,
    universe_file: Path,
    vendor: str,
    source_snapshots: Sequence[dict[str, object]],
    config: PriceQualityConfig,
    generated_at: datetime,
) -> dict[str, object]:
    return {
        "audit_id": "us-equity-trial-v0-price-quality",
        "dataset_kind": "prototype_real",
        "claims_eligible": False,
        "generated_at": _utc_iso(generated_at),
        "code_revision": get_code_revision(),
        "universe_file": repository_relative_path(universe_file),
        "universe_file_sha256": sha256_file(universe_file),
        "vendor": vendor,
        "source_snapshot_count": len(source_snapshots),
        "source_snapshots": list(source_snapshots),
        "config": {
            "minimum_history_sessions": config.minimum_history_sessions,
            "stale_run_sessions": config.stale_run_sessions,
            "extreme_return_threshold": str(config.extreme_return_threshold),
            "split_raw_return_threshold": str(
                config.split_raw_return_threshold
            ),
            "split_adjusted_return_tolerance": str(
                config.split_adjusted_return_tolerance
            ),
        },
        "audit": audit.to_dict(),
    }


def write_json_atomic(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit the frozen real-market price panel."
    )
    parser.add_argument(
        "--universe-file", type=Path, default=DEFAULT_UNIVERSE_FILE
    )
    parser.add_argument(
        "--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE
    )
    parser.add_argument(
        "--end-date", type=date.fromisoformat, default=DEFAULT_END_DATE
    )
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--calendar", default=DEFAULT_CALENDAR)
    parser.add_argument("--vendor", default=TIINGO_VENDOR)
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--minimum-history-sessions", type=int, default=1250)
    parser.add_argument("--stale-run-sessions", type=int, default=5)
    parser.add_argument(
        "--extreme-return-threshold", type=Decimal, default=Decimal("0.30")
    )
    parser.add_argument(
        "--split-raw-return-threshold", type=Decimal, default=Decimal("0.35")
    )
    parser.add_argument(
        "--split-adjusted-return-tolerance",
        type=Decimal,
        default=Decimal("0.10"),
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Return exit status 1 when the completed audit is not a pass.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.start_date > args.end_date:
        raise ValueError("start-date must not be after end-date")
    universe = read_universe(args.universe_file)
    config = PriceQualityConfig(
        minimum_history_sessions=args.minimum_history_sessions,
        stale_run_sessions=args.stale_run_sessions,
        extreme_return_threshold=args.extreme_return_threshold,
        split_raw_return_threshold=args.split_raw_return_threshold,
        split_adjusted_return_tolerance=args.split_adjusted_return_tolerance,
    )
    session_factory = open_research_database(args.database_url)
    panel, source_snapshots = load_trial_panel(
        session_factory=session_factory,
        universe=universe,
        start_date=args.start_date,
        end_date=args.end_date,
        vendor=args.vendor,
    )
    audit = audit_price_panel(
        panel,
        expected_tickers=[entry.ticker for entry in universe],
        expected_ciks={entry.ticker: entry.cik for entry in universe},
        start_date=args.start_date,
        end_date=args.end_date,
        benchmark=args.benchmark,
        calendar_name=args.calendar,
        config=config,
    )
    document = build_audit_document(
        audit=audit,
        universe_file=args.universe_file,
        vendor=args.vendor,
        source_snapshots=source_snapshots,
        config=config,
        generated_at=datetime.now(timezone.utc),
    )
    write_json_atomic(args.output, document)
    failed = sum(item.status == "fail" for item in audit.securities)
    review = sum(item.status == "review" for item in audit.securities)
    print(
        f"price audit status={audit.status} securities={len(audit.securities)} "
        f"failed={failed} review={review} output={args.output}"
    )
    return 1 if args.require_pass and not audit.audit_passed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f"price-panel audit failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
